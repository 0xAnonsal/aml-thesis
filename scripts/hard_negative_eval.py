"""Hard-negative eval: attacker + synthetic benign + 500 real Sepolia background labeled as benign.

Fixes the label leakage of §8.9.36 by adding REAL Sepolia background
tx as "hard negatives" — organic dev-testnet traffic that shouldn't
look like laundering.

Usage: python3 hard_negative_eval.py <dataset_label> [num_hard_neg]
"""
import sys
import json
import random
import time
from pathlib import Path
sys.path.insert(0, '/home/anon/aml-thesis')
sys.path.insert(0, '/home/anon/aml-thesis/src')

from dotenv import load_dotenv
load_dotenv('/home/anon/aml-thesis/.env')

from aml.detectors.baselines import LouvainDetector, derive_binary_labels, PerExchangeDetector
from aml.detectors.dataset import combine_runs, partial_visibility_split
from aml.detectors.multi_agent import LLMDefenderCoordinator

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
num_hard_neg = int(sys.argv[2]) if len(sys.argv) > 2 else 500
model = sys.argv[3] if len(sys.argv) > 3 else 'haiku'

attacker_dir = DATASETS[label]
benign_dirs = sorted(BENIGN_ROOT.iterdir())

print(f'=== HARD-NEGATIVE eval: {label} + v57 benign + {num_hard_neg} real bg ===')
print(f'Model: {model}')

t0 = time.time()
combined = combine_runs([attacker_dir] + benign_dirs)
g = combined.graph
bin_labels = derive_binary_labels(combined.node_labels)
attacker_addrs = {a for a, y in bin_labels.items() if y == 1}
benign_addrs_synth = {a for a, y in bin_labels.items() if y == 0}
unknown = list(set(g.nodes()) - set(bin_labels.keys()))
print(f'  nodes={g.number_of_nodes():,} edges={g.number_of_edges():,}')
print(f'  attacker={len(attacker_addrs)} synth_benign={len(benign_addrs_synth)} unknown={len(unknown)}')

# HARD-NEGATIVE INJECTION: pick N random background addrs and LABEL them as benign
random.seed(SEED)
if len(unknown) >= num_hard_neg:
    hard_negatives = random.sample(unknown, num_hard_neg)
else:
    hard_negatives = unknown
    print(f'  WARN: only {len(unknown)} unknown available, using all')

# Update labels: hard negatives get label=0 (benign)
for a in hard_negatives:
    bin_labels[a] = 0
    combined.node_labels[a] = 'hard_benign_sepolia_background'

# Recompute
benign_addrs = {a for a, y in bin_labels.items() if y == 0}
unknown_remain = set(g.nodes()) - set(bin_labels.keys())
print(f'  After hard-neg: synth_benign+hard_neg={len(benign_addrs)}  remaining_unknown={len(unknown_remain)}')

views = partial_visibility_split(combined, num_exchanges=3, seed=SEED)

# Run Louvain baseline
print(f'\n--- Louvain baseline ---')
t0 = time.time()
lou = PerExchangeDetector(detector_factory=lambda: LouvainDetector()).fit_per_view(views, bin_labels)
lou_time = time.time() - t0
flagged_lou = set()
for view in views:
    visible = list(view.visible_addresses)
    sub = lou.detectors[view.name]
    for addr, y in zip(visible, sub.predict(visible)):
        if y == 1:
            flagged_lou.add(addr)
tp = sum(1 for a in attacker_addrs if a in flagged_lou)
fp_hard = sum(1 for a in hard_negatives if a in flagged_lou)
fp_synth = sum(1 for a in benign_addrs_synth if a in flagged_lou)
fp_bg = sum(1 for a in unknown_remain if a in flagged_lou)
fp_total_labeled_benign = fp_hard + fp_synth
prec = tp / (tp + fp_total_labeled_benign) if (tp + fp_total_labeled_benign) else 0
rec = tp / len(attacker_addrs) if attacker_addrs else 0
f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0

print(f'  TP={tp}/{len(attacker_addrs)} FP_synth={fp_synth}/{len(benign_addrs_synth)} FP_hard={fp_hard}/{len(hard_negatives)}')
print(f'  P={prec:.4f} R={rec:.4f} F1={f1:.4f}')
print(f'  FPR on hard-negatives (real bg): {fp_hard/len(hard_negatives)*100:.2f}%')
print(f'  FPR on remaining bg: {fp_bg/max(1,len(unknown_remain))*100:.2f}%')

# Run LLM defender
print(f'\n--- LLM defender ({model}) ---')
t0 = time.time()
llm = LLMDefenderCoordinator(
    detector_factory=lambda: LouvainDetector(),
    llm_model=model,
)
llm.fit_per_view(views, bin_labels)
llm_time = time.time() - t0

flagged_llm = set()
for view in views:
    visible = list(view.visible_addresses)
    sub = llm._binary.detectors[view.name]
    for addr, y in zip(visible, sub.predict(visible)):
        if y == 1:
            flagged_llm.add(addr)
tp_llm = sum(1 for a in attacker_addrs if a in flagged_llm)
fp_hard_llm = sum(1 for a in hard_negatives if a in flagged_llm)
fp_synth_llm = sum(1 for a in benign_addrs_synth if a in flagged_llm)
fp_total_llm = fp_hard_llm + fp_synth_llm
prec_llm = tp_llm / (tp_llm + fp_total_llm) if (tp_llm + fp_total_llm) else 0
rec_llm = tp_llm / len(attacker_addrs) if attacker_addrs else 0
f1_llm = 2 * prec_llm * rec_llm / (prec_llm + rec_llm) if (prec_llm + rec_llm) else 0

print(f'  fit={llm_time:.1f}s cost=${llm.usage.get("cost_usd",0):.4f} fallback={llm.llm_output_used_fallback}')
print(f'  TP={tp_llm}/{len(attacker_addrs)} FP_synth={fp_synth_llm}/{len(benign_addrs_synth)} FP_hard={fp_hard_llm}/{len(hard_negatives)}')
print(f'  P={prec_llm:.4f} R={rec_llm:.4f} F1={f1_llm:.4f}')
print(f'  FPR on hard-negatives (real bg): {fp_hard_llm/len(hard_negatives)*100:.2f}%')

# Save
out = {
    'label': label, 'model': model, 'num_hard_neg': num_hard_neg,
    'nodes': g.number_of_nodes(), 'edges': g.number_of_edges(),
    'n_attacker': len(attacker_addrs),
    'n_benign_synth': len(benign_addrs_synth),
    'n_hard_neg': len(hard_negatives),
    'n_remaining_bg': len(unknown_remain),
    'louvain': {
        'tp': tp, 'fp_synth': fp_synth, 'fp_hard_neg': fp_hard,
        'precision': round(prec, 4), 'recall': round(rec, 4), 'f1': round(f1, 4),
        'fpr_hard_neg': round(fp_hard/len(hard_negatives), 4) if hard_negatives else 0,
    },
    'llm_defender': {
        'model': model, 'cost_usd': round(llm.usage.get('cost_usd', 0), 4),
        'fit_time_s': round(llm_time, 2),
        'used_fallback': llm.llm_output_used_fallback,
        'tp': tp_llm, 'fp_synth': fp_synth_llm, 'fp_hard_neg': fp_hard_llm,
        'precision': round(prec_llm, 4), 'recall': round(rec_llm, 4), 'f1': round(f1_llm, 4),
        'fpr_hard_neg': round(fp_hard_llm/len(hard_negatives), 4) if hard_negatives else 0,
        'reasoning_preview': (llm.llm_reasoning or '')[:400],
    },
}
out_path = REPO / 'results' / f'hard_neg_eval_{label}.json'
out_path.write_text(json.dumps(out, indent=2))
print(f'\nWrote {out_path}')
