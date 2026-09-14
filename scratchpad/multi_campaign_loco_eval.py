"""Multi-campaign LOCO evaluation — 7 attacker datasets + 1 benign corpus.

Tests whether the defender pipeline scales when it sees MULTIPLE
attacker campaigns simultaneously (7 × ~3 actors = ~21 true clusters).
If ARI/F1 collapses vs. per-campaign eval → simplification was hiding
weakness. If they hold → pipeline generalizes to real streaming
multi-campaign scenarios.

Usage: python3 multi_campaign_loco_eval.py [model=haiku]
"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, '/home/anon/aml-thesis')
sys.path.insert(0, '/home/anon/aml-thesis/src')

import networkx as nx
import numpy as np
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
SEED = 42
SWEEP = [3, 5, 8, 12, 20, 30]

# All 7 attacker campaigns
ATTACKERS = [
    REPO / 'results/sepolia_campaign/2026-09-05T01-47-06_defi-exploit_seed800_sepolia',
    REPO / 'results/sepolia_campaign/2026-09-06T01-28-20_defi-exploit_seed802_sepolia',
    REPO / 'results/sepolia_campaign/2026-09-08T18-17-57_defi-exploit_seed803_sepolia',
    REPO / 'results/anvil_option_a/2026-09-09T17-18-02_defi-exploit_seed830',
    REPO / 'results/anvil_option_a/2026-09-09T20-55-17_ransomware-cashout_seed850',
]
# Held-out (find by glob)
for p in sorted((REPO / 'results/anvil_option_a').iterdir()):
    if p.is_dir() and (p / 'chain_trace.jsonl').exists() and ('seed900' in p.name or 'seed901' in p.name):
        ATTACKERS.append(p)

BENIGN_ROOT = REPO / 'results' / 'benign_corpus_v57'
benign_dirs = sorted(BENIGN_ROOT.iterdir())

model = sys.argv[1] if len(sys.argv) > 1 else 'haiku'

print(f'=== MULTI-CAMPAIGN LOCO eval: {len(ATTACKERS)} attackers × 1 combined graph ===')
for a in ATTACKERS: print(f'  · {a.name}')

# Combine everything into ONE graph
combined = combine_runs(list(ATTACKERS) + benign_dirs)
g = combined.graph
bin_labels = derive_binary_labels(combined.node_labels)
attacker_addrs = {a for a, y in bin_labels.items() if y == 1}
print(f'\n  Combined graph: {g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges')
print(f'  Total attackers: {len(attacker_addrs)}')

# True actor clusters — join by attacker_ prefix + run source
true_labels_map: dict[str, str] = {}
for a, l in combined.node_labels.items():
    if 'attacker_' in l:
        true_labels_map[a] = l
n_true_actors = len(set(true_labels_map.values()))
print(f'  True actor clusters (across all 7 campaigns): {n_true_actors}')

# 3-exchange partial visibility
views = partial_visibility_split(combined, num_exchanges=3, seed=SEED)

# ============ PHASE 1: F1 ============
lou = PerExchangeDetector(detector_factory=lambda: LouvainDetector()).fit_per_view(
    views, bin_labels,
)
addr_pred: dict[str, int] = {}
for view in views:
    sub = lou.detectors[view.name]
    preds = sub.predict(list(view.visible_addresses))
    for a, p in zip(list(view.visible_addresses), preds):
        if p == 1 or addr_pred.get(a, 0) == 1:
            addr_pred[a] = 1
        else:
            addr_pred[a] = addr_pred.get(a, 0)

labeled_addrs = list(bin_labels.keys())
y_true = np.array([bin_labels[a] for a in labeled_addrs])
y_pred = np.array([addr_pred.get(a, 0) for a in labeled_addrs])
f1 = f1_score(y_true, y_pred)
prec = precision_score(y_true, y_pred) if y_pred.sum() > 0 else 0.0
rec = recall_score(y_true, y_pred)
print(f'\n  Phase 1 Louvain F1={f1:.4f}  P={prec:.4f}  R={rec:.4f}')

# ============ PHASE 2: LLM + P1-71 + P1-73 ============
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
        if pred != 1: continue
        fp = base_features[i]
        conf = probs[i] if probs[i] is not None else 1.0
        entries.append((addr, fp, float(conf)))
        all_flagged.add(addr)
        addr_to_fp[addr] = fp
    entries.sort(key=lambda x: -x[2])
    per_exchange_flagged[view.name] = entries[:60]

print(f'  flagged unique: {len(all_flagged)}')

def _fmt(addr, ex, fp, conf):
    fs = ", ".join(f"{n}={fp[i]:.2f}" for i, n in enumerate(FEATURE_NAMES) if abs(fp[i]) > 0.01)
    return f"  ({ex}) {addr}  conf={conf:.3f}\n    features: {fs}"

lines = []
for ex, entries in per_exchange_flagged.items():
    lines.append(f"\n=== Exchange {ex} — {len(entries)} flagged ===")
    for a, fp, c in entries:
        lines.append(_fmt(a, ex, fp, c))

user_prompt = ("Cross-exchange federation batch — this batch contains flags from MULTIPLE distinct "
               "actor campaigns (real streaming scenario). Cluster the flagged addresses into "
               "distinct actor operations.\n") + "\n".join(lines)

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

address_to_cluster, reasoning = _parse_llm_clusters(llm_text, all_flagged)
n_baseline = len(set(address_to_cluster.values()))
print(f'  baseline clusters: {n_baseline}, clustered addrs: {len(address_to_cluster)}')

common = [a for a in address_to_cluster if a in true_labels_map]
def _ari(cluster_map):
    common_local = [a for a in cluster_map if a in true_labels_map]
    if len(common_local) < 3: return None
    t = [true_labels_map[a] for a in common_local]
    p = [cluster_map[a] for a in common_local]
    return adjusted_rand_score(t, p)

baseline_ari = _ari(address_to_cluster)
print(f'\n  baseline ARI: {baseline_ari:.4f}  (true actors: {n_true_actors}, common addrs: {len(common)})')

# P1-71 sweep (wider range for multi-campaign)
sweep = {'baseline': {'max_c': n_baseline, 'ari': baseline_ari}}
for max_c in SWEEP:
    if max_c >= n_baseline:
        sweep[f'max_{max_c}'] = {'max_c': n_baseline, 'ari': baseline_ari, 'note': 'no merge'}
        continue
    merged = _merge_clusters_by_centroid(address_to_cluster, addr_to_fp, max_c)
    a = _ari(merged); n = len(set(merged.values()))
    sweep[f'max_{max_c}'] = {'max_c': n, 'ari': a}
    print(f'  merge → max_c={max_c}: got {n} clusters, ARI={a:.4f}')

# Silhouette auto-tune with wider range
auto_k = _auto_pick_max_clusters(address_to_cluster, addr_to_fp, [2, 3, 5, 8, 12, 20, 30])
auto_merged = _merge_clusters_by_centroid(address_to_cluster, addr_to_fp, auto_k)
auto_ari = _ari(auto_merged); n_auto = len(set(auto_merged.values()))
print(f'\n  SILHOUETTE k={auto_k} → {n_auto} clusters, ARI={auto_ari:.4f}')

# Save
out = REPO / 'results' / f'multi_campaign_loco_{model}.json'
out.write_text(json.dumps({
    'n_attackers_combined': len(ATTACKERS),
    'n_nodes': g.number_of_nodes(), 'n_edges': g.number_of_edges(),
    'n_attacker_addrs': len(attacker_addrs),
    'n_true_actor_clusters': n_true_actors,
    'phase1': {
        'louvain_f1': round(f1, 4),
        'precision': round(prec, 4),
        'recall': round(rec, 4),
    },
    'phase2': {
        'llm_model': model,
        'llm_cost_usd': round(cost, 4),
        'llm_fit_time_s': round(llm_time, 2),
        'n_flagged': len(all_flagged),
        'baseline_ari': round(baseline_ari, 4) if baseline_ari is not None else None,
        'sweep': {k: {**v, 'ari': round(v['ari'], 4) if v['ari'] is not None else None}
                  for k, v in sweep.items()},
        'silhouette': {
            'picked_k': auto_k,
            'n_clusters_after': n_auto,
            'ari': round(auto_ari, 4) if auto_ari is not None else None,
        },
    },
}, indent=2))
print(f'\nWrote {out}')
