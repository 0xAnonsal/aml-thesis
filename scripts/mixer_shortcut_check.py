"""Mixer-shortcut check done the way the pipeline computes features: node_order = ALL graph nodes (contracts included),
(a) on the full combined graph and (b) inside the three exchange views (contracts shared). Also dataset.pkl (LOCO).
Reports per class the fraction of addresses with any mixer_* > 0 / swap_* > 0 and the trivial rule P/R/F1."""
import sys, math, json, pickle
sys.path.insert(0, '/home/anon/aml-thesis'); sys.path.insert(0, '/home/anon/aml-thesis/src')
from pathlib import Path
import numpy as np
from aml.detectors.baselines import derive_binary_labels
from aml.detectors.dataset import combine_runs, partial_visibility_split
from aml.detectors.gnn import FEATURE_NAMES, extract_features
REPO = Path('/home/anon/aml-thesis'); BENIGN_ROOT = REPO / 'results' / 'benign_corpus_v57'
DATASETS = {
    'sepolia_800': REPO / 'results/sepolia_campaign/2026-09-05T01-47-06_defi-exploit_seed800_sepolia',
    'sepolia_802': REPO / 'results/sepolia_campaign/2026-09-06T01-28-20_defi-exploit_seed802_sepolia',
    'sepolia_803': REPO / 'results/sepolia_campaign/2026-09-08T18-17-57_defi-exploit_seed803_sepolia',
    'anvil_830':   REPO / 'results/anvil_option_a/2026-09-09T17-18-02_defi-exploit_seed830',
    'anvil_850':   REPO / 'results/anvil_option_a/2026-09-09T20-55-17_ransomware-cashout_seed850',
}
MIX = [i for i, n in enumerate(FEATURE_NAMES) if n.startswith('mixer_')]; SWP = [i for i, n in enumerate(FEATURE_NAMES) if n.startswith('swap_')]
def prf(y, p):
    tp = int(((y == 1) & (p == 1)).sum()); fp = int(((y == 0) & (p == 1)).sum()); fn = int(((y == 1) & (p == 0)).sum())
    P = tp / (tp + fp) if tp + fp else 0.0; R = tp / (tp + fn) if tp + fn else 0.0
    return round(P, 3), round(R, 3), round(2 * P * R / (P + R), 3) if P + R else 0.0
def report(tag, g, bl):
    nodes = sorted(g.nodes()); X = extract_features(g, nodes); idx = {a: i for i, a in enumerate(nodes)}
    lab = [a for a in nodes if a in bl]; y = np.array([bl[a] for a in lab]); Xl = X[[idx[a] for a in lab]]
    bg = [a for a in nodes if a not in bl]; Xb = X[[idx[a] for a in bg]] if bg else np.zeros((0, X.shape[1]))
    mix_any = Xl[:, MIX].sum(1) > 0; swp_any = Xl[:, SWP].sum(1) > 0
    out = {'attackers': int(y.sum()), 'benign': int((y == 0).sum()), 'background': len(bg),
           'att_mixer_any': round(float(mix_any[y == 1].mean()), 3) if y.sum() else None, 'ben_mixer_any': round(float(mix_any[y == 0].mean()), 3),
           'bg_mixer_any': round(float((Xb[:, MIX].sum(1) > 0).mean()), 3) if len(bg) else None,
           'att_swap_any': round(float(swp_any[y == 1].mean()), 3) if y.sum() else None, 'ben_swap_any': round(float(swp_any[y == 0].mean()), 3),
           'rule_mixer_PRF': prf(y, mix_any.astype(int)), 'rule_swap_PRF': prf(y, swp_any.astype(int))}
    per = {}
    for i in MIX + SWP:
        per[FEATURE_NAMES[i]] = (round(float((Xl[y == 1, i] > 0).mean()), 3) if y.sum() else None, round(float((Xl[y == 0, i] > 0).mean()), 3))
    out['per_feature_att_ben_nonzero'] = per
    print(f'--- {tag}: att={out["attackers"]} ben={out["benign"]} bg={out["background"]} | mixer>0: att {out["att_mixer_any"]} ben {out["ben_mixer_any"]} bg {out["bg_mixer_any"]} | swap>0: att {out["att_swap_any"]} ben {out["ben_swap_any"]} | rule mixer P/R/F1 {out["rule_mixer_PRF"]}', flush=True)
    print('    ', per, flush=True)
    return out
res = {}
for label, adir in DATASETS.items():
    combined = combine_runs([adir] + sorted(BENIGN_ROOT.iterdir())); bl = derive_binary_labels(combined.node_labels)
    res[label] = {'full_graph': report(f'{label} FULL GRAPH', combined.graph, bl)}
    views = partial_visibility_split(combined, num_exchanges=3, seed=42)
    res[label]['views'] = {v.name: report(f'{label} view {v.name}', v.visible_subgraph, {a: bl[a] for a in v.visible_addresses if a in bl}) for v in views}
d = pickle.load(open(Path.home() / 'aml-results' / 'batch_2026-06-26' / 'dataset.pkl', 'rb')); combined = d['combined']
bl = derive_binary_labels(combined.node_labels)
res['dataset_pkl'] = {'full_graph': report('dataset.pkl FULL GRAPH', combined.graph, bl)}
views = partial_visibility_split(combined, num_exchanges=3, seed=42)
res['dataset_pkl']['views'] = {v.name: report(f'dataset.pkl view {v.name}', v.visible_subgraph, {a: bl[a] for a in v.visible_addresses if a in bl}) for v in views}
json.dump(res, open('/home/anon/aml-thesis/results/mixer_shortcut_check.json', 'w'), indent=1)
