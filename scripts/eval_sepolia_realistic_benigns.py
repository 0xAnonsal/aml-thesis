"""Realistic evaluation of the defender on a Sepolia campaign.

Answers the operational question that the §5.3 synthetic benign corpus
cannot: "how many REAL Sepolia users would our detector wrongly flag
as launderers?"

The Sepolia campaign's chain_trace.jsonl captures every transaction
in the campaign's block window — not only our attacker's tx (~35-140)
but also all background traffic from other Sepolia users (~5-15k tx).
Those background addresses appear in the graph as `unknown` label
(no ground truth). This script:

  1. Trains the detector on the synthetic labeled corpus (400 benigns
     from ~/aml-results/batch_2026-06-26/benign/).
  2. Predicts on the Sepolia graph = our attacker addresses (labeled)
     + all background addresses (unlabeled).
  3. Reports:
        - Recall on our attacker addresses (should be high — like §5.3)
        - False Positive Count on unknown background addresses
          (= our REAL false-positive rate against organic traffic)
        - Contrast with the FPR on the synthetic benign corpus (§5.3)

Usage:
    python scripts/eval_sepolia_realistic_benigns.py <sepolia_run_dir>
    python scripts/eval_sepolia_realistic_benigns.py \\
        results/sepolia_campaign/2026-08-11T18-02-38_defi-exploit_seed100_sepolia
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

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


def analyse(sepolia_run: Path, num_benigns: int) -> dict:
    print(f"Loading {num_benigns} benign campaigns for training...")
    benign_dirs = sorted(BENIGN_ROOT.iterdir())[:num_benigns]
    if len(benign_dirs) < num_benigns:
        print(f"  WARN: only {len(benign_dirs)} available")

    print(f"Combining Sepolia run + benigns...")
    combined = combine_runs([sepolia_run, *benign_dirs])
    g = combined.graph
    print(f"  {g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges")

    # Labels: labeled = attacker (from Sepolia) + benign (from synthetic)
    # Unknown = organic Sepolia background addresses
    bin_labels = derive_binary_labels(combined.node_labels)
    all_addresses = set(g.nodes())
    labeled_addresses = set(bin_labels.keys())
    unknown_addresses = all_addresses - labeled_addresses

    attacker_addrs = {a for a, y in bin_labels.items() if y == 1}
    benign_addrs = {a for a, y in bin_labels.items() if y == 0}

    print(f"  Labeled attacker addrs (from Sepolia):    {len(attacker_addrs)}")
    print(f"  Labeled benign addrs (from synth corpus): {len(benign_addrs)}")
    print(f"  UNKNOWN background addrs (Sepolia only):  {len(unknown_addresses)}")
    print()

    views = partial_visibility_split(combined, num_exchanges=3, seed=SEED)
    print(f"  Built 3 exchange views")
    for v in views:
        print(f"    {v.name}: {len(v.visible_addresses)} addrs")
    print()

    # Train two L1 detectors
    results: dict = {
        "sepolia_run": sepolia_run.name,
        "num_benigns_training": len(benign_dirs),
        "n_nodes_total": g.number_of_nodes(),
        "n_edges_total": g.number_of_edges(),
        "n_attacker_labeled": len(attacker_addrs),
        "n_benign_labeled": len(benign_addrs),
        "n_background_unknown": len(unknown_addresses),
        "configs": {},
    }

    for name, factory in [
        ("louvain", lambda: LouvainDetector()),
        ("gcn", lambda: GCNDetector(seed=SEED, epochs=50)),
    ]:
        print("=" * 74)
        print(f"Config: {name} — L1 supervised on labeled train set")
        print("=" * 74)
        t0 = time.time()
        det = PerExchangeDetector(detector_factory=factory).fit_per_view(
            views, bin_labels,
        )
        fit_time = time.time() - t0

        # Predict per-view; federate = flag if any view flags
        flagged: set[str] = set()
        for view in views:
            visible = list(view.visible_addresses)
            sub = det.detectors[view.name]
            for addr, y in zip(visible, sub.predict(visible)):
                if y == 1:
                    flagged.add(addr)

        # Recall on attacker addresses
        tp_attacker = sum(1 for a in attacker_addrs if a in flagged)
        recall_attacker = (
            tp_attacker / len(attacker_addrs) if attacker_addrs else 0.0
        )

        # False positives on labeled benigns (synthetic baseline)
        fp_benign = sum(1 for a in benign_addrs if a in flagged)
        fpr_benign = fp_benign / len(benign_addrs) if benign_addrs else 0.0

        # False positives on UNKNOWN background (real Sepolia traffic)
        fp_bg = sum(1 for a in unknown_addresses if a in flagged)
        fpr_bg = fp_bg / len(unknown_addresses) if unknown_addresses else 0.0

        print(f"  Fit: {fit_time:.1f}s")
        print(f"  ATTACKER recall:     {tp_attacker}/{len(attacker_addrs)}  "
              f"= {recall_attacker*100:.2f}%")
        print(f"  BENIGN synth FPR:    {fp_benign}/{len(benign_addrs)}  "
              f"= {fpr_benign*100:.4f}%")
        print(f"  BACKGROUND real FPR: {fp_bg}/{len(unknown_addresses)}  "
              f"= {fpr_bg*100:.4f}%  <-- THE OPERATIONAL METRIC")
        print()

        results["configs"][name] = {
            "fit_time_seconds": round(fit_time, 2),
            "attacker_recall": round(recall_attacker, 4),
            "attacker_tp": tp_attacker,
            "attacker_total": len(attacker_addrs),
            "benign_synth_fpr": round(fpr_benign, 6),
            "benign_synth_fp": fp_benign,
            "benign_synth_total": len(benign_addrs),
            "background_real_fpr": round(fpr_bg, 6),
            "background_real_fp": fp_bg,
            "background_real_total": len(unknown_addresses),
        }

    print("=" * 74)
    print("SUMMARY — realistic Sepolia evaluation")
    print("=" * 74)
    print(f"  {'Detector':<12} {'Attacker Recall':>18} "
          f"{'Synth FPR':>12} {'Real Sepolia FPR':>20}")
    for name, e in results["configs"].items():
        print(f"  {name:<12} "
              f"{e['attacker_tp']:>3}/{e['attacker_total']:<3} "
              f"({e['attacker_recall']*100:>5.1f}%) "
              f"{e['benign_synth_fpr']*100:>10.4f}% "
              f"{e['background_real_fpr']*100:>18.4f}%")

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sepolia_run", type=Path,
                        help="Path to a Sepolia campaign run directory")
    parser.add_argument("--num-benigns", type=int, default=50,
                        help="Synthetic benigns for training (default 50)")
    parser.add_argument("--out", type=Path, default=None,
                        help="JSON output path (default: results/eval_sepolia_realistic_<run>.json)")
    args = parser.parse_args()

    if not (args.sepolia_run / "campaign.json").exists():
        raise SystemExit(f"missing {args.sepolia_run}/campaign.json")

    out_json = args.out or (
        REPO_ROOT / "results" /
        f"eval_sepolia_realistic_{args.sepolia_run.name}.json"
    )

    rep = analyse(args.sepolia_run, args.num_benigns)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, indent=2))
    print(f"\nWrote {out_json}")


if __name__ == "__main__":
    main()
