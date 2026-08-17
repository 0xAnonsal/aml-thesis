"""Binary detection analysis over seed 403 — did we catch the launderer?

Complements eval_defender_seed403_comparison.py which reports ARI
(clustering quality). This script reports the operational question:
of the 53 attacker addresses in seed 403, how many did each detector
flag as illicit? Reports precision / recall / F1 for the binary task
+ inspects which LLM clusters contain the attacker addresses.

Usage:
    python scripts/eval_detection_seed403.py
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path

from aml.detectors.baselines import LouvainDetector, derive_binary_labels
from aml.detectors.dataset import combine_runs, partial_visibility_split
from aml.detectors.gnn import GCNDetector
from aml.detectors.multi_agent import (
    LLMDefenderCoordinator,
    MultiAgentDetector,
)
from aml.env import PriceOracle, resolve_campaign_ts
from aml.utils.env import load_dotenv_if_present


REPO_ROOT = Path(__file__).resolve().parents[1]
PRICE_CACHE = REPO_ROOT / "data" / "prices"
SEED = 42

ATTACKER_RUN = REPO_ROOT / "results" / "anvil" / "2026-08-16T20-20-49_defi-exploit_seed403"
BENIGN_ROOT = Path.home() / "aml-results" / "batch_2026-06-26" / "benign"


def prf1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return prec, rec, f1


def measure_l1_detection(det, views, bin_labels: dict[str, int],
                         attacker_addrs: set[str]) -> dict:
    """Sum per-view predictions on the 53 attacker addresses.

    A wallet is 'flagged' if ANY exchange view flags it. This matches
    the federated detection semantics: any KYC-owning exchange
    detecting the wallet is enough to trigger.
    """
    flagged_addrs: set[str] = set()
    per_view: dict[str, dict] = {}

    for view in views:
        visible = list(view.visible_addresses)
        # get the trained sub-detector for this view (via PerExchangeDetector)
        sub = det._binary.detectors[view.name]
        preds = sub.predict(visible)
        view_flagged: set[str] = set()
        for addr, y in zip(visible, preds):
            if y == 1:
                view_flagged.add(addr)
                flagged_addrs.add(addr)

        # Per-view F1 on the intersection of view addrs with labeled set
        labeled_in_view = [a for a in visible if a in bin_labels]
        tp = sum(1 for a in labeled_in_view
                 if a in view_flagged and bin_labels[a] == 1)
        fp = sum(1 for a in labeled_in_view
                 if a in view_flagged and bin_labels[a] == 0)
        fn = sum(1 for a in labeled_in_view
                 if a not in view_flagged and bin_labels[a] == 1)
        prec, rec, f1 = prf1(tp, fp, fn)
        per_view[view.name] = {
            "n_addresses_visible": len(visible),
            "n_flagged": len(view_flagged),
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "attacker_addrs_in_view": sum(1 for a in labeled_in_view
                                          if bin_labels[a] == 1),
        }

    # Federation-level: TP = attacker addrs flagged by AT LEAST ONE view
    tp = sum(1 for a in attacker_addrs if a in flagged_addrs)
    fp = sum(1 for a in flagged_addrs
             if a in bin_labels and bin_labels[a] == 0)
    fn = sum(1 for a in attacker_addrs if a not in flagged_addrs)
    prec, rec, f1 = prf1(tp, fp, fn)

    return {
        "federation_tp": tp,
        "federation_fp": fp,
        "federation_fn": fn,
        "federation_precision": round(prec, 4),
        "federation_recall": round(rec, 4),
        "federation_f1": round(f1, 4),
        "per_view": per_view,
    }


def inspect_llm_clustering(det, attacker_addrs: set[str]) -> dict:
    """Which LLM clusters contain the 53 attacker addresses?"""
    addr_to_cluster = det.actor_clusters
    cluster_ids_of_attackers = [addr_to_cluster.get(a) for a in attacker_addrs
                                if a in addr_to_cluster]
    cluster_counter = Counter(cluster_ids_of_attackers)

    # For each cluster containing an attacker, how many attacker vs benign?
    cluster_composition = {}
    for cid in cluster_counter:
        members = [a for a, c in addr_to_cluster.items() if c == cid]
        n_att = sum(1 for a in members if a in attacker_addrs)
        cluster_composition[str(cid)] = {
            "size": len(members),
            "attackers": n_att,
            "purity": round(n_att / len(members), 4) if members else 0.0,
        }

    return {
        "n_attacker_addrs_assigned": len(cluster_ids_of_attackers),
        "n_clusters_touched_by_attackers": len(cluster_counter),
        "top_clusters_by_attacker_count": dict(cluster_counter.most_common(5)),
        "cluster_composition": cluster_composition,
    }


def main() -> None:
    load_dotenv_if_present()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set")

    out_json = REPO_ROOT / "results" / "eval_detection_seed403.json"

    benign_dirs = sorted(BENIGN_ROOT.iterdir())[:50]
    run_dirs = [ATTACKER_RUN, *benign_dirs]
    print(f"Combining {len(run_dirs)} runs...")
    combined = combine_runs(run_dirs)

    views = partial_visibility_split(combined, num_exchanges=3, seed=SEED)
    bin_labels = derive_binary_labels(combined.node_labels)
    train_labels = {a: bin_labels[a] for a in bin_labels}
    attacker_addrs = {a for a, y in bin_labels.items() if y == 1}

    print(f"  {combined.graph.number_of_nodes():,} nodes, "
          f"{combined.graph.number_of_edges():,} edges")
    print(f"  Attacker addresses (ground truth): {len(attacker_addrs)}")
    print(f"  Benign addresses (labeled):        "
          f"{len(bin_labels) - len(attacker_addrs)}")
    print()

    oracle = PriceOracle(cache_dir=PRICE_CACHE)
    campaign_ts = resolve_campaign_ts(oracle, None)

    results: dict = {
        "attacker_run": ATTACKER_RUN.name,
        "num_benigns": len(benign_dirs),
        "n_attacker_addresses_ground_truth": len(attacker_addrs),
        "n_benign_addresses_labeled": len(bin_labels) - len(attacker_addrs),
        "configs": {},
    }

    # Config 1: Louvain L1
    print("=" * 78)
    print("Config 1: Louvain (unsupervised L1)")
    print("=" * 78)
    t0 = time.time()
    det = MultiAgentDetector(detector_factory=lambda: LouvainDetector())
    det.fit_per_view(views, train_labels)
    m = measure_l1_detection(det, views, bin_labels, attacker_addrs)
    m["fit_time"] = round(time.time() - t0, 2)
    print(f"  Federation: TP={m['federation_tp']}/{len(attacker_addrs)} "
          f"FP={m['federation_fp']} FN={m['federation_fn']}")
    print(f"  Precision={m['federation_precision']}  "
          f"Recall={m['federation_recall']}  F1={m['federation_f1']}")
    results["configs"]["louvain"] = m
    print()

    # Config 2: GCN L1
    print("=" * 78)
    print("Config 2: GCN (supervised L1)")
    print("=" * 78)
    t0 = time.time()
    det = MultiAgentDetector(
        detector_factory=lambda: GCNDetector(seed=SEED, epochs=50),
    )
    det.fit_per_view(views, train_labels)
    m = measure_l1_detection(det, views, bin_labels, attacker_addrs)
    m["fit_time"] = round(time.time() - t0, 2)
    print(f"  Federation: TP={m['federation_tp']}/{len(attacker_addrs)} "
          f"FP={m['federation_fp']} FN={m['federation_fn']}")
    print(f"  Precision={m['federation_precision']}  "
          f"Recall={m['federation_recall']}  F1={m['federation_f1']}")
    results["configs"]["gcn"] = m
    print()

    # Config 3: GCN + LLM Opus (best LLM for cluster inspection)
    print("=" * 78)
    print("Config 3: GCN L1 + LLM Opus L2 (cluster inspection)")
    print("=" * 78)
    t0 = time.time()
    llm = LLMDefenderCoordinator(
        detector_factory=lambda: GCNDetector(seed=SEED, epochs=50),
        llm_model="opus",
        oracle=oracle,
        campaign_ts=campaign_ts,
    )
    llm.fit_per_view(views, train_labels)
    elapsed = time.time() - t0
    m = measure_l1_detection(llm, views, bin_labels, attacker_addrs)
    m["fit_time"] = round(elapsed, 2)
    m["cost_usd"] = round(llm.usage.get("cost_usd", 0.0), 4)
    m["llm_reasoning"] = llm.llm_reasoning
    m["clustering"] = inspect_llm_clustering(llm, attacker_addrs)
    print(f"  Federation (L1 GCN): TP={m['federation_tp']}/{len(attacker_addrs)} "
          f"FP={m['federation_fp']} FN={m['federation_fn']}")
    print(f"  Precision={m['federation_precision']}  "
          f"Recall={m['federation_recall']}  F1={m['federation_f1']}")
    print(f"  Cost: ${m['cost_usd']}")
    print(f"  LLM clustering:")
    c = m["clustering"]
    print(f"    Attacker addrs assigned to a cluster: "
          f"{c['n_attacker_addrs_assigned']}/{len(attacker_addrs)}")
    print(f"    Distinct clusters touched by attackers: "
          f"{c['n_clusters_touched_by_attackers']}")
    print(f"    Top clusters by attacker count: "
          f"{c['top_clusters_by_attacker_count']}")
    results["configs"]["gcn+llm_opus"] = m
    print()

    # Final summary table
    print("=" * 78)
    print(f"DETECTION SUMMARY — did we catch the launderer? "
          f"(over {len(attacker_addrs)} attacker addrs)")
    print("=" * 78)
    print(f"  {'Config':<20} {'TP':>6} {'FP':>6} {'FN':>6} "
          f"{'Prec':>8} {'Recall':>8} {'F1':>8}")
    for name, m in results["configs"].items():
        print(f"  {name:<20} {m['federation_tp']:>6} {m['federation_fp']:>6} "
              f"{m['federation_fn']:>6} {m['federation_precision']:>8.4f} "
              f"{m['federation_recall']:>8.4f} {m['federation_f1']:>8.4f}")

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_json}")


if __name__ == "__main__":
    main()
