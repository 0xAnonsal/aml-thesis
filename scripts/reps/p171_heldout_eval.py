"""Held-out validation of P1-71 defender on seeds 900/901 + benign v58.

Attackers seed 900 (defi-exploit) and 901 (ransomware-cashout) never seen
during development. Benign corpus v58 (seeds 400-404) never used.
Compare F1 / ARI vs. reported numbers (§8.9.36-45) to detect leakage.

Usage: python3 p171_heldout_eval.py <label> [model=haiku]
   label ∈ {'anvil_900', 'anvil_901'}
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
    LLMDefenderCoordinator, _LLM_COORDINATOR_SYSTEM_PROMPT,
    _build_llm_user_prompt, _format_address_for_llm, _parse_llm_clusters,
    _merge_clusters_by_centroid, _auto_pick_max_clusters,
)
from aml.detectors.gnn import extract_features, FEATURE_NAMES
from aml.attackers.llm_client import LLMClient
import httpx

REPO = Path('/home/anon/aml-thesis')
BENIGN_ROOT = REPO / 'results' / 'benign_corpus_v58'
SEED = 42
SWEEP = [3, 5, 8, 12]

# Find attacker dirs by pattern (the timestamp is unpredictable)
def _find_attacker(scenario: str, seed: int) -> Path:
    for p in (REPO / 'results/anvil_option_a').iterdir():
        if p.is_dir() and f'{scenario}_seed{seed}' in p.name:
            # skip empty dirs
            if (p / 'chain_trace.jsonl').exists():
                return p
    raise FileNotFoundError(f'No completed run for {scenario} seed={seed}')

DATASETS = {
    'anvil_900': ('defi-exploit', 900),
    'anvil_901': ('ransomware-cashout', 901),
}

label = sys.argv[1] if len(sys.argv) > 1 else 'anvil_900'
model = sys.argv[2] if len(sys.argv) > 2 else 'haiku'

scenario, seed = DATASETS[label]
attacker_dir = _find_attacker(scenario, seed)
benign_dirs = sorted(BENIGN_ROOT.iterdir())

print(f'=== HELD-OUT eval: {label} ({scenario} seed={seed}) ===')
print(f'  attacker: {attacker_dir.name}')
print(f'  benign corpus v58: {len(benign_dirs)} seeds')

combined = combine_runs([attacker_dir] + benign_dirs)
g = combined.graph
bin_labels = derive_binary_labels(combined.node_labels)
attacker_addrs = {a for a, y in bin_labels.items() if y == 1}
print(f'  nodes={g.number_of_nodes():,} edges={g.number_of_edges():,}')
print(f'  attackers={len(attacker_addrs)}')

# ============ PHASE 1: F1 evaluation (Louvain) ============
views = partial_visibility_split(combined, num_exchanges=3, seed=SEED)
lou = PerExchangeDetector(detector_factory=lambda: LouvainDetector()).fit_per_view(
    views, bin_labels,
)

# Aggregate predictions across views
all_addrs = set()
for view in views:
    all_addrs.update(view.visible_addresses)
addr_pred: dict[str, int] = {}
for view in views:
    sub = lou.detectors[view.name]
    preds = sub.predict(list(view.visible_addresses))
    for a, p in zip(list(view.visible_addresses), preds):
        # Union: flagged if ANY exchange flags it
        if p == 1 or addr_pred.get(a, 0) == 1:
            addr_pred[a] = 1
        else:
            addr_pred[a] = addr_pred.get(a, 0)

# Compute F1 on labeled addresses
labeled_addrs = list(bin_labels.keys())
y_true = np.array([bin_labels[a] for a in labeled_addrs])
y_pred = np.array([addr_pred.get(a, 0) for a in labeled_addrs])
f1 = f1_score(y_true, y_pred)
prec = precision_score(y_true, y_pred) if y_pred.sum() > 0 else 0.0
rec = recall_score(y_true, y_pred)
print(f'\n  Louvain Phase 1: F1={f1:.4f}  P={prec:.4f}  R={rec:.4f}')

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
        if pred != 1:
            continue
        fp = base_features[i]
        conf = probs[i] if probs[i] is not None else 1.0
        entries.append((addr, fp, float(conf)))
        all_flagged.add(addr)
        addr_to_fp[addr] = fp
    entries.sort(key=lambda x: -x[2])
    per_exchange_flagged[view.name] = entries[:60]

print(f'  flagged unique: {len(all_flagged)}')

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

user_prompt = "Cross-exchange federation batch.\n" + "\n".join(lines)

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

# True clusters (attacker campaigns only)
true_labels_map: dict[str, str] = {}
for a, l in combined.node_labels.items():
    if 'attacker_' in l:
        true_labels_map[a] = l
common = [a for a in address_to_cluster if a in true_labels_map]

def _ari(cluster_map):
    common_local = [a for a in cluster_map if a in true_labels_map]
    if len(common_local) < 3:
        return None
    t = [true_labels_map[a] for a in common_local]
    p = [cluster_map[a] for a in common_local]
    return adjusted_rand_score(t, p)

baseline_ari = _ari(address_to_cluster)
print(f'\n  baseline ARI: {baseline_ari:.4f}  (true={len(set(true_labels_map[a] for a in common))} common_addrs={len(common)})')

# Sweep
sweep = {'baseline': {'max_c': n_baseline, 'ari': baseline_ari}}
for max_c in SWEEP:
    if max_c >= n_baseline:
        sweep[f'max_{max_c}'] = {'max_c': n_baseline, 'ari': baseline_ari, 'note': 'no merge'}
        continue
    merged = _merge_clusters_by_centroid(address_to_cluster, addr_to_fp, max_c)
    a = _ari(merged)
    n = len(set(merged.values()))
    sweep[f'max_{max_c}'] = {'max_c': n, 'ari': a}
    print(f'  merge → max_c={max_c}: got {n} clusters, ARI={a:.4f}')

# Silhouette auto-tune
auto_k = _auto_pick_max_clusters(address_to_cluster, addr_to_fp, [2, 3, 5, 8, 12])
auto_merged = _merge_clusters_by_centroid(address_to_cluster, addr_to_fp, auto_k)
auto_ari = _ari(auto_merged)
n_auto = len(set(auto_merged.values()))
print(f'\n  SILHOUETTE k={auto_k} → {n_auto} clusters, ARI={auto_ari:.4f}')

# Save
out = REPO / 'results' / 'reps' / f'rep{__import__("os").environ.get("REP","2")}_p171_heldout_{label}_{model}.json'
out.write_text(json.dumps({
    'label': label, 'scenario': scenario, 'seed': seed, 'model': model,
    'attacker_dir': attacker_dir.name,
    'nodes': g.number_of_nodes(), 'edges': g.number_of_edges(),
    'n_attacker': len(attacker_addrs),
    'phase1': {
        'louvain_f1': round(f1, 4),
        'precision': round(prec, 4),
        'recall': round(rec, 4),
    },
    'phase2': {
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
