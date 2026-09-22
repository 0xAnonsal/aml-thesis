"""Campaign-id LOCO (§8.9.49) — extends §8.9.48 with proper per-campaign
true labels.

Instead of true clusters = 3 role types, this eval builds true labels
of the form `{role}_{seed}` giving 7 campaigns × 3 roles = up to 21
true clusters. This is the STRICT campaign-attribution test.
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
import re

REPO = Path('/home/anon/aml-thesis')
SEED = 42
SWEEP = [3, 5, 8, 12, 20, 30, 50]

ATTACKERS = [
    REPO / 'results/sepolia_campaign/2026-09-05T01-47-06_defi-exploit_seed800_sepolia',
    REPO / 'results/sepolia_campaign/2026-09-06T01-28-20_defi-exploit_seed802_sepolia',
    REPO / 'results/sepolia_campaign/2026-09-08T18-17-57_defi-exploit_seed803_sepolia',
    REPO / 'results/anvil_option_a/2026-09-09T17-18-02_defi-exploit_seed830',
    REPO / 'results/anvil_option_a/2026-09-09T20-55-17_ransomware-cashout_seed850',
]
for p in sorted((REPO / 'results/anvil_option_a').iterdir()):
    if p.is_dir() and (p / 'chain_trace.jsonl').exists() and ('seed900' in p.name or 'seed901' in p.name):
        ATTACKERS.append(p)

BENIGN_ROOT = REPO / 'results' / 'benign_corpus_v57'
benign_dirs = sorted(BENIGN_ROOT.iterdir())

model = sys.argv[1] if len(sys.argv) > 1 else 'haiku'

print(f'=== CAMPAIGN-ID LOCO eval: {len(ATTACKERS)} attackers × 1 combined graph ===')

# Build per-campaign address→campaign_id map
addr_to_campaign: dict[str, str] = {}
for attacker_dir in ATTACKERS:
    seed_match = re.search(r'seed(\d+)', attacker_dir.name)
    seed_id = seed_match.group(1) if seed_match else attacker_dir.name
    addr_data = json.loads((attacker_dir / 'addresses.json').read_text())
    # Every attacker-controlled address in this dir → tag with seed
    for key in ('attacker_wallets', 'bootstrap_attackers',
                'burners_generated_during_campaign', 'clean_exit_wallets',
                'clean_exits_funded'):
        for a in addr_data.get(key, []):
            addr = a['address'] if isinstance(a, dict) else a
            addr_to_campaign[addr.lower()] = f'seed{seed_id}'
    src = addr_data.get('source_wallet')
    if src: addr_to_campaign[src.lower()] = f'seed{seed_id}'

print(f'  Addresses tagged with campaign_id: {len(addr_to_campaign)}')
print(f'  Unique campaigns: {len(set(addr_to_campaign.values()))}')

combined = combine_runs(list(ATTACKERS) + benign_dirs)
g = combined.graph
bin_labels = derive_binary_labels(combined.node_labels)
attacker_addrs = {a for a, y in bin_labels.items() if y == 1}
print(f'  Combined graph: {g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges')

# Build TWO true label maps:
#   A) role-only (matches §8.9.48 for reference)
#   B) role + campaign_id (STRICT test — this is what we care about)
true_role: dict[str, str] = {}
true_role_camp: dict[str, str] = {}
for a, l in combined.node_labels.items():
    if 'attacker_' in l:
        true_role[a] = l
        camp = addr_to_campaign.get(a.lower(), 'unknown')
        true_role_camp[a] = f'{l}__{camp}'

n_role = len(set(true_role.values()))
n_role_camp = len(set(true_role_camp.values()))
print(f'  Ground truth: {n_role} role clusters, {n_role_camp} role×campaign clusters')

views = partial_visibility_split(combined, num_exchanges=3, seed=SEED)
lou = PerExchangeDetector(detector_factory=lambda: LouvainDetector()).fit_per_view(views, bin_labels)

# Phase 1 F1
addr_pred: dict[str, int] = {}
for view in views:
    sub = lou.detectors[view.name]
    preds = sub.predict(list(view.visible_addresses))
    for a, p in zip(list(view.visible_addresses), preds):
        if p == 1 or addr_pred.get(a, 0) == 1: addr_pred[a] = 1
        else: addr_pred[a] = addr_pred.get(a, 0)

labeled_addrs = list(bin_labels.keys())
y_true = np.array([bin_labels[a] for a in labeled_addrs])
y_pred = np.array([addr_pred.get(a, 0) for a in labeled_addrs])
f1 = f1_score(y_true, y_pred); prec = precision_score(y_true, y_pred) if y_pred.sum() > 0 else 0.0
rec = recall_score(y_true, y_pred)
print(f'\n  Phase 1 Louvain F1={f1:.4f}  P={prec:.4f}  R={rec:.4f}')

# Phase 2: gather flagged
per_exchange_flagged = {}
all_flagged = set(); addr_to_fp = {}
for view in views:
    visible = list(view.visible_addresses)
    sub = lou.detectors[view.name]
    predictions = sub.predict(visible)
    probs = sub.predict_proba(visible) if hasattr(sub, 'predict_proba') else [None]*len(visible)
    base_features = extract_features(view.visible_subgraph, visible)
    entries = []
    for i, (addr, pred) in enumerate(zip(visible, predictions)):
        if pred != 1: continue
        fp = base_features[i]; conf = probs[i] if probs[i] is not None else 1.0
        entries.append((addr, fp, float(conf)))
        all_flagged.add(addr); addr_to_fp[addr] = fp
    entries.sort(key=lambda x: -x[2])
    per_exchange_flagged[view.name] = entries[:60]
print(f'  flagged unique: {len(all_flagged)}')

def _fmt(addr, ex, fp, conf):
    fs = ", ".join(f"{n}={fp[i]:.2f}" for i, n in enumerate(FEATURE_NAMES) if abs(fp[i]) > 0.01)
    return f"  ({ex}) {addr}  conf={conf:.3f}\n    features: {fs}"

lines = []
for ex, entries in per_exchange_flagged.items():
    lines.append(f"\n=== Exchange {ex} — {len(entries)} flagged ===")
    for a, fp, c in entries: lines.append(_fmt(a, ex, fp, c))

user_prompt = ("Cross-exchange federation batch — multiple distinct attacker campaigns present. "
               "Cluster the flagged addresses into per-campaign actor operations "
               "(not just role types — separate CAMPAIGNS should be distinct clusters).\n") + "\n".join(lines)

llm_client = LLMClient(
    timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0),
    max_retries=5,
)
print(f'\nCalling {model}...')
t0 = time.time()
result = llm_client.complete(prompt=user_prompt, system=_LLM_COORDINATOR_SYSTEM_PROMPT,
                              model=model, max_tokens=8192)
llm_time = time.time() - t0
llm_text = result.text if hasattr(result, 'text') else str(result)
cost = getattr(result, 'cost_usd', 0)
print(f'  fit={llm_time:.1f}s cost=${cost:.4f}')

address_to_cluster, reasoning = _parse_llm_clusters(llm_text, all_flagged)
n_baseline = len(set(address_to_cluster.values()))
print(f'  baseline clusters: {n_baseline}, clustered addrs: {len(address_to_cluster)}')

def _ari(cluster_map, ground):
    common_local = [a for a in cluster_map if a in ground]
    if len(common_local) < 3: return None, 0
    t = [ground[a] for a in common_local]
    p = [cluster_map[a] for a in common_local]
    return adjusted_rand_score(t, p), len(common_local)

# ARI on BOTH ground truths for reference
ari_role_baseline, common_role = _ari(address_to_cluster, true_role)
ari_camp_baseline, common_camp = _ari(address_to_cluster, true_role_camp)
print(f'\n  Baseline ARI (role-only, 3 clusters):        {ari_role_baseline:.4f}  ({common_role} common)')
print(f'  Baseline ARI (role×campaign, {n_role_camp} clusters): {ari_camp_baseline:.4f}  ({common_camp} common)')

# P1-71 sweep on role×campaign
print(f'\n  P1-71 sweep against ROLE×CAMPAIGN ground truth:')
sweep = {'baseline': {'max_c': n_baseline, 'ari_role': ari_role_baseline, 'ari_camp': ari_camp_baseline}}
for max_c in SWEEP:
    if max_c >= n_baseline:
        sweep[f'max_{max_c}'] = {'max_c': n_baseline, 'ari_role': ari_role_baseline,
                                  'ari_camp': ari_camp_baseline, 'note': 'no merge'}
        continue
    merged = _merge_clusters_by_centroid(address_to_cluster, addr_to_fp, max_c)
    ar_role, _ = _ari(merged, true_role)
    ar_camp, _ = _ari(merged, true_role_camp)
    n = len(set(merged.values()))
    sweep[f'max_{max_c}'] = {'max_c': n, 'ari_role': ar_role, 'ari_camp': ar_camp}
    print(f'    max_c={max_c:3d} → {n:3d} clusters, ARI_role={ar_role:.4f}, ARI_camp={ar_camp:.4f}')

# Silhouette auto-tune
auto_k = _auto_pick_max_clusters(address_to_cluster, addr_to_fp, [3, 5, 8, 12, 20, 30])
auto_merged = _merge_clusters_by_centroid(address_to_cluster, addr_to_fp, auto_k)
n_auto = len(set(auto_merged.values()))
ari_auto_role, _ = _ari(auto_merged, true_role)
ari_auto_camp, _ = _ari(auto_merged, true_role_camp)
print(f'\n  SILHOUETTE k={auto_k} → {n_auto} clusters')
print(f'    ARI_role: {ari_auto_role:.4f}')
print(f'    ARI_camp: {ari_auto_camp:.4f}')

# Save
out = REPO / 'results' / 'reps' / f'rep{__import__("os").environ.get("REP","2")}_campaign_id_loco_{model}.json'
def _r(x): return round(x, 4) if x is not None else None
out.write_text(json.dumps({
    'n_attackers_combined': len(ATTACKERS),
    'n_nodes': g.number_of_nodes(), 'n_edges': g.number_of_edges(),
    'n_true_role_clusters': n_role,
    'n_true_role_x_campaign_clusters': n_role_camp,
    'phase1': {'louvain_f1': _r(f1), 'precision': _r(prec), 'recall': _r(rec)},
    'phase2': {
        'llm_model': model,
        'llm_cost_usd': _r(cost),
        'llm_fit_time_s': _r(llm_time),
        'n_flagged': len(all_flagged),
        'baseline_ari_role': _r(ari_role_baseline),
        'baseline_ari_campaign': _r(ari_camp_baseline),
        'sweep': {k: {**{kk: (_r(vv) if isinstance(vv,(int,float)) or vv is None else vv) for kk,vv in v.items()}}
                  for k, v in sweep.items()},
        'silhouette': {
            'picked_k': auto_k, 'n_clusters_after': n_auto,
            'ari_role': _r(ari_auto_role), 'ari_campaign': _r(ari_auto_camp),
        },
    },
}, indent=2))
print(f'\nWrote {out}')
