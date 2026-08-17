"""Evaluate LLM Defender coordinator over the seed 403 headline run.

Ad-hoc script for TFM §5.6.5 refinement paragraph: takes the refined
10-ETH attacker campaign (seed 403, 92% recovery, 24 burners, 27 clean
exits across Binance/Coinbase/Kraken) plus a matched pool of 50 benign
campaigns, builds a mini combined dataset, runs the partial-visibility
split, and evaluates both detectors (cosine baseline + LLM Sonnet)
against it.

Purpose: quantify detector performance against the *refined* attacker
specifically, not the aggregate 20-campaign batch reported in §5.3.

Cost estimate: ~$0.05 Sonnet (single prompt, 1 attacker cluster).

Usage:
    python scripts/eval_llm_defender_seed403.py --model sonnet
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from aml.detectors.baselines import derive_binary_labels
from aml.detectors.dataset import combine_runs, partial_visibility_split
from aml.detectors.gnn import GCNDetector
from aml.detectors.multi_agent import (
    LLMDefenderCoordinator,
    MultiAgentDetector,
    actor_clustering_metrics,
    true_actor_clusters,
)
from aml.env import PriceOracle, resolve_campaign_ts
from aml.utils.env import load_dotenv_if_present


REPO_ROOT = Path(__file__).resolve().parents[1]
PRICE_CACHE = REPO_ROOT / "data" / "prices"
SEED = 42

ATTACKER_RUN = REPO_ROOT / "results" / "anvil" / "2026-08-16T20-20-49_defi-exploit_seed403"
BENIGN_ROOT = Path.home() / "aml-results" / "batch_2026-06-26" / "benign"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="sonnet",
                        choices=["haiku", "sonnet", "opus"])
    parser.add_argument("--num-benigns", type=int, default=50,
                        help="How many benign campaigns to include")
    parser.add_argument("--num-exchanges", type=int, default=3)
    args = parser.parse_args()

    load_dotenv_if_present()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set")

    out_json = REPO_ROOT / "results" / f"eval_llm_defender_seed403_{args.model}.json"

    # Collect run dirs: seed 403 attacker + N benigns
    if not ATTACKER_RUN.is_dir():
        raise SystemExit(f"missing attacker run {ATTACKER_RUN}")

    benign_dirs = sorted(BENIGN_ROOT.iterdir())[:args.num_benigns]
    if len(benign_dirs) < args.num_benigns:
        print(f"WARN: only {len(benign_dirs)} benigns found "
              f"(requested {args.num_benigns})")

    run_dirs = [ATTACKER_RUN, *benign_dirs]
    print(f"Combining {len(run_dirs)} runs "
          f"(1 attacker seed 403 + {len(benign_dirs)} benigns)...")
    combined = combine_runs(run_dirs)
    g = combined.graph
    print(f"  {g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges")

    true_clusters = true_actor_clusters(combined.runs)
    n_actors_true = len(set(true_clusters.values()))
    print(f"  True actor clusters: {n_actors_true}")

    views = partial_visibility_split(
        combined, num_exchanges=args.num_exchanges, seed=SEED,
    )
    print(f"  Built {len(views)} exchange views:")
    for v in views:
        print(f"    {v.name}: {len(v.visible_addresses)} addr, "
              f"{v.visible_subgraph.number_of_edges()} edges")

    bin_labels = derive_binary_labels(combined.node_labels)
    train_labels = {a: bin_labels[a] for a in bin_labels}
    n_att = sum(train_labels.values())
    print(f"  Training labels: {len(train_labels):,} "
          f"({n_att:,} attackers, {len(train_labels) - n_att:,} benigns)")
    print()

    results: dict = {
        "attacker_run": str(ATTACKER_RUN.name),
        "num_benigns": len(benign_dirs),
        "num_exchanges": args.num_exchanges,
        "seed": SEED,
        "llm_model": args.model,
        "n_graph_nodes": int(g.number_of_nodes()),
        "n_graph_edges": int(g.number_of_edges()),
        "n_true_actor_clusters": n_actors_true,
        "n_attacker_addresses": int(n_att),
    }

    # --- Baseline cosine ---
    print("=" * 78)
    print("BASELINE: MultiAgentDetector (cosine)")
    print("=" * 78)
    t0 = time.time()
    ma = MultiAgentDetector(detector_factory=lambda: GCNDetector(seed=SEED, epochs=50))
    ma.fit_per_view(views, train_labels)
    ma_elapsed = time.time() - t0
    ma_metrics = actor_clustering_metrics(true_clusters, ma.actor_clusters)
    print(f"  Fit: {ma_elapsed:.1f}s  clusters_pred={ma_metrics['n_clusters_pred']}")
    print(f"  ARI={ma_metrics['ari']}  Hom={ma_metrics['homogeneity']}  "
          f"Comp={ma_metrics['completeness']}")
    results["baseline_cosine"] = {
        "fit_time_seconds": round(ma_elapsed, 2),
        **{k: (round(v, 4) if isinstance(v, float) else v)
           for k, v in ma_metrics.items()},
    }
    print()

    # --- LLM defender ---
    print("=" * 78)
    print(f"NOVEL: LLMDefenderCoordinator ({args.model})")
    print("=" * 78)
    oracle = PriceOracle(cache_dir=PRICE_CACHE)
    campaign_ts = resolve_campaign_ts(oracle, None)
    print(f"  Market: campaign_ts={campaign_ts.isoformat()}  "
          f"ETH=${oracle.price('eth', campaign_ts):,.2f}")

    t0 = time.time()
    llm = LLMDefenderCoordinator(
        detector_factory=lambda: GCNDetector(seed=SEED, epochs=50),
        llm_model=args.model,
        oracle=oracle,
        campaign_ts=campaign_ts,
    )
    llm.fit_per_view(views, train_labels)
    llm_elapsed = time.time() - t0
    llm_metrics = actor_clustering_metrics(true_clusters, llm.actor_clusters)
    print(f"  Fit: {llm_elapsed:.1f}s  clusters_pred={llm_metrics['n_clusters_pred']}")
    print(f"  ARI={llm_metrics['ari']}  Hom={llm_metrics['homogeneity']}  "
          f"Comp={llm_metrics['completeness']}")
    print(f"  Cost: ${llm.usage.get('cost_usd', 0):.4f}  "
          f"in={llm.usage.get('input_tokens', 0)}  "
          f"out={llm.usage.get('output_tokens', 0)}")
    print(f"  Used fallback: {llm.llm_output_used_fallback}")
    print(f"\n  Reasoning (first 400 chars):\n  {llm.llm_reasoning[:400]}"
          + ("..." if len(llm.llm_reasoning) > 400 else ""))
    results["llm_defender"] = {
        "model": args.model,
        "fit_time_seconds": round(llm_elapsed, 2),
        "cost_usd": round(llm.usage.get("cost_usd", 0.0), 4),
        "input_tokens": llm.usage.get("input_tokens", 0),
        "output_tokens": llm.usage.get("output_tokens", 0),
        "used_fallback": llm.llm_output_used_fallback,
        "reasoning": llm.llm_reasoning,
        **{k: (round(v, 4) if isinstance(v, float) else v)
           for k, v in llm_metrics.items()},
    }
    print()

    print("=" * 78)
    print("HEAD-TO-HEAD (ARI over seed 403 + benigns)")
    print("=" * 78)
    ari_b = ma_metrics.get("ari") or 0.0
    ari_l = llm_metrics.get("ari") or 0.0
    diff = ari_l - ari_b
    winner = ("LLM wins" if diff > 0.01
              else "Cosine wins" if diff < -0.01
              else "Tie")
    print(f"  Cosine ARI: {ari_b:.4f}")
    print(f"  LLM ARI:    {ari_l:.4f}")
    print(f"  Diff:       {diff:+.4f}  ({winner})")
    results["head_to_head"] = {
        "baseline_ari": round(ari_b, 4),
        "llm_ari": round(ari_l, 4),
        "diff": round(diff, 4),
        "verdict": winner,
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_json}")


if __name__ == "__main__":
    main()
