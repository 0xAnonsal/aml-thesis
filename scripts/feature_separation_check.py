"""Per-feature class separation computed the way the GCN sees features (node_order = all graph nodes).
Cohen's d and nonzero fractions for the 19 features, 5 datasets (+ benign v57) and dataset.pkl."""
import sys, math, json, pickle
sys.path.insert(0, '/home/anon/aml-thesis'); sys.path.insert(0, '/home/anon/aml-thesis/src')
from pathlib import Path
import numpy as np
from aml.detectors.baselines import derive_binary_labels
from aml.detectors.dataset import combine_runs
from aml.detectors.gnn import FEATURE_NAMES, extract_features
REPO = Path('/home/anon/aml-thesis'); BENIGN_ROOT = REPO / 'results' / 'benign_corpus_v57'
DATASETS = {
    'sepolia_800': REPO / 'results/sepolia_campaign/2026-09-05T01-47-06_defi-exploit_seed800_sepolia',
    'sepolia_802': REPO / 'results/sepolia_campaign/2026-09-06T01-28-20_defi-exploit_seed802_sepolia',
    'sepolia_803': REPO / 'results/sepolia_campaign/2026-09-08T18-17-57_defi-exploit_seed803_sepolia',
    'anvil_830':   REPO / 'results/anvil_option_a/2026-09-09T17-18-02_defi-exploit_seed830',
    'anvil_850':   REPO / 'results/anvil_option_a/2026-09-09T20-55-17_ransomware-cashout_seed850',
}
def sep(tag, g, bl):
    nodes = sorted(g.nodes()); X = extract_features(g, nodes); idx = {a: i for i, a in enumerate(nodes)}
    lab = [a for a in nodes if a in bl]; y = np.array([bl[a] for a in lab]); Xl = X[[idx[a] for a in lab]]
    rows = []
    for i, n in enumerate(FEATURE_NAMES):
        a, b = Xl[y == 1, i], Xl[y == 0, i]
        sp = math.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
        d = float((a.mean() - b.mean()) / sp) if sp > 0 else 0.0
        rows.append({'feature': n, 'd': round(d, 2), 'att_nz': round(float((a > 0).mean()), 3), 'ben_nz': round(float((b > 0).mean()), 3),
                     'att_mean': round(float(a.mean()), 3), 'ben_mean': round(float(b.mean()), 3)})
    rows.sort(key=lambda r: -abs(r['d']))
    print(f'=== {tag} (att={int(y.sum())}, ben={int((y==0).sum())}) ===', flush=True)
    for r in rows: print(f"  {r['feature']:20s} d={r['d']:+6.2f} att>0={r['att_nz']:.3f} ben>0={r['ben_nz']:.3f} means {r['att_mean']:.2f}/{r['ben_mean']:.2f}", flush=True)
    return rows
out = {}
for label, adir in DATASETS.items():
    c = combine_runs([adir] + sorted(BENIGN_ROOT.iterdir())); out[label] = sep(label, c.graph, derive_binary_labels(c.node_labels))
d = pickle.load(open(Path.home() / 'aml-results' / 'batch_2026-06-26' / 'dataset.pkl', 'rb'))['combined']
out['dataset_pkl'] = sep('dataset.pkl', d.graph, derive_binary_labels(d.node_labels))
json.dump(out, open('/home/anon/aml-thesis/results/feature_separation_full_graph.json', 'w'), indent=1)
