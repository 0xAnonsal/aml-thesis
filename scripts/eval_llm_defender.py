"""Evaluate LLMDefenderCoordinator vs MultiAgentDetector (cosine baseline).

Task #15 follow-up. Runs both detectors on the same simulated dataset under
identical partial-visibility split, compares:
  1. Binary F1 (same PerExchangeDetector under both → expected identical)
  2. ARI actor clustering (this is where LLM defender should win)
  3. Cluster count / homogeneity / completeness
  4. LLM reasoning text (qualitative for chapter 5)

Cost estimate (per full-dataset eval):
  - Haiku 4.5:  ~$0.005 (default — cheap iteration)
  - Sonnet 4.6: ~$0.05  (headline runs)
  - Opus 4.7:   ~$0.25  (qualitative demo)

Usage:
    python scripts/eval_llm_defender.py                    # default Haiku
    python scripts/eval_llm_defender.py --model sonnet
    python scripts/eval_llm_defender.py --model opus

Writes: results/eval_llm_defender_<model>.json
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import time
from pathlib import Path

from aml.detectors.baselines import derive_binary_labels
from aml.detectors.dataset import partial_visibility_split
from aml.detectors.gnn import GCNDetector
from aml.detectors.multi_agent import (
    LLMDefenderCoordinator,
    MultiAgentDetector,
    actor_clustering_metrics,
    true_actor_clusters,
)
from aml.utils.env import load_dotenv_if_present


REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PKL = Path.home() / "aml-results" / "batch_2026-06-26" / "dataset.pkl"
SEED = 42


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="haiku",
                        choices=["haiku", "sonnet", "opus"],
                        help="LLM model for defender coordinator")
    parser.add_argument("--pickle", type=Path, default=DATASET_PKL,
                        help="Path to combined dataset pickle")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output JSON path (default: results/eval_llm_defender_<model>.json)")
    parser.add_argument("--num-exchanges", type=int, default=3,
                        help="Number of exchange partitions")
    args = parser.parse_args()

    out_json = args.out or (REPO_ROOT / "results" / f"eval_llm_defender_{args.model}.json")

    # Load env (needs ANTHROPIC_API_KEY)
    load_dotenv_if_present()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY not set in environment. "
            "Add it to .env at the repo root."
        )

    print(f"Loading {args.pickle}...")
    with args.pickle.open("rb") as f:
        d = pickle.load(f)
    combined = d["combined"]
    g = combined.graph
    print(f"  {g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges / "
          f"{len(combined.all_run_names)} runs")

    # Ground truth: one actor cluster per attacker run; benigns are singletons
    true_clusters = true_actor_clusters(combined.runs)
    n_actors_true = len(set(true_clusters.values()))
    print(f"  True actor clusters: {n_actors_true} "
          f"(one per attacker run + benign singletons)")

    # Partial visibility split (3 exchanges by default)
    views = partial_visibility_split(
        combined, num_exchanges=args.num_exchanges, seed=SEED,
    )
    print(f"  Built {len(views)} exchange views:")
    for v in views:
        print(f"    {v.name}: {len(v.visible_addresses)} addresses, "
              f"{v.visible_subgraph.number_of_edges()} edges")

    # Binary labels for training (drops contracts/infra/unknown)
    bin_labels = derive_binary_labels(combined.node_labels)
    train_labels = {a: bin_labels[a] for a in bin_labels}
    print(f"  Training labels: {len(train_labels):,} "
          f"({sum(train_labels.values()):,} attackers, "
          f"{len(train_labels) - sum(train_labels.values()):,} benigns)")
    print()

    results: dict = {
        "dataset": str(args.pickle),
        "num_exchanges": args.num_exchanges,
        "seed": SEED,
        "llm_model": args.model,
        "n_graph_nodes": int(g.number_of_nodes()),
        "n_graph_edges": int(g.number_of_edges()),
        "n_runs": len(combined.all_run_names),
        "n_true_actor_clusters": n_actors_true,
    }

    # --- Baseline: MultiAgentDetector (cosine-similarity coordinator) ---
    print("=" * 78)
    print("BASELINE: MultiAgentDetector (cosine-similarity clustering)")
    print("=" * 78)
    t0 = time.time()
    ma_baseline = MultiAgentDetector(
        detector_factory=lambda: GCNDetector(seed=SEED, epochs=50),
    )
    ma_baseline.fit_per_view(views, train_labels)
    ma_elapsed = time.time() - t0
    ma_metrics = actor_clustering_metrics(true_clusters, ma_baseline.actor_clusters)
    print(f"  Fit time: {ma_elapsed:.1f}s")
    print(f"  n_clusters_pred: {ma_metrics['n_clusters_pred']}")
    print(f"  ARI:          {ma_metrics['ari']}")
    print(f"  Homogeneity:  {ma_metrics['homogeneity']}")
    print(f"  Completeness: {ma_metrics['completeness']}")
    results["baseline_cosine"] = {
        "fit_time_seconds": round(ma_elapsed, 2),
        **{k: (round(v, 4) if isinstance(v, float) else v)
           for k, v in ma_metrics.items()},
    }
    print()

    # --- Novel: LLMDefenderCoordinator ---
    print("=" * 78)
    print(f"NOVEL: LLMDefenderCoordinator (model={args.model})")
    print("=" * 78)
    t0 = time.time()
    llm_det = LLMDefenderCoordinator(
        detector_factory=lambda: GCNDetector(seed=SEED, epochs=50),
        llm_model=args.model,
    )
    llm_det.fit_per_view(views, train_labels)
    llm_elapsed = time.time() - t0
    llm_metrics = actor_clustering_metrics(true_clusters, llm_det.actor_clusters)
    print(f"  Fit time: {llm_elapsed:.1f}s")
    print(f"  n_clusters_pred: {llm_metrics['n_clusters_pred']}")
    print(f"  ARI:          {llm_metrics['ari']}")
    print(f"  Homogeneity:  {llm_metrics['homogeneity']}")
    print(f"  Completeness: {llm_metrics['completeness']}")
    print(f"  LLM cost:     ${llm_det.usage.get('cost_usd', 0):.4f}")
    print(f"  LLM tokens:   in={llm_det.usage.get('input_tokens', 0)}, "
          f"out={llm_det.usage.get('output_tokens', 0)}")
    print(f"  Used fallback: {llm_det.llm_output_used_fallback}")
    print(f"\n  LLM reasoning (first 500 chars):\n"
          f"  {llm_det.llm_reasoning[:500]}"
          + ("..." if len(llm_det.llm_reasoning) > 500 else ""))
    results["llm_defender"] = {
        "model": args.model,
        "fit_time_seconds": round(llm_elapsed, 2),
        "cost_usd": round(llm_det.usage.get("cost_usd", 0.0), 4),
        "input_tokens": llm_det.usage.get("input_tokens", 0),
        "output_tokens": llm_det.usage.get("output_tokens", 0),
        "used_fallback": llm_det.llm_output_used_fallback,
        "reasoning": llm_det.llm_reasoning,
        **{k: (round(v, 4) if isinstance(v, float) else v)
           for k, v in llm_metrics.items()},
    }
    print()

    # --- Head-to-head verdict ---
    print("=" * 78)
    print("HEAD-TO-HEAD (actor clustering ARI)")
    print("=" * 78)
    ari_baseline = ma_metrics.get("ari") or 0.0
    ari_llm = llm_metrics.get("ari") or 0.0
    diff = ari_llm - ari_baseline
    winner = ("LLM wins" if diff > 0.01
              else "Cosine wins" if diff < -0.01
              else "Tie")
    print(f"  Cosine ARI: {ari_baseline:.4f}")
    print(f"  LLM ARI:    {ari_llm:.4f}")
    print(f"  Diff:       {diff:+.4f}  ({winner})")
    results["head_to_head"] = {
        "baseline_ari": round(ari_baseline, 4),
        "llm_ari": round(ari_llm, 4),
        "diff": round(diff, 4),
        "verdict": winner,
    }
    print()

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(results, indent=2))
    print(f"Wrote {out_json}")


if __name__ == "__main__":
    main()
