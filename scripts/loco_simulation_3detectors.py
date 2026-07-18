"""LOCO cross-validation on the SIMULATED dataset — all 3 detectors.

Task #13 finalization. Audit v1 showed GCN F1 crashes from 0.967 (seed=42)
to 0.418 (LOCO) on the 20-run pickle. Now we test if the same holds for
Louvain and MultiAgent, and whether MultiAgent's advantage over GCN
SURVIVES the fair LOCO evaluation.

If MultiAgent > GCN survives LOCO → thesis's central claim is intact under
honest evaluation, we just report the LOCO numbers in chapter 5.

If not → we need to either regenerate the simulation with more diversity
OR reframe the claim.

Uses the 35-run pickle (batch_2026-06-26/dataset.pkl, 1078 nodes, 4837
edges, includes all attacker + benign campaigns from the final batch).

For each of the 35 campaigns:
  - Hold out that campaign entirely (all its nodes go to test)
  - Train Louvain, GCN, MultiAgent on the other 34
  - Evaluate F1 per detector on the held-out campaign

Output: results/loco_simulation_3detectors.json

Usage:
    python scripts/loco_simulation_3detectors.py
"""
from __future__ import annotations

import json
import pickle
import statistics
import time
from pathlib import Path

import networkx as nx

from aml.detectors.baselines import (
    LouvainDetector, derive_binary_labels,
)
from aml.detectors.dataset import partial_visibility_split
from aml.detectors.eval import evaluate
from aml.detectors.gnn import GCNDetector
from aml.detectors.multi_agent import MultiAgentDetector


REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PKL = Path.home() / "aml-results" / "batch_2026-06-26" / "dataset.pkl"
OUT_JSON = REPO_ROOT / "results" / "loco_simulation_3detectors.json"
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


def fit_evaluate_one_detector(name, det, combined, train_runs, test_runs, views=None):
    """Fit detector on train_runs, evaluate on test_runs. Returns dict metrics or None."""
    g = combined.graph
    bin_labels = derive_binary_labels(combined.node_labels)
    train_nodes, test_nodes = _split_nodes_by_runs(g, train_runs, test_runs)
    train_labels = {a: bin_labels[a] for a in train_nodes if a in bin_labels}
    test_labels = {a: bin_labels[a] for a in test_nodes if a in bin_labels}
    if len(test_labels) == 0 or len(set(test_labels.values())) < 2:
        return None
    test_addrs = sorted(test_labels.keys())
    y_test = [test_labels[a] for a in test_addrs]

    try:
        if name == "MultiAgent":
            # MultiAgent uses fit_per_view (needs the exchange views), NOT
            # the base Detector.fit which raises RuntimeError on purpose.
            det.fit_per_view(views, train_labels)
        else:
            det.fit(g, train_labels)
        # Some detectors (Louvain) return predict() only, not predict_proba().
        # Fall back to hard predict + treat as proba.
        try:
            proba = det.predict_proba(test_addrs)
        except (AttributeError, NotImplementedError):
            pred_hard = det.predict(test_addrs)
            proba = [float(p) for p in pred_hard]
        pred = [1 if p >= 0.5 else 0 for p in proba]
    except Exception as e:   # noqa: BLE001
        return {"error": str(e)}

    m = evaluate(y_test, pred, proba)
    return {
        "f1": round(m.f1, 4),
        "precision": round(m.precision, 4),
        "recall": round(m.recall, 4),
        "auc": round(m.roc_auc, 4) if m.roc_auc is not None else None,
        "n_test": len(y_test),
        "n_test_pos": sum(y_test),
    }


def main():
    print(f"Loading {DATASET_PKL}...")
    with DATASET_PKL.open("rb") as f:
        d = pickle.load(f)
    combined = d["combined"]
    views_full = d.get("views")   # if the pickle already includes the split
    all_runs = combined.all_run_names
    print(f"  {combined.graph.number_of_nodes():,} nodes / "
          f"{combined.graph.number_of_edges():,} edges / "
          f"{len(all_runs)} runs")
    print()

    results = {"per_detector": {"Louvain": [], "GCN": [], "MultiAgent": []}}

    t_start = time.time()
    for i, held_out in enumerate(all_runs, 1):
        t0 = time.time()
        train_runs = set(all_runs) - {held_out}
        test_runs = {held_out}

        # Recompute views ONLY over the training graph so no test-run leakage
        # Simplest: rebuild the partial visibility split each fold since it's
        # deterministic on the train subgraph seed
        try:
            # Build views over TRAINING graph only (subgraph induced by train nodes)
            train_nodes_all, _ = _split_nodes_by_runs(combined.graph, train_runs, test_runs)
            train_subgraph = combined.graph.subgraph(train_nodes_all).copy()
            from aml.detectors.dataset import CombinedDataset
            train_combined = CombinedDataset(
                graph=train_subgraph,
                node_labels={a: l for a, l in combined.node_labels.items() if a in train_subgraph},
                runs=[r for r in combined.runs
                      if r.meta.get("run_name", str(r.run_dir)) in train_runs],
            )
            views_train = partial_visibility_split(train_combined, num_exchanges=3, seed=SEED)
        except Exception as e:   # noqa: BLE001
            print(f"  [{i:2d}/{len(all_runs)}] {held_out:40s}  ERR building views: {e}")
            continue

        fold_result = {"fold": i, "held_out": held_out, "detectors": {}}

        # Louvain
        try:
            l_det = LouvainDetector(seed=SEED)
            l_m = fit_evaluate_one_detector("Louvain", l_det, combined,
                                            train_runs, test_runs)
            fold_result["detectors"]["Louvain"] = l_m
        except Exception as e:   # noqa: BLE001
            fold_result["detectors"]["Louvain"] = {"error": str(e)}

        # GCN
        try:
            g_det = GCNDetector(seed=SEED)
            g_m = fit_evaluate_one_detector("GCN", g_det, combined,
                                            train_runs, test_runs)
            fold_result["detectors"]["GCN"] = g_m
        except Exception as e:   # noqa: BLE001
            fold_result["detectors"]["GCN"] = {"error": str(e)}

        # MultiAgent
        try:
            ma_det = MultiAgentDetector(seed=SEED)
            ma_m = fit_evaluate_one_detector("MultiAgent", ma_det, combined,
                                             train_runs, test_runs,
                                             views=views_train)
            fold_result["detectors"]["MultiAgent"] = ma_m
        except Exception as e:   # noqa: BLE001
            fold_result["detectors"]["MultiAgent"] = {"error": str(e)}

        for detname in ("Louvain", "GCN", "MultiAgent"):
            m = fold_result["detectors"].get(detname)
            if m and "f1" in m:
                results["per_detector"][detname].append(m["f1"])

        elapsed = time.time() - t0
        parts = []
        for d in ("Louvain", "GCN", "MultiAgent"):
            m = fold_result["detectors"].get(d)
            if m is None or "error" in m:
                parts.append(f"{d}=ERR")
            elif isinstance(m.get("f1"), float):
                parts.append(f"{d}={m['f1']:.3f}")
            else:
                parts.append(f"{d}=?")
        f1_str = " ".join(parts)
        print(f"  [{i:2d}/{len(all_runs)}] {held_out:40s}  {f1_str}  ({elapsed:.1f}s)")

    print()
    print("=" * 80)
    print("LOCO SUMMARY (over 35 folds)")
    print("=" * 80)
    for detname in ("Louvain", "GCN", "MultiAgent"):
        f1s = results["per_detector"][detname]
        if not f1s:
            print(f"  {detname:12s}: NO valid folds")
            continue
        mean_f1 = statistics.mean(f1s)
        std_f1 = statistics.stdev(f1s) if len(f1s) > 1 else 0
        print(f"  {detname:12s}: F1 = {mean_f1:.4f} ± {std_f1:.4f}  "
              f"(min={min(f1s):.3f}, max={max(f1s):.3f}, n={len(f1s)})")
        results.setdefault("summary", {})[detname] = {
            "loco_f1_mean": round(mean_f1, 4),
            "loco_f1_std": round(std_f1, 4),
            "loco_f1_min": round(min(f1s), 4),
            "loco_f1_max": round(max(f1s), 4),
            "n_folds_valid": len(f1s),
        }
    print()
    print(f"Total time: {time.time() - t_start:.1f}s")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, indent=2))
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
