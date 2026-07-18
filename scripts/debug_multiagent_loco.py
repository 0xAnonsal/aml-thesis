"""Debug MultiAgent under LOCO — run ONE fold and print the actual error.

FINDINGS (2026-07-18 audit session):

1. MultiAgentDetector.__init__() does NOT accept `seed=` — it takes
   `detector_factory` (callable returning a fresh local Detector),
   `similarity_threshold`, `prior`. Correct instantiation:

     MultiAgentDetector(
         detector_factory=lambda: GCNDetector(seed=SEED, epochs=50),
     )

   The wrong `seed=SEED` call in loco_simulation_3detectors.py was why
   MultiAgent got 0 valid folds. This debug script exercises the correct
   API and confirms F1=0.947 on one fold.

2. Louvain and GCN both predict "1" for all 20 test nodes with proba=0.5
   under LOCO-hold-out-full-campaign. This is because the test set is
   degenerate (18 pos / 2 neg out of 20 test addresses when we hold out
   one defi-exploit campaign). Both detectors fall back to majority-class
   prediction → identical F1 (this is why loco_simulation_3detectors.py
   reported identical Louvain=GCN F1=0.877 across 11 folds — an artefact
   of imbalance, NOT the detectors actually agreeing on discrimination).

3. Implication: LOCO-by-full-campaign is the WRONG methodology for the
   simulation dataset. Better evaluation would be stratified group k-fold
   with campaign as group — keeps proportional attacker/benign in each
   test fold while preventing same-campaign leakage between train/test.

4. The 400 benign runs all skip under LOCO (their held-out test has no
   positive class — F1 undefined). So the "420 folds" in the simulation
   LOCO effectively becomes 11 valid attacker folds. Small-N eval risk.

Run this before touching loco_simulation_3detectors.py to verify the
MultiAgent fix works on real data.
"""
from __future__ import annotations

import pickle
import traceback
from pathlib import Path

from aml.detectors.baselines import derive_binary_labels
from aml.detectors.dataset import partial_visibility_split, CombinedDataset
from aml.detectors.baselines import LouvainDetector
from aml.detectors.gnn import GCNDetector
from aml.detectors.multi_agent import MultiAgentDetector


DATASET_PKL = Path.home() / "aml-results" / "batch_2026-06-26" / "dataset.pkl"
SEED = 42


def _split_nodes_by_runs(g, train_runs, test_runs):
    train_nodes, test_nodes = set(), set()
    for addr, data in g.nodes(data=True):
        runs = set(data.get("runs", []))
        if runs & test_runs:
            test_nodes.add(addr)
        elif runs & train_runs:
            train_nodes.add(addr)
    return train_nodes, test_nodes


with DATASET_PKL.open("rb") as f:
    d = pickle.load(f)
combined = d["combined"]
all_runs = combined.all_run_names
print(f"Total runs: {len(all_runs)}")

# Pick one attacker fold to test with
held_out = "2026-06-26T13-46-01_defi-exploit_seed53"
train_runs = set(all_runs) - {held_out}
test_runs = {held_out}
print(f"Testing fold: {held_out}")

# Build train subgraph
train_nodes, test_nodes = _split_nodes_by_runs(combined.graph, train_runs, test_runs)
print(f"Train nodes: {len(train_nodes):,}, Test nodes: {len(test_nodes):,}")

train_subgraph = combined.graph.subgraph(train_nodes).copy()
print(f"Train subgraph: {train_subgraph.number_of_nodes():,} nodes / "
      f"{train_subgraph.number_of_edges():,} edges")

train_combined = CombinedDataset(
    graph=train_subgraph,
    node_labels={a: l for a, l in combined.node_labels.items() if a in train_subgraph},
    runs=[r for r in combined.runs
          if r.meta.get("run_name", str(r.run_dir)) in train_runs],
)
print(f"Train combined: {len(train_combined.node_labels)} labels, "
      f"{len(train_combined.runs)} runs")

try:
    views_train = partial_visibility_split(train_combined, num_exchanges=3, seed=SEED)
    print(f"Views built: {len(views_train)}")
    for v in views_train:
        print(f"  {v.name}: {len(v.visible_addresses)} addrs, "
              f"{v.visible_subgraph.number_of_nodes()} sub-nodes / "
              f"{v.visible_subgraph.number_of_edges()} sub-edges")
except Exception as e:
    print(f"FAILED building views: {e}")
    traceback.print_exc()
    raise SystemExit(1)

bin_labels = derive_binary_labels(combined.node_labels)
train_labels = {a: bin_labels[a] for a in train_nodes if a in bin_labels}
print(f"Train labels: {len(train_labels)} "
      f"(pos={sum(train_labels.values())}, neg={len(train_labels) - sum(train_labels.values())})")

test_labels = {a: bin_labels[a] for a in test_nodes if a in bin_labels}
print(f"Test labels: {len(test_labels)} "
      f"(pos={sum(test_labels.values())}, neg={len(test_labels) - sum(test_labels.values())})")

print("\n=== TESTING Louvain + GCN separately (to check they differ) ===")
test_addrs = sorted(test_labels.keys())
y_test = [test_labels[a] for a in test_addrs]

louv = LouvainDetector(seed=SEED)
louv.fit(train_subgraph, train_labels)
louv_pred = louv.predict(test_addrs)
print(f"  Louvain preds: {louv_pred}")

gcn = GCNDetector(seed=SEED)
gcn.fit(train_subgraph, train_labels)
gcn_pred = gcn.predict(test_addrs)
gcn_proba = list(gcn.predict_proba(test_addrs))
print(f"  GCN preds: {gcn_pred}")
print(f"  GCN proba: {[round(p, 3) for p in gcn_proba]}")

identical = louv_pred == gcn_pred
print(f"  LOUVAIN == GCN preds? {identical}")

print("\n=== TESTING MultiAgentDetector.fit_per_view() (with correct init) ===")
try:
    # MultiAgent needs detector_factory, NOT seed directly
    det = MultiAgentDetector(
        detector_factory=lambda: GCNDetector(seed=SEED, epochs=50),
    )
    print(f"  Detector created: {type(det).__name__}")
    result = det.fit_per_view(views_train, train_labels)
    print(f"  fit_per_view returned: {type(result).__name__}")

    test_addrs = sorted(test_labels.keys())
    print(f"  Predicting on {len(test_addrs)} test addresses...")
    pred = det.predict(test_addrs)
    print(f"  predict() returned {len(pred)} predictions")
    print(f"  Sample: pred[:5]={pred[:5]}")

    try:
        proba = det.predict_proba(test_addrs)
        print(f"  predict_proba() returned {len(proba)} probas")
        print(f"  Sample: proba[:5]={list(proba)[:5]}")
    except Exception as e:
        print(f"  predict_proba FAILED: {e}")

    # Compute F1
    from aml.detectors.eval import evaluate
    y_test = [test_labels[a] for a in test_addrs]
    try:
        proba = det.predict_proba(test_addrs)
    except Exception:
        proba = [float(p) for p in pred]
    pred_binary = [1 if p >= 0.5 else 0 for p in proba]
    m = evaluate(y_test, pred_binary, proba)
    print(f"\n  RESULT: F1={m.f1:.4f}  P={m.precision:.4f}  R={m.recall:.4f}")

except Exception as e:
    print(f"\nFAILED: {e}")
    traceback.print_exc()
