"""Fix pipeline for the detector on a real Sepolia background.

Follows up on eval_sepolia_realistic_benigns.py. Given the finding
that both Louvain and GCN produce catastrophic FPR on organic Sepolia
traffic (99.78% / 59.13% respectively) when trained solely on the
synthetic run_benign corpus, this script tries two mitigations:

  APPROACH 1 — Threshold sweep on the L1 detector's probabilities
      Sweep the classification threshold from 0.5 up to 0.999.
      Report recall_attacker vs fpr_background at each threshold.
      Identify the operating point that keeps fpr_real ≤ FPR_BUDGET
      (default 1%) while maximizing attacker recall.

  APPROACH 2 — Retrain L1 with a sampled fraction of the real
      background labeled as benign (holding out the rest for eval).
      This adds distributional information about hub-like benigns
      that the synthetic corpus lacks. Randomly splits unknown
      background 50/50 into train_benign_extra + eval_background;
      re-trains GCN on (synth_benigns + train_benign_extra + attacker);
      evaluates on eval_background.

Both approaches are applied to GCN; Louvain (unsupervised) only gets
approach 1. Reports side-by-side with the naive baseline so the
draft can quantify the improvement.

Usage:
    python scripts/eval_sepolia_detector_fix.py <sepolia_run_dir>
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np

from aml.detectors.baselines import (
    LouvainDetector,
    PerExchangeDetector,
    derive_binary_labels,
)
from aml.detectors.dataset import combine_runs, partial_visibility_split
from aml.detectors.gnn import GCNDetector


REPO_ROOT = Path(__file__).resolve().parents[1]
BENIGN_ROOT = Path.home() / "aml-results" / "batch_2026-06-26" / "benign"
SEED = 42
FPR_BUDGET_DEFAULT = 0.01   # 1%


def federated_scores(det_by_view: dict, views) -> dict[str, float]:
    """Federate per-view predict_proba: an address's score is the MAX
    probability assigned by any view that can see it (paranoid federation,
    matches predict semantics that flag if ANY view flags)."""
    scores: dict[str, float] = {}
    for view in views:
        visible = list(view.visible_addresses)
        sub = det_by_view.detectors[view.name]
        for addr, p in zip(visible, sub.predict_proba(visible)):
            if addr not in scores or p > scores[addr]:
                scores[addr] = float(p)
    return scores


def eval_at_threshold(
    scores: dict[str, float], threshold: float,
    attacker_addrs: set[str], benign_addrs: set[str],
    background_addrs: set[str],
) -> dict:
    tp = sum(1 for a in attacker_addrs if scores.get(a, 0) >= threshold)
    fp_synth = sum(1 for a in benign_addrs if scores.get(a, 0) >= threshold)
    fp_bg = sum(1 for a in background_addrs if scores.get(a, 0) >= threshold)
    return {
        "threshold": threshold,
        "attacker_tp": tp,
        "attacker_recall": round(tp / len(attacker_addrs), 4)
                            if attacker_addrs else 0.0,
        "benign_synth_fp": fp_synth,
        "benign_synth_fpr": round(fp_synth / len(benign_addrs), 6)
                            if benign_addrs else 0.0,
        "background_real_fp": fp_bg,
        "background_real_fpr": round(fp_bg / len(background_addrs), 6)
                                if background_addrs else 0.0,
    }


def load_setup(sepolia_run: Path, num_benigns: int):
    """Combine Sepolia + synth benigns; build views; return everything."""
    benign_dirs = sorted(BENIGN_ROOT.iterdir())[:num_benigns]
    combined = combine_runs([sepolia_run, *benign_dirs])
    g = combined.graph
    bin_labels = derive_binary_labels(combined.node_labels)
    all_addresses = set(g.nodes())
    labeled = set(bin_labels.keys())
    unknown = all_addresses - labeled
    attacker_addrs = {a for a, y in bin_labels.items() if y == 1}
    benign_addrs = {a for a, y in bin_labels.items() if y == 0}
    views = partial_visibility_split(combined, num_exchanges=3, seed=SEED)
    return combined, g, views, bin_labels, attacker_addrs, benign_addrs, unknown


def approach_1_threshold_sweep(
    views, train_labels, attacker_addrs, benign_addrs, background_addrs,
    fpr_budget: float,
) -> dict:
    """Sweep threshold on both GCN and Louvain probability outputs."""
    thresholds = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 0.995, 0.999]
    results: dict = {"approach": "threshold_sweep", "detectors": {}}

    for name, factory in [
        ("louvain", lambda: LouvainDetector()),
        ("gcn",     lambda: GCNDetector(seed=SEED, epochs=50)),
    ]:
        print(f"\n--- {name.upper()} threshold sweep ---")
        det = PerExchangeDetector(detector_factory=factory).fit_per_view(
            views, train_labels,
        )
        scores = federated_scores(det, views)

        rows = [
            eval_at_threshold(scores, t, attacker_addrs, benign_addrs,
                              background_addrs)
            for t in thresholds
        ]

        # Find operating point: max recall with fpr_real ≤ budget
        feasible = [r for r in rows if r["background_real_fpr"] <= fpr_budget]
        best = max(feasible, key=lambda r: r["attacker_recall"], default=None)

        print(f"  {'thr':>6} {'recall':>8} {'fpr_synth':>10} "
              f"{'fpr_real':>10} {'tp':>4} {'fp_bg':>6}")
        for r in rows:
            marker = "  <-- BEST" if r == best else ""
            print(f"  {r['threshold']:>6.3f} {r['attacker_recall']:>8.4f} "
                  f"{r['benign_synth_fpr']:>10.4f} "
                  f"{r['background_real_fpr']:>10.4f} "
                  f"{r['attacker_tp']:>4} {r['background_real_fp']:>6}{marker}")

        results["detectors"][name] = {
            "sweep": rows,
            "best_at_fpr_budget": best,
            "fpr_budget": fpr_budget,
        }

    return results


def approach_2_retrain_with_background(
    combined, views, bin_labels, attacker_addrs, benign_addrs,
    background_addrs, fraction: float = 0.5,
) -> dict:
    """Sample `fraction` of background as extra training benigns; hold out
    the rest as eval-only background for FPR measurement."""
    rng = random.Random(SEED)
    bg_list = sorted(background_addrs)
    rng.shuffle(bg_list)
    n_train = int(len(bg_list) * fraction)
    train_bg_extra = set(bg_list[:n_train])
    eval_bg = set(bg_list[n_train:])

    # New label map: original + extra background as benign (label 0)
    aug_labels = dict(bin_labels)
    for a in train_bg_extra:
        aug_labels[a] = 0

    print(f"\n--- Retraining GCN with {n_train} background addrs as extra benigns ---")
    print(f"    (eval on {len(eval_bg)} held-out background addrs)")

    det = PerExchangeDetector(
        detector_factory=lambda: GCNDetector(seed=SEED, epochs=50)
    ).fit_per_view(views, aug_labels)
    scores = federated_scores(det, views)

    thresholds = [0.5, 0.7, 0.9, 0.95, 0.99]
    rows = [
        eval_at_threshold(scores, t, attacker_addrs, benign_addrs, eval_bg)
        for t in thresholds
    ]

    print(f"  {'thr':>6} {'recall':>8} {'fpr_synth':>10} "
          f"{'fpr_real_bg':>12} {'tp':>4} {'fp_bg':>6}")
    for r in rows:
        print(f"  {r['threshold']:>6.3f} {r['attacker_recall']:>8.4f} "
              f"{r['benign_synth_fpr']:>10.4f} "
              f"{r['background_real_fpr']:>12.4f} "
              f"{r['attacker_tp']:>4} {r['background_real_fp']:>6}")

    return {
        "approach": "retrain_with_background",
        "train_background_fraction": fraction,
        "n_background_train": len(train_bg_extra),
        "n_background_eval": len(eval_bg),
        "sweep": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sepolia_run", type=Path)
    parser.add_argument("--num-benigns", type=int, default=50)
    parser.add_argument("--fpr-budget", type=float, default=FPR_BUDGET_DEFAULT,
                        help="Max acceptable real FPR (default 1%%)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    print(f"Loading Sepolia + {args.num_benigns} benigns...")
    (combined, g, views, bin_labels, attacker_addrs, benign_addrs,
     background_addrs) = load_setup(args.sepolia_run, args.num_benigns)

    print(f"  {g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges")
    print(f"  Attacker: {len(attacker_addrs)}  Benign synth: {len(benign_addrs)}  "
          f"Background: {len(background_addrs)}")
    print(f"  FPR budget for OK operating point: {args.fpr_budget*100:.2f}%")

    print("\n" + "=" * 74)
    print("APPROACH 1 — Threshold sweep")
    print("=" * 74)
    t0 = time.time()
    a1 = approach_1_threshold_sweep(
        views, bin_labels, attacker_addrs, benign_addrs, background_addrs,
        args.fpr_budget,
    )
    a1_time = time.time() - t0

    print("\n" + "=" * 74)
    print("APPROACH 2 — Retrain GCN with background hold-in as benigns")
    print("=" * 74)
    t0 = time.time()
    a2 = approach_2_retrain_with_background(
        combined, views, bin_labels, attacker_addrs, benign_addrs,
        background_addrs, fraction=0.5,
    )
    a2_time = time.time() - t0

    print("\n" + "=" * 74)
    print("FINAL VERDICT")
    print("=" * 74)

    a1_gcn = a1["detectors"]["gcn"].get("best_at_fpr_budget")
    a1_louvain = a1["detectors"]["louvain"].get("best_at_fpr_budget")
    print("Approach 1 (threshold sweep):")
    if a1_gcn:
        print(f"  GCN best:     threshold={a1_gcn['threshold']} "
              f"recall={a1_gcn['attacker_recall']*100:.1f}% "
              f"fpr_real={a1_gcn['background_real_fpr']*100:.4f}%")
    else:
        print(f"  GCN: NO threshold achieves fpr_real ≤ {args.fpr_budget*100}%")
    if a1_louvain:
        print(f"  Louvain best: threshold={a1_louvain['threshold']} "
              f"recall={a1_louvain['attacker_recall']*100:.1f}% "
              f"fpr_real={a1_louvain['background_real_fpr']*100:.4f}%")
    else:
        print(f"  Louvain: NO threshold achieves fpr_real ≤ {args.fpr_budget*100}%")

    print(f"\nApproach 2 (retrain with real bg as benign):")
    best_a2 = max(
        (r for r in a2["sweep"]
         if r["background_real_fpr"] <= args.fpr_budget),
        key=lambda r: r["attacker_recall"], default=None,
    )
    if best_a2:
        print(f"  GCN retrained: threshold={best_a2['threshold']} "
              f"recall={best_a2['attacker_recall']*100:.1f}% "
              f"fpr_real={best_a2['background_real_fpr']*100:.4f}%")
    else:
        print(f"  GCN retrained: NO threshold achieves fpr_real ≤ {args.fpr_budget*100}%")

    results = {
        "sepolia_run": args.sepolia_run.name,
        "fpr_budget": args.fpr_budget,
        "n_attacker": len(attacker_addrs),
        "n_benign_synth": len(benign_addrs),
        "n_background_real": len(background_addrs),
        "approach_1_threshold_sweep": a1,
        "approach_1_time_seconds": round(a1_time, 2),
        "approach_2_retrain_with_background": a2,
        "approach_2_time_seconds": round(a2_time, 2),
    }

    out_json = args.out or (
        REPO_ROOT / "results" /
        f"eval_sepolia_detector_fix_{args.sepolia_run.name}.json"
    )
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_json}")


if __name__ == "__main__":
    main()
