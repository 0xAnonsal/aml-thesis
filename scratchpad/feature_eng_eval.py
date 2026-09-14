"""Feature engineering ablation — extend LLM prompt fingerprint with
3 graph-native features: pagerank, betweenness_centrality (approx),
local_clustering_coefficient.

Compares against §8.9.37 baseline (19-dim features).

Usage: python3 feature_eng_eval.py <dataset_label> [model=haiku]
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

print(f'=== FEATURE ENGINEERING eval: {label} + extended features ===')

combined = combine_runs([attacker_dir] + benign_dirs)
g = combined.graph
bin_labels = derive_binary_labels(combined.node_labels)
attacker_addrs = {a for a, y in bin_labels.items() if y == 1}
print(f'  nodes={g.number_of_nodes():,} edges={g.number_of_edges():,}')
print(f'  attackers={len(attacker_addrs)}')

# Compute 3 graph-native features on the FULL combined graph
print('\nComputing graph-native features...')
t0 = time.time()
# Convert to simple DiGraph (nx methods want simple graphs, we have MultiDi)
if isinstance(g, nx.MultiDiGraph):
    g_simple = nx.DiGraph()
    for u, v in g.edges():
        g_simple.add_edge(u, v)
else:
    g_simple = g

pagerank = nx.pagerank(g_simple, alpha=0.85, max_iter=100, tol=1e-4)
print(f'  pagerank: {time.time()-t0:.1f}s')

# Betweenness: approximate with k=200 sample (much faster than exact)
t0 = time.time()
if g_simple.number_of_nodes() > 200:
    betweenness = nx.betweenness_centrality(g_simple, k=200, seed=SEED, normalized=True)
else:
    betweenness = nx.betweenness_centrality(g_simple, normalized=True)
print(f'  betweenness (k=200): {time.time()-t0:.1f}s')

t0 = time.time()
clustering = nx.clustering(g_simple.to_undirected())
print(f'  clustering: {time.time()-t0:.1f}s')

# Extend fingerprints — compute base 19-dim first, then append 3 graph features
views = partial_visibility_split(combined, num_exchanges=3, seed=SEED)

# Fit Louvain phase 1
lou = PerExchangeDetector(detector_factory=lambda: LouvainDetector()).fit_per_view(
    views, bin_labels,
)

# Collect per-exchange flagged with EXTENDED fingerprints (19 + 3 = 22 dim)
per_exchange_flagged: dict[str, list[tuple]] = {}
all_flagged: set[str] = set()

for view in views:
    visible = list(view.visible_addresses)
    sub = lou.detectors[view.name]
    predictions = sub.predict(visible)
    probs = sub.predict_proba(visible) if hasattr(sub, 'predict_proba') else [None]*len(visible)
    # Extract base features per address
    base_features = extract_features(view.visible_subgraph, visible)
    entries = []
    for i, (addr, pred) in enumerate(zip(visible, predictions)):
        if pred != 1:
            continue
        base_fp = base_features[i]
        # Extend with 3 graph features
        extended_fp = np.concatenate([
            base_fp,
            [pagerank.get(addr, 0.0) * 1e6,  # scale for readability
             betweenness.get(addr, 0.0) * 100,
             clustering.get(addr, 0.0) * 100],
        ])
        conf = probs[i] if probs[i] is not None else 1.0
        entries.append((addr, extended_fp, float(conf)))
        all_flagged.add(addr)
    # Sort by confidence, top-K
    entries.sort(key=lambda x: -x[2])
    per_exchange_flagged[view.name] = entries[:60]  # top_k_flagged

print(f'\nFlagged total (unique): {len(all_flagged)}')

# Build custom prompt with EXTENDED features
EXT_FEATURE_NAMES = list(FEATURE_NAMES) + ['pagerank_x1e6', 'betweenness_x100', 'clustering_x100']

def _fmt_addr(addr, ex, fp, conf):
    features_str = ", ".join(
        f"{name}={fp[i]:.2f}"
        for i, name in enumerate(EXT_FEATURE_NAMES)
        if abs(fp[i]) > 0.01
    )
    return f"  ({ex}) {addr}  conf={conf:.3f}\n    features: {features_str}"

lines = []
for ex, entries in per_exchange_flagged.items():
    lines.append(f"\n=== Exchange {ex} — {len(entries)} flagged ===")
    for a, fp, c in entries:
        lines.append(_fmt_addr(a, ex, fp, c))

user_prompt = (
    "Cross-exchange federation batch. Each exchange independently "
    "flagged suspicious addresses. Extended fingerprints include 3 "
    "NEW graph-native features:\n"
    "  - pagerank_x1e6: importance score in the full graph (hub-like nodes score high)\n"
    "  - betweenness_x100: fraction of shortest paths passing through this node (intermediary role)\n"
    "  - clustering_x100: local triangle density (0 = star-topology, high = tight community)\n\n"
    + "\n".join(lines)
)

# Call LLM
system_prompt = _LLM_COORDINATOR_SYSTEM_PROMPT
llm_client = LLMClient(
    timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0),
    max_retries=5,
)

print(f'\nCalling {model}...')
t0 = time.time()
result = llm_client.complete(
    prompt=user_prompt, system=system_prompt,
    model=model, max_tokens=8192,
)
llm_time = time.time() - t0
llm_text = result.text if hasattr(result, 'text') else str(result)
cost = getattr(result, 'cost_usd', 0)
print(f'  fit={llm_time:.1f}s cost=${cost:.4f}')

# Parse
address_to_cluster, reasoning = _parse_llm_clusters(llm_text, all_flagged)
print(f'  parsed clusters: {len(set(address_to_cluster.values()))}')
print(f'  addresses clustered: {len(address_to_cluster)}')

# ARI
addrs_labeled = {}
for a, l in combined.node_labels.items():
    if 'attacker_' in l:
        addrs_labeled[a] = l
common = [a for a in address_to_cluster if a in addrs_labeled]
if len(common) >= 3:
    true_arr = [addrs_labeled[a] for a in common]
    pred_arr = [address_to_cluster[a] for a in common]
    n_true = len(set(true_arr))
    ari = adjusted_rand_score(true_arr, pred_arr)
    print(f'\n  ARI: {ari:.4f}  (true clusters: {n_true}, common addrs: {len(common)})')
else:
    ari = None
    print('  ARI skipped')

# Save
out = REPO / 'results' / f'feature_eng_{label}_{model}.json'
out.write_text(json.dumps({
    'label': label, 'model': model,
    'feature_dim': len(EXT_FEATURE_NAMES),
    'nodes': g.number_of_nodes(), 'edges': g.number_of_edges(),
    'n_attacker': len(attacker_addrs),
    'n_flagged': len(all_flagged),
    'llm': {
        'cost_usd': round(cost, 4),
        'fit_time_s': round(llm_time, 2),
        'n_pred_clusters': len(set(address_to_cluster.values())),
        'ari': round(ari, 4) if ari is not None else None,
        'reasoning_preview': (reasoning or '')[:400],
    },
}, indent=2))
print(f'\nWrote {out}')
