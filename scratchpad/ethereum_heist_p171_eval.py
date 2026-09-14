"""P1-71 post-hoc cluster merge on EthereumHeist (Wu 2023) — cross-domain validation.

Same pipeline as §8.9.42 but on the external dataset (633k nodes, 23 real hacks).
Uses hack_membership as ground truth for actor clustering (ARI).
"""
import sys, json, pickle, random, time
from pathlib import Path
sys.path.insert(0, '/home/anon/aml-thesis')
sys.path.insert(0, '/home/anon/aml-thesis/src')

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, adjusted_rand_score
from sklearn.model_selection import train_test_split
import numpy as np
import httpx

from aml.detectors.multi_agent import _merge_clusters_by_centroid, _auto_pick_max_clusters
from aml.attackers.llm_client import LLMClient

REPO = Path('/home/anon/aml-thesis')
SEED = 42
random.seed(SEED); np.random.seed(SEED)
SWEEP = [3, 5, 8, 12, 20]

print('Loading EthereumHeist...')
data = pickle.load(open(REPO / 'data/ethereum_heist_combined.pkl', 'rb'))
graph = data['graph']
node_labels = data['node_labels']
hack_membership = data['hack_membership']
hacks = data['hacks']

print(f'  Graph: {graph.number_of_nodes():,} nodes / {graph.number_of_edges():,} edges')
print(f'  Hacks: {len(hacks)}')

# Build 4-dim features (same as §8.9.39)
print('\nBuilding features...')
t0 = time.time()
addrs = list(node_labels.keys())
features = np.zeros((len(addrs), 4))
for i, a in enumerate(addrs):
    in_edges = graph.in_edges(a, data=True) if graph.has_node(a) else []
    out_edges = graph.out_edges(a, data=True) if graph.has_node(a) else []
    features[i, 0] = len(list(in_edges))
    features[i, 1] = len(list(out_edges))
    features[i, 2] = np.log1p(sum(d.get('value', 0) for _, _, d in in_edges) / 1e18)
    features[i, 3] = np.log1p(sum(d.get('value', 0) for _, _, d in out_edges) / 1e18)
labels_y = np.array([node_labels[a] for a in addrs])
print(f'  built in {time.time()-t0:.1f}s')

X_train, X_test, y_train, y_test, addr_train, addr_test = train_test_split(
    features, labels_y, addrs, test_size=0.2, random_state=SEED, stratify=labels_y,
)

# RF Phase 1
print('\n--- RF baseline ---')
t0 = time.time()
rf = RandomForestClassifier(n_estimators=100, n_jobs=-1, random_state=SEED)
rf.fit(X_train, y_train)
rf_pred = rf.predict(X_test)
rf_scores = rf.predict_proba(X_test)[:, 1]
print(f'  fit={time.time()-t0:.1f}s F1={f1_score(y_test, rf_pred):.4f}')

# Top-60 flagged
top_k = 60
top_idx = np.argsort(-rf_scores)[:top_k]
top_addrs = [addr_test[i] for i in top_idx]
top_feats = X_test[top_idx]
addr_to_fp = {top_addrs[i]: top_feats[i] for i in range(top_k)}

# LLM Phase 2
print(f'\n--- LLM Phase 2 on top-{top_k} ---')
llm_client = LLMClient(
    timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0),
    max_retries=5,
)
addr_map = {f'a{i}': top_addrs[i] for i in range(top_k)}
prompt_data = {
    'addresses': {
        f'a{i}': {
            'in_degree': int(top_feats[i, 0]),
            'out_degree': int(top_feats[i, 1]),
            'log_in_eth': round(float(top_feats[i, 2]), 2),
            'log_out_eth': round(float(top_feats[i, 3]), 2),
        }
        for i in range(top_k)
    },
    'task': (
        'Cluster these 60 flagged addresses into distinct actor groups '
        'based on behavioral similarity. Real Ethereum hacks are separate '
        'operations. Output JSON: {"actor_clusters": {"0": ["a1","a5"], "1": ["a3","a7"], ...}, '
        '"overall_reasoning": "..."}. Aim for 5-25 clusters.'
    ),
}
user_prompt = json.dumps(prompt_data)
system_prompt = (
    'You are an AML analyst clustering flagged Ethereum wallets into '
    'distinct actor operations. Reason about behavioral fingerprints: '
    'high-degree hubs are likely CEX/DEX/services; low-degree isolates '
    'are likely dust/mules; balanced in/out are typical intermediaries. '
    'Output ONLY valid JSON matching the requested schema.'
)

t0 = time.time()
result = llm_client.complete(
    prompt=user_prompt, system=system_prompt,
    model='haiku', max_tokens=8192,
)
llm_time = time.time() - t0
llm_text = result.text if hasattr(result, 'text') else str(result)
cost = getattr(result, 'cost_usd', 0.0)
print(f'  fit={llm_time:.1f}s cost=${cost:.4f}')

# Parse
import re
text = llm_text.strip()
fence = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
if fence: text = fence.group(1)
else:
    s = text.find('{'); e = text.rfind('}')
    if s >= 0 and e > s: text = text[s:e+1]
try:
    parsed = json.loads(text)
except json.JSONDecodeError as ex:
    print(f'  PARSE FAIL: {ex}')
    parsed = {}

pred_clusters = {}
for cid_key, ids in (parsed.get('actor_clusters', {}) or {}).items():
    try: cid = int(cid_key)
    except: continue
    for id_str in ids:
        if id_str in addr_map:
            pred_clusters[addr_map[id_str]] = cid

# Ground truth
true_clusters_str = {}
for a in top_addrs:
    m = hack_membership.get(a)
    if m: true_clusters_str[a] = ','.join(sorted(m))
    else: true_clusters_str[a] = 'unknown'

def _ari(pmap):
    common = [a for a in top_addrs if a in pmap and true_clusters_str.get(a, 'unknown') != 'unknown']
    if len(common) < 3: return None, 0, 0
    t = [true_clusters_str[a] for a in common]
    p = [pmap[a] for a in common]
    return adjusted_rand_score(t, p), len(set(t)), len(set(p))

baseline_ari, n_true, n_pred_baseline = _ari(pred_clusters)
print(f'\n  baseline ARI: {baseline_ari:.4f}  (true={n_true} pred={n_pred_baseline})')

# ============ P1-71 SWEEP ============
sweep = {'baseline': {'max_c': n_pred_baseline, 'ari': baseline_ari}}
for max_c in SWEEP:
    if max_c >= n_pred_baseline:
        sweep[f'max_{max_c}'] = {'max_c': n_pred_baseline, 'ari': baseline_ari, 'note': 'no merge'}
        continue
    merged = _merge_clusters_by_centroid(pred_clusters, addr_to_fp, max_c)
    a, _, np_ = _ari(merged)
    sweep[f'max_{max_c}'] = {'max_c': np_, 'ari': a}
    print(f'  merge → max_c={max_c}: got {np_} clusters, ARI={a:.4f}')

best_key = max(sweep.keys(), key=lambda k: sweep[k]['ari'] if sweep[k]['ari'] is not None else -1)
best = sweep[best_key]
delta = (best['ari'] - baseline_ari) if baseline_ari is not None and best['ari'] is not None else None
print(f'\n  BEST (oracle): {best_key} → ARI={best["ari"]:.4f} ({best["max_c"]} clusters)')
print(f'  Δ vs baseline: {delta:+.4f}' if delta is not None else '  Δ: N/A')

# Silhouette auto-tune
auto_k = _auto_pick_max_clusters(pred_clusters, addr_to_fp, k_candidates=[2, 3, 5, 8, 12])
auto_merged = _merge_clusters_by_centroid(pred_clusters, addr_to_fp, auto_k)
auto_ari, _, _ = _ari(auto_merged)
n_auto = len(set(auto_merged.values()))
auto_delta = (auto_ari - baseline_ari) if baseline_ari is not None and auto_ari is not None else None
print(f'\n  SILHOUETTE-PICKED k={auto_k}: got {n_auto} clusters, ARI={auto_ari:.4f}  Δ={auto_delta:+.4f}')

# Save
out = REPO / 'results' / 'p171_posthoc_ethereum_heist.json'
out.write_text(json.dumps({
    'dataset': 'EthereumHeist (Wu 2023)',
    'n_nodes': graph.number_of_nodes(),
    'n_edges': graph.number_of_edges(),
    'n_hacks': len(hacks),
    'top_k_flagged': top_k,
    'true_n_clusters': n_true,
    'llm': {
        'model': 'haiku',
        'cost_usd': round(cost, 4),
        'fit_time_s': round(llm_time, 2),
    },
    'sweep': {k: {**v, 'ari': round(v['ari'], 4) if v['ari'] is not None else None}
              for k, v in sweep.items()},
    'best': best_key,
    'delta_vs_baseline': round(delta, 4) if delta is not None else None,
    'silhouette_auto': {
        'picked_k': auto_k,
        'n_clusters_after': n_auto,
        'ari': round(auto_ari, 4) if auto_ari is not None else None,
        'delta_vs_baseline': round(auto_delta, 4) if auto_delta is not None else None,
    },
}, indent=2))
print(f'\nWrote {out}')
