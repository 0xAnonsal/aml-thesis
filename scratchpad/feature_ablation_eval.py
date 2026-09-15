"""Feature ablation (§8.9.50) — which of the 19 fingerprint dims matter?

Ranks features by RandomForest importance on the binary attacker/benign
label, then re-runs the P1-71+P1-73 pipeline with top-K features only
(others zeroed out). Compares ARI across K ∈ {5, 10, 19}.

Purpose: if a smaller subset gives ≈same ARI → simpler, more defensible
pipeline. If ARI collapses at K=5 → confirms all 19 features carry signal.

Usage: python3 feature_ablation_eval.py <dataset> [model=haiku]
"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, '/home/anon/aml-thesis')
sys.path.insert(0, '/home/anon/aml-thesis/src')

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import adjusted_rand_score, f1_score, precision_score, recall_score
from aml.detectors.baselines import LouvainDetector, derive_binary_labels, PerExchangeDetector
from aml.detectors.dataset import combine_runs, partial_visibility_split
from aml.detectors.multi_agent import (
    _LLM_COORDINATOR_SYSTEM_PROMPT, _parse_llm_clusters,
    _merge_clusters_by_centroid, _auto_pick_max_clusters,
)
from aml.detectors.gnn import extract_features, FEATURE_NAMES
from aml.attackers.llm_client import LLMClient
import httpx

REPO = Path('/home/anon/aml-thesis')
BENIGN_ROOT = REPO / 'results' / 'benign_corpus_v57'
SEED = 42

DATASETS = {
    'sepolia_800': REPO / 'results/sepolia_campaign/2026-09-05T01-47-06_defi-exploit_seed800_sepolia',
    'sepolia_802': REPO / 'results/sepolia_campaign/2026-09-06T01-28-20_defi-exploit_seed802_sepolia',
    'sepolia_803': REPO / 'results/sepolia_campaign/2026-09-08T18-17-57_defi-exploit_seed803_sepolia',
    'anvil_830':   REPO / 'results/anvil_option_a/2026-09-09T17-18-02_defi-exploit_seed830',
    'anvil_850':   REPO / 'results/anvil_option_a/2026-09-09T20-55-17_ransomware-cashout_seed850',
}

label = sys.argv[1] if len(sys.argv) > 1 else 'sepolia_803'
model = sys.argv[2] if len(sys.argv) > 2 else 'haiku'

attacker_dir = DATASETS[label]
benign_dirs = sorted(BENIGN_ROOT.iterdir())

print(f'=== FEATURE ABLATION eval: {label} ===')
combined = combine_runs([attacker_dir] + benign_dirs)
g = combined.graph
bin_labels = derive_binary_labels(combined.node_labels)

# Extract features for ALL labeled addresses, build ranked importance
print('\nRanking features by RF importance...')
all_addrs = list(bin_labels.keys())
X = extract_features(g, all_addrs)
y = np.array([bin_labels[a] for a in all_addrs])
rf = RandomForestClassifier(n_estimators=100, random_state=SEED, n_jobs=-1)
rf.fit(X, y)
importances = rf.feature_importances_
ranked = sorted(zip(FEATURE_NAMES, importances), key=lambda kv: -kv[1])
print('Feature importance (top→bottom):')
for name, imp in ranked:
    print(f'  {name:22s}  {imp:.4f}')

def _run_pipeline(mask: np.ndarray, tag: str):
    """Run Louvain+LLM+P1-71+silhouette with feature mask applied."""
    print(f'\n--- ABLATION: {tag} (kept {int(mask.sum())}/19 features) ---')
    views = partial_visibility_split(combined, num_exchanges=3, seed=SEED)
    lou = PerExchangeDetector(detector_factory=lambda: LouvainDetector()).fit_per_view(views, bin_labels)

    per_exchange = {}; all_flagged = set(); addr_to_fp = {}
    for view in views:
        visible = list(view.visible_addresses)
        sub = lou.detectors[view.name]
        predictions = sub.predict(visible)
        probs = sub.predict_proba(visible) if hasattr(sub, 'predict_proba') else [None]*len(visible)
        base = extract_features(view.visible_subgraph, visible)
        # Apply mask: zero out non-kept features
        base_masked = base * mask
        entries = []
        for i, (addr, pred) in enumerate(zip(visible, predictions)):
            if pred != 1: continue
            fp = base_masked[i]; conf = probs[i] if probs[i] is not None else 1.0
            entries.append((addr, fp, float(conf)))
            all_flagged.add(addr); addr_to_fp[addr] = fp
        entries.sort(key=lambda x: -x[2])
        per_exchange[view.name] = entries[:60]

    lines = []
    for ex, entries in per_exchange.items():
        lines.append(f"\n=== Exchange {ex} — {len(entries)} flagged ===")
        for a, fp, c in entries:
            fs = ", ".join(f"{n}={fp[i]:.2f}" for i, n in enumerate(FEATURE_NAMES) if mask[i] and abs(fp[i]) > 0.01)
            lines.append(f"  ({ex}) {a}  conf={c:.3f}\n    features: {fs}")

    user_prompt = f"Cross-exchange batch (ABLATION: {tag}).\n" + "\n".join(lines)
    llm_client = LLMClient(timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0), max_retries=5)
    t0 = time.time()
    result = llm_client.complete(prompt=user_prompt, system=_LLM_COORDINATOR_SYSTEM_PROMPT,
                                  model=model, max_tokens=8192)
    llm_time = time.time() - t0
    cost = getattr(result, 'cost_usd', 0)
    print(f'  LLM: {llm_time:.1f}s cost=${cost:.4f}')

    text = result.text if hasattr(result, 'text') else str(result)
    address_to_cluster, _ = _parse_llm_clusters(text, all_flagged)

    true_labels_map = {a: l for a, l in combined.node_labels.items() if 'attacker_' in l}
    def _ari(cm):
        common = [a for a in cm if a in true_labels_map]
        if len(common) < 3: return None
        return adjusted_rand_score([true_labels_map[a] for a in common], [cm[a] for a in common])

    baseline_ari = _ari(address_to_cluster)
    print(f'  Baseline ARI: {baseline_ari:.4f}')

    merged_3 = _merge_clusters_by_centroid(address_to_cluster, addr_to_fp, 3)
    ari_3 = _ari(merged_3)
    print(f'  max_c=3 ARI: {ari_3:.4f}')

    auto_k = _auto_pick_max_clusters(address_to_cluster, addr_to_fp, [2, 3, 5, 8, 12])
    auto_merged = _merge_clusters_by_centroid(address_to_cluster, addr_to_fp, auto_k)
    ari_auto = _ari(auto_merged)
    print(f'  Silhouette k={auto_k}: ARI={ari_auto:.4f}')

    return {
        'features_kept': int(mask.sum()),
        'llm_cost_usd': round(cost, 4),
        'llm_fit_time_s': round(llm_time, 2),
        'baseline_ari': round(baseline_ari, 4) if baseline_ari is not None else None,
        'max_c_3_ari': round(ari_3, 4) if ari_3 is not None else None,
        'silhouette_k': auto_k,
        'silhouette_ari': round(ari_auto, 4) if ari_auto is not None else None,
    }

# Run ablations
results = {}
name_to_idx = {n: i for i, n in enumerate(FEATURE_NAMES)}
top_names = [n for n, _ in ranked]

# All 19
mask_19 = np.ones(19)
results['all_19'] = {'top_features': list(FEATURE_NAMES), **_run_pipeline(mask_19, 'ALL_19')}

# Top-10
mask_10 = np.zeros(19)
for n in top_names[:10]: mask_10[name_to_idx[n]] = 1
results['top_10'] = {'top_features': top_names[:10], **_run_pipeline(mask_10, 'TOP_10')}

# Top-5
mask_5 = np.zeros(19)
for n in top_names[:5]: mask_5[name_to_idx[n]] = 1
results['top_5'] = {'top_features': top_names[:5], **_run_pipeline(mask_5, 'TOP_5')}

# Save
out = REPO / 'results' / f'feature_ablation_{label}_{model}.json'
out.write_text(json.dumps({
    'label': label, 'model': model,
    'feature_importance_ranked': [(n, round(imp, 4)) for n, imp in ranked],
    'ablations': results,
}, indent=2))
print(f'\nWrote {out}')

# Summary
print('\n=== SUMMARY ===')
for k in ('all_19', 'top_10', 'top_5'):
    r = results[k]
    print(f'  {k:8s}: silhouette ARI={r["silhouette_ari"]:.4f}, max_c=3 ARI={r["max_c_3_ari"]:.4f}, cost=${r["llm_cost_usd"]:.4f}')
