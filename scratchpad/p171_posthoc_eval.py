"""P1-71 post-hoc cluster merge — deterministic enforcement of max cluster count.

Runs LLM Phase 2 once, then merges predicted clusters by centroid distance
until |clusters| <= max_clusters. Sweeps max_clusters ∈ {3, 5, 8, 12}.

Usage: python3 p171_posthoc.py <dataset_label> [model=haiku]
"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, '/home/anon/aml-thesis')
sys.path.insert(0, '/home/anon/aml-thesis/src')

import networkx as nx
import numpy as np
from sklearn.metrics import adjusted_rand_score
from aml.detectors.baselines import LouvainDetector, derive_binary_labels, PerExchangeDetector
from aml.detectors.dataset import combine_runs, partial_visibility_split
from aml.detectors.multi_agent import (
    LLMDefenderCoordinator, _LLM_COORDINATOR_SYSTEM_PROMPT,
    _build_llm_user_prompt, _format_address_for_llm, _parse_llm_clusters,
)
from aml.detectors.gnn import extract_features, FEATURE_NAMES, FEATURE_DIM
from aml.attackers.llm_client import LLMClient
import httpx

REPO = Path('/home/anon/aml-thesis')
BENIGN_ROOT = REPO / 'results' / 'benign_corpus_v57'
SEED = 42
SWEEP = [3, 5, 8, 12, 20]

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

print(f'=== P1-71 POST-HOC MERGE eval: {label} ===')

combined = combine_runs([attacker_dir] + benign_dirs)
g = combined.graph
bin_labels = derive_binary_labels(combined.node_labels)
attacker_addrs = {a for a, y in bin_labels.items() if y == 1}
print(f'  nodes={g.number_of_nodes():,} edges={g.number_of_edges():,}')

# Split + Louvain Phase 1
views = partial_visibility_split(combined, num_exchanges=3, seed=SEED)
lou = PerExchangeDetector(detector_factory=lambda: LouvainDetector()).fit_per_view(
    views, bin_labels,
)

# Collect flagged with 19-dim fingerprints
per_exchange_flagged: dict[str, list[tuple]] = {}
all_flagged: set[str] = set()
addr_to_fp: dict[str, np.ndarray] = {}

for view in views:
    visible = list(view.visible_addresses)
    sub = lou.detectors[view.name]
    predictions = sub.predict(visible)
    probs = sub.predict_proba(visible) if hasattr(sub, 'predict_proba') else [None]*len(visible)
    base_features = extract_features(view.visible_subgraph, visible)
    entries = []
    for i, (addr, pred) in enumerate(zip(visible, predictions)):
        if pred != 1:
            continue
        fp = base_features[i]
        conf = probs[i] if probs[i] is not None else 1.0
        entries.append((addr, fp, float(conf)))
        all_flagged.add(addr)
        # Store max-conf fingerprint if seen on multiple exchanges
        if addr not in addr_to_fp or conf > 0:
            addr_to_fp[addr] = fp
    entries.sort(key=lambda x: -x[2])
    per_exchange_flagged[view.name] = entries[:60]

print(f'  flagged unique: {len(all_flagged)}')

# Build prompt (19-dim, no extended features — pure post-hoc test)
def _fmt(addr, ex, fp, conf):
    features_str = ", ".join(
        f"{name}={fp[i]:.2f}"
        for i, name in enumerate(FEATURE_NAMES)
        if abs(fp[i]) > 0.01
    )
    return f"  ({ex}) {addr}  conf={conf:.3f}\n    features: {features_str}"

lines = []
for ex, entries in per_exchange_flagged.items():
    lines.append(f"\n=== Exchange {ex} — {len(entries)} flagged ===")
    for a, fp, c in entries:
        lines.append(_fmt(a, ex, fp, c))

user_prompt = "Cross-exchange federation batch. 19-dim fingerprints per flagged address.\n" + "\n".join(lines)

# Single LLM call
llm_client = LLMClient(
    timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0),
    max_retries=5,
)
print(f'\nCalling {model}...')
t0 = time.time()
result = llm_client.complete(
    prompt=user_prompt, system=_LLM_COORDINATOR_SYSTEM_PROMPT,
    model=model, max_tokens=8192,
)
llm_time = time.time() - t0
llm_text = result.text if hasattr(result, 'text') else str(result)
cost = getattr(result, 'cost_usd', 0)
print(f'  fit={llm_time:.1f}s cost=${cost:.4f}')

# Parse assignments
address_to_cluster, reasoning = _parse_llm_clusters(llm_text, all_flagged)
n_baseline = len(set(address_to_cluster.values()))
print(f'  baseline clusters: {n_baseline}, clustered addrs: {len(address_to_cluster)}')

# True clusters (attacker campaigns only — same as feature_eng eval)
true_labels: dict[str, str] = {}
for a, l in combined.node_labels.items():
    if 'attacker_' in l:
        true_labels[a] = l
common = [a for a in address_to_cluster if a in true_labels]

def _ari(cluster_map: dict[str, int]) -> float | None:
    common_local = [a for a in cluster_map if a in true_labels]
    if len(common_local) < 3:
        return None
    t = [true_labels[a] for a in common_local]
    p = [cluster_map[a] for a in common_local]
    return adjusted_rand_score(t, p)

baseline_ari = _ari(address_to_cluster)
print(f'\n  baseline ARI: {baseline_ari:.4f}  (true clusters: {len(set(true_labels[a] for a in common))}, common addrs: {len(common)})')

# ============== P1-71 POST-HOC MERGE ==============
# Compute per-cluster centroid from address fingerprints
def _centroids(cluster_map: dict[str, int]) -> dict[int, np.ndarray]:
    groups: dict[int, list[np.ndarray]] = {}
    for a, c in cluster_map.items():
        if a in addr_to_fp:
            groups.setdefault(c, []).append(addr_to_fp[a])
    return {c: np.mean(fps, axis=0) for c, fps in groups.items() if fps}

def _merge_to(cluster_map: dict[str, int], max_c: int) -> dict[str, int]:
    """Iteratively merge two closest clusters (by centroid L2 distance)
    until |clusters| <= max_c."""
    result = dict(cluster_map)
    cents = _centroids(result)
    while len(cents) > max_c:
        # find closest pair
        cluster_ids = list(cents.keys())
        best = (None, None, float('inf'))
        for i, ci in enumerate(cluster_ids):
            for cj in cluster_ids[i+1:]:
                d = float(np.linalg.norm(cents[ci] - cents[cj]))
                if d < best[2]:
                    best = (ci, cj, d)
        ci, cj, _ = best
        # merge cj into ci
        for a in list(result.keys()):
            if result[a] == cj:
                result[a] = ci
        cents = _centroids(result)
    return result

sweep_results = {'baseline': {'max_c': n_baseline, 'ari': baseline_ari}}
for max_c in SWEEP:
    if max_c >= n_baseline:
        sweep_results[f'max_{max_c}'] = {'max_c': n_baseline, 'ari': baseline_ari, 'note': 'no merge needed'}
        continue
    merged = _merge_to(address_to_cluster, max_c)
    n_after = len(set(merged.values()))
    ari = _ari(merged)
    sweep_results[f'max_{max_c}'] = {'max_c': n_after, 'ari': ari}
    print(f'  merge → max_c={max_c}: got {n_after} clusters, ARI={ari:.4f}')

# Find best
best_key = max(sweep_results.keys(), key=lambda k: sweep_results[k]['ari'] or -1)
best = sweep_results[best_key]
print(f'\n  BEST: {best_key} → ARI={best["ari"]:.4f} ({best["max_c"]} clusters)')
delta = (best['ari'] - baseline_ari) if baseline_ari is not None else None
print(f'  Δ vs baseline: {delta:+.4f}')

# Save
out = REPO / 'results' / f'p171_posthoc_{label}_{model}.json'
out.write_text(json.dumps({
    'label': label, 'model': model,
    'nodes': g.number_of_nodes(), 'edges': g.number_of_edges(),
    'n_flagged': len(all_flagged),
    'n_common_addrs': len(common),
    'true_n_clusters': len(set(true_labels[a] for a in common)) if common else 0,
    'llm': {
        'cost_usd': round(cost, 4),
        'fit_time_s': round(llm_time, 2),
    },
    'sweep': {k: {**v, 'ari': round(v['ari'], 4) if v['ari'] is not None else None} for k, v in sweep_results.items()},
    'best': best_key,
    'delta_vs_baseline': round(delta, 4) if delta is not None else None,
    'reasoning_preview': (reasoning or '')[:400],
}, indent=2))
print(f'\nWrote {out}')
