"""RF feature importance on sepolia_803 + benign v57: labelled-only extraction (as Tabla 25 was built) vs full-graph extraction."""
import sys; sys.path.insert(0, '/home/anon/aml-thesis'); sys.path.insert(0, '/home/anon/aml-thesis/src')
from pathlib import Path
import numpy as np, json
from sklearn.ensemble import RandomForestClassifier
from aml.detectors.baselines import derive_binary_labels
from aml.detectors.dataset import combine_runs
from aml.detectors.gnn import FEATURE_NAMES, extract_features
REPO = Path('/home/anon/aml-thesis')
c = combine_runs([REPO / 'results/sepolia_campaign/2026-09-08T18-17-57_defi-exploit_seed803_sepolia'] + sorted((REPO / 'results/benign_corpus_v57').iterdir()))
g = c.graph; bl = derive_binary_labels(c.node_labels); addrs = sorted(bl); y = np.array([bl[a] for a in addrs])
out = {}
for tag, nodes in (('labelled_only', addrs), ('full_graph', sorted(g.nodes()))):
    X = extract_features(g, nodes); idx = {a: i for i, a in enumerate(nodes)}; Xl = X[[idx[a] for a in addrs]]
    rf = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1).fit(Xl, y)
    imp = rf.feature_importances_; order = np.argsort(-imp)
    out[tag] = [(FEATURE_NAMES[i], round(float(imp[i]), 4)) for i in order]
    print(f'=== {tag} ===')
    for r, (n, v) in enumerate(out[tag], 1): print(f'  {r:2d} {n:20s} {v:.4f}')
json.dump(out, open('/home/anon/aml-thesis/results/rf_importance_full_graph.json', 'w'), indent=1)
