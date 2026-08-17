"""Full detector comparison over the seed 403 headline run.

Runs 5 configurations against the same mini dataset (seed 403 + 50
benigns) and reports a single comparison table for TFM §5.6.5:

  Config              Layer 1     Layer 2
  ------              -------     -------
  louvain+cosine      Louvain     cosine similarity (baseline)
  gcn+cosine          GCN         cosine similarity (novel L1 + baseline L2)
  gcn+llm_haiku       GCN         LLM Haiku 4.5
  gcn+llm_sonnet      GCN         LLM Sonnet 4.6
  gcn+llm_opus        GCN         LLM Opus 4.7 (headline qualitative)

Reports per-config: ARI, homogeneity, completeness, cost, wall time,
and (for LLM) reasoning excerpt.

Cost estimate: ~$0.60 (Haiku ~$0.005 + Sonnet ~$0.10 + Opus ~$0.50).

Usage:
    python scripts/eval_defender_seed403_comparison.py
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from aml.detectors.baselines import LouvainDetector, derive_binary_labels
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


def run_layer2_baseline(views, train_labels, l1_factory, l1_name: str) -> dict:
    """Runs MultiAgentDetector (cosine L2) with the given L1 factory."""
    t0 = time.time()
    det = MultiAgentDetector(detector_factory=l1_factory)
    det.fit_per_view(views, train_labels)
    elapsed = time.time() - t0
    true_clusters = train_labels  # placeholder; recomputed by caller
    return {"det": det, "elapsed": elapsed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-benigns", type=int, default=50)
    parser.add_argument("--num-exchanges", type=int, default=3)
    parser.add_argument("--skip-opus", action="store_true",
                        help="Skip Opus tier for cheaper iteration.")
    args = parser.parse_args()

    load_dotenv_if_present()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set")

    out_json = REPO_ROOT / "results" / "eval_defender_seed403_comparison.json"

    if not ATTACKER_RUN.is_dir():
        raise SystemExit(f"missing attacker run {ATTACKER_RUN}")

    benign_dirs = sorted(BENIGN_ROOT.iterdir())[:args.num_benigns]
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
    print(f"  Built {len(views)} exchange views")

    bin_labels = derive_binary_labels(combined.node_labels)
    train_labels = {a: bin_labels[a] for a in bin_labels}
    n_att = sum(train_labels.values())
    print(f"  Training: {len(train_labels):,} "
          f"({n_att:,} attackers, {len(train_labels) - n_att:,} benigns)")
    print()

    oracle = PriceOracle(cache_dir=PRICE_CACHE)
    campaign_ts = resolve_campaign_ts(oracle, None)
    print(f"Market context: campaign_ts={campaign_ts.isoformat()}  "
          f"ETH=${oracle.price('eth', campaign_ts):,.2f}\n")

    results: dict = {
        "attacker_run": str(ATTACKER_RUN.name),
        "num_benigns": len(benign_dirs),
        "num_exchanges": args.num_exchanges,
        "seed": SEED,
        "n_graph_nodes": int(g.number_of_nodes()),
        "n_graph_edges": int(g.number_of_edges()),
        "n_true_actor_clusters": n_actors_true,
        "n_attacker_addresses": int(n_att),
        "configs": {},
    }

    def record_layer2(config_name: str, det, elapsed: float,
                      cost_usd: float = 0.0, tokens_in: int = 0,
                      tokens_out: int = 0, reasoning: str = "",
                      used_fallback: bool = False) -> None:
        m = actor_clustering_metrics(true_clusters, det.actor_clusters)
        entry = {
            "fit_time_seconds": round(elapsed, 2),
            "cost_usd": round(cost_usd, 4),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "used_fallback": used_fallback,
            **{k: (round(v, 4) if isinstance(v, float) else v)
               for k, v in m.items()},
        }
        if reasoning:
            entry["reasoning"] = reasoning
        results["configs"][config_name] = entry
        pred = m.get("n_clusters_pred")
        ari = m.get("ari")
        hom = m.get("homogeneity")
        comp = m.get("completeness")
        print(f"  {config_name:<20} clusters={pred}  ARI={ari:.4f}  "
              f"Hom={hom:.4f}  Comp={comp:.4f}  "
              f"t={elapsed:.1f}s  ${cost_usd:.4f}")

    # 1) Louvain L1 + cosine L2
    print("=" * 78)
    print("Config 1: Louvain L1 + cosine L2")
    print("=" * 78)
    r = run_layer2_baseline(
        views, train_labels,
        l1_factory=lambda: LouvainDetector(),
        l1_name="louvain",
    )
    record_layer2("louvain+cosine", r["det"], r["elapsed"])
    print()

    # 2) GCN L1 + cosine L2
    print("=" * 78)
    print("Config 2: GCN L1 + cosine L2 (baseline)")
    print("=" * 78)
    r = run_layer2_baseline(
        views, train_labels,
        l1_factory=lambda: GCNDetector(seed=SEED, epochs=50),
        l1_name="gcn",
    )
    record_layer2("gcn+cosine", r["det"], r["elapsed"])
    print()

    # 3-5) GCN L1 + LLM L2
    llm_models = ["haiku", "sonnet"]
    if not args.skip_opus:
        llm_models.append("opus")

    for model in llm_models:
        print("=" * 78)
        print(f"Config: GCN L1 + LLM {model.capitalize()} L2")
        print("=" * 78)
        t0 = time.time()
        det = LLMDefenderCoordinator(
            detector_factory=lambda: GCNDetector(seed=SEED, epochs=50),
            llm_model=model,
            oracle=oracle,
            campaign_ts=campaign_ts,
        )
        det.fit_per_view(views, train_labels)
        elapsed = time.time() - t0
        record_layer2(
            f"gcn+llm_{model}",
            det, elapsed,
            cost_usd=det.usage.get("cost_usd", 0.0),
            tokens_in=det.usage.get("input_tokens", 0),
            tokens_out=det.usage.get("output_tokens", 0),
            reasoning=det.llm_reasoning,
            used_fallback=det.llm_output_used_fallback,
        )
        print()

    # Final comparison table
    print("=" * 78)
    print("COMPARISON TABLE (seed 403 + 50 benigns, ARI over true actor clusters)")
    print("=" * 78)
    print(f"  {'Config':<20} {'ARI':>8} {'Hom':>8} {'Comp':>8} "
          f"{'Clusters':>10} {'Time(s)':>10} {'Cost($)':>10}")
    for name, e in results["configs"].items():
        print(f"  {name:<20} {e['ari']:>8.4f} {e['homogeneity']:>8.4f} "
              f"{e['completeness']:>8.4f} {e['n_clusters_pred']:>10} "
              f"{e['fit_time_seconds']:>10.1f} {e['cost_usd']:>10.4f}")

    total_cost = sum(e["cost_usd"] for e in results["configs"].values())
    total_time = sum(e["fit_time_seconds"] for e in results["configs"].values())
    print(f"\n  TOTAL time: {total_time:.1f}s  TOTAL cost: ${total_cost:.4f}")
    results["total_cost_usd"] = round(total_cost, 4)
    results["total_time_seconds"] = round(total_time, 2)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_json}")


if __name__ == "__main__":
    main()
