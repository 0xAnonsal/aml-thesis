"""LLM defender eval on EthereumHeist (Wu 2023, 633k nodes, 23 real hacks).

External cross-domain eval — different pipeline from ours, so no label
leakage. Uses hack_membership as ground truth for actor clustering (ARI).
"""
import sys, json, pickle, random, time
from pathlib import Path
sys.path.insert(0, '/home/anon/aml-thesis')
sys.path.insert(0, '/home/anon/aml-thesis/src')

from dotenv import load_dotenv
load_dotenv('/home/anon/aml-thesis/.env')

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, adjusted_rand_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
import numpy as np

REPO = Path('/home/anon/aml-thesis')
SEED = 42
random.seed(SEED)
np.random.seed(SEED)

print('Loading EthereumHeist...')
data = pickle.load(open(REPO / 'data/ethereum_heist_combined.pkl', 'rb'))
graph = data['graph']
node_labels = data['node_labels']       # {addr: 0/1}
hack_membership = data['hack_membership']  # {addr: set(hack_names) or None}
hacks = data['hacks']

print(f'  Graph: {graph.number_of_nodes():,} nodes / {graph.number_of_edges():,} edges')
print(f'  Labels: {len(node_labels):,}')
print(f'  Hacks: {len(hacks)}')
print(f'  Illicit rate: {sum(node_labels.values())/len(node_labels)*100:.2f}%')

# Build feature vectors: [in_degree, out_degree, log_total_in_value, log_total_out_value]
# Simple 4-dim features to keep prompt compact
print('\nBuilding features...')
t0 = time.time()
addrs = list(node_labels.keys())
features = np.zeros((len(addrs), 4))
addr_to_idx = {a: i for i, a in enumerate(addrs)}
for i, a in enumerate(addrs):
    in_edges = graph.in_edges(a, data=True) if graph.has_node(a) else []
    out_edges = graph.out_edges(a, data=True) if graph.has_node(a) else []
    features[i, 0] = len(list(in_edges))
    features[i, 1] = len(list(out_edges))
    features[i, 2] = np.log1p(sum(d.get('value', 0) for _, _, d in in_edges) / 1e18)
    features[i, 3] = np.log1p(sum(d.get('value', 0) for _, _, d in out_edges) / 1e18)
labels_y = np.array([node_labels[a] for a in addrs])
print(f'  built in {time.time()-t0:.1f}s')

# Train/test split (stratified)
X_train, X_test, y_train, y_test, addr_train, addr_test = train_test_split(
    features, labels_y, addrs, test_size=0.2, random_state=SEED, stratify=labels_y,
)
print(f'  train: {len(X_train):,}  test: {len(X_test):,}')

# RF baseline (F1)
print('\n--- RF baseline ---')
t0 = time.time()
rf = RandomForestClassifier(n_estimators=100, n_jobs=-1, random_state=SEED)
rf.fit(X_train, y_train)
rf_pred = rf.predict(X_test)
rf_scores = rf.predict_proba(X_test)[:, 1]
rf_f1 = f1_score(y_test, rf_pred)
rf_prec = precision_score(y_test, rf_pred)
rf_rec = recall_score(y_test, rf_pred)
print(f'  fit={time.time()-t0:.1f}s')
print(f'  F1={rf_f1:.4f}  P={rf_prec:.4f}  R={rf_rec:.4f}')

# Pick top-60 flagged (highest RF score) for LLM clustering
top_k = 60
top_idx = np.argsort(-rf_scores)[:top_k]
top_addrs = [addr_test[i] for i in top_idx]
top_feats = X_test[top_idx]

# LLM clustering: send features + ask for actor_clusters
print(f'\n--- LLM defender on top-{top_k} flagged ---')
from aml.attackers.llm_client import LLMClient
import httpx
llm_client = LLMClient(
    timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0),
    max_retries=5,
)

# Build compact JSON prompt with features + address indices (short IDs to save tokens)
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
tokens_in = getattr(result, 'input_tokens', 0)
tokens_out = getattr(result, 'output_tokens', 0)
print(f'  fit={llm_time:.1f}s cost=${cost:.4f}  in={tokens_in} out={tokens_out}')

# Parse JSON
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
    print(f'  Raw: {llm_text[:500]}')
    parsed = {}

# Build predicted clusters (addr → cluster_id)
pred_clusters = {}
for cid_key, ids in (parsed.get('actor_clusters', {}) or {}).items():
    try: cid = int(cid_key)
    except: continue
    for id_str in ids:
        if id_str in addr_map:
            pred_clusters[addr_map[id_str]] = cid

# Build true clusters using hack_membership (join set members as string label)
true_clusters_str = {}
for a in top_addrs:
    m = hack_membership.get(a)
    if m:
        true_clusters_str[a] = ','.join(sorted(m))
    else:
        true_clusters_str[a] = 'unknown'

# ARI computation on addresses with both pred + true labeled
common = [a for a in top_addrs if a in pred_clusters and a in true_clusters_str
          and true_clusters_str[a] != 'unknown']
if len(common) >= 3:
    true_arr = [true_clusters_str[a] for a in common]
    pred_arr = [pred_clusters[a] for a in common]
    n_true_clusters = len(set(true_arr))
    n_pred_clusters = len(set(pred_arr))
    ari = adjusted_rand_score(true_arr, pred_arr)
    print(f'\n  Addresses in common: {len(common)}/{top_k}')
    print(f'  True clusters (hacks): {n_true_clusters}')
    print(f'  Pred clusters: {n_pred_clusters}')
    print(f'  ARI: {ari:.4f}')
else:
    print(f'\n  ARI skipped (only {len(common)} labeled addrs in common)')
    ari = None
    n_true_clusters = n_pred_clusters = 0

# Save
out = REPO / 'results' / 'llm_defender_ethereum_heist.json'
out.write_text(json.dumps({
    'dataset': 'EthereumHeist (Wu 2023)',
    'n_nodes': graph.number_of_nodes(),
    'n_hacks': len(hacks),
    'illicit_rate': round(sum(labels_y)/len(labels_y), 4),
    'rf_baseline': {'f1': round(rf_f1, 4), 'precision': round(rf_prec, 4), 'recall': round(rf_rec, 4)},
    'llm_defender': {
        'model': 'haiku', 'cost_usd': round(cost, 4),
        'fit_time_s': round(llm_time, 2),
        'top_k_flagged': top_k,
        'tokens_in': tokens_in, 'tokens_out': tokens_out,
        'ari': round(ari, 4) if ari is not None else None,
        'n_true_clusters': n_true_clusters,
        'n_pred_clusters': n_pred_clusters,
        'reasoning_preview': parsed.get('overall_reasoning', '')[:400] if isinstance(parsed, dict) else '',
    },
}, indent=2))
print(f'\nWrote {out}')
