"""Eval LLMDefenderCoordinator on EthereumHeist (Wu 2023) real dataset.

Task #15 extension — validate LLM defender on REAL ETH hacks instead of just
simulation. Uses the 23-hack EthereumHeist pickle (built by load_ethereum_heist.py).

METHODOLOGY:
  - Exclude UpbitHack (95% of the graph, biases everything). Working set:
    ~60k nodes, 130k edges, ~31k labeled (heist + benign mixed).
  - Ground truth clusters: one per hack (22 attacker clusters after excluding
    Upbit) + benign singletons (each benign = own actor).
  - Partial visibility: random 3-way partition (each non-contract address to
    exactly one of 3 simulated exchanges).
  - Train GCN per view, run cosine + LLM defender coordinators.
  - Report ARI vs ground truth hack membership.

Cost estimate: Sonnet ~$0.20-0.30, Haiku ~$0.03-0.05 (similar to sim eval).

Usage:
    python scripts/eval_llm_defender_heist.py --model haiku
    python scripts/eval_llm_defender_heist.py --model sonnet
    python scripts/eval_llm_defender_heist.py --include-upbit   # WARNING: slow
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import numpy as np

from aml.detectors.baselines import PerExchangeDetector
from aml.detectors.gnn import GCNDetector
from aml.detectors.multi_agent import (
    LLMDefenderCoordinator,
    MultiAgentDetector,
    actor_clustering_metrics,
)
from aml.utils.env import load_dotenv_if_present


REPO_ROOT = Path(__file__).resolve().parents[1]
HEIST_PKL = REPO_ROOT / "data" / "ethereum_heist_combined.pkl"
SEED = 42


@dataclass
class ExchangeView:
    """Minimal ExchangeView clone — decoupled from aml.detectors.dataset
    so we don't have to reshape EthereumHeist into a CombinedDataset."""
    name: str
    visible_addresses: set[str] = field(default_factory=set)
    visible_subgraph: nx.MultiDiGraph = field(default_factory=nx.MultiDiGraph)


def build_heist_views(
    graph: nx.MultiDiGraph, num_exchanges: int, seed: int,
) -> list[ExchangeView]:
    """Random partition of nodes into num_exchanges views; induced subgraph per view."""
    rng = random.Random(seed)
    ex_names = [f"exchange_{chr(ord('A') + i)}" for i in range(num_exchanges)]
    assignments: dict[str, str] = {}
    for addr in sorted(graph.nodes()):
        assignments[addr] = rng.choice(ex_names)
    views: list[ExchangeView] = []
    for name in ex_names:
        visible = {a for a, ex in assignments.items() if ex == name}
        subgraph = graph.subgraph(visible).copy()
        views.append(ExchangeView(name=name, visible_addresses=visible,
                                   visible_subgraph=subgraph))
    return views


def build_true_clusters(
    node_labels: dict[str, int],
    hack_membership: dict[str, set[str]],
) -> dict[str, int]:
    """Ground truth: each hack = one actor cluster; each benign = own singleton.

    Cluster IDs 0..N-1 for the N distinct hacks; benigns get IDs N, N+1, ... .
    """
    hacks_seen: list[str] = []
    hack_to_id: dict[str, int] = {}
    clusters: dict[str, int] = {}

    for addr, label in node_labels.items():
        if label == 1:
            # Attacker → assign the FIRST hack membership as its cluster
            # (attackers rarely span hacks; if they do, first-hack is fine
            # as a canonical choice — matches how we'd label a real dataset)
            hacks = hack_membership.get(addr, set())
            if not hacks:
                continue
            hack = sorted(hacks)[0]
            if hack not in hack_to_id:
                hack_to_id[hack] = len(hacks_seen)
                hacks_seen.append(hack)
            clusters[addr] = hack_to_id[hack]

    next_id = len(hacks_seen)
    for addr, label in node_labels.items():
        if label == 0:
            clusters[addr] = next_id
            next_id += 1
    return clusters


def filter_to_hacks(
    graph: nx.MultiDiGraph, node_labels: dict[str, int],
    hack_membership: dict[str, set[str]], allowed_hacks: set[str],
) -> tuple[nx.MultiDiGraph, dict[str, int], dict[str, set[str]]]:
    """Subgraph induced by nodes belonging to at least one allowed hack.

    Applied when --exclude-upbit is set to keep training tractable.
    """
    keep = {
        a for a, hacks in hack_membership.items()
        if hacks & allowed_hacks or hacks == set()   # keep unlabeled counterparties
    }
    # Also drop unlabeled counterparties whose only edges are to UpbitHack nodes
    subgraph = graph.subgraph(keep).copy()
    filtered_labels = {a: v for a, v in node_labels.items() if a in subgraph}
    filtered_membership = {
        a: (hacks & allowed_hacks) or set()
        for a, hacks in hack_membership.items() if a in subgraph
    }
    return subgraph, filtered_labels, filtered_membership


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="haiku",
                        choices=["haiku", "sonnet", "opus"])
    parser.add_argument("--include-upbit", action="store_true",
                        help="Include UpbitHack (95%% of data, very slow)")
    parser.add_argument("--num-exchanges", type=int, default=3)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    scope = "full" if args.include_upbit else "no_upbit"
    out_json = args.out or (REPO_ROOT / "results" /
                             f"eval_llm_defender_heist_{args.model}_{scope}.json")

    load_dotenv_if_present()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set in .env")

    print(f"Loading {HEIST_PKL}...")
    with HEIST_PKL.open("rb") as f:
        d = pickle.load(f)
    graph = d["graph"]
    node_labels = d["node_labels"]
    hack_membership = d["hack_membership"]
    hacks = d["hacks"]
    print(f"  Full: {graph.number_of_nodes():,} nodes / "
          f"{graph.number_of_edges():,} edges / {len(hacks)} hacks")

    if not args.include_upbit:
        print("Excluding UpbitHack (95% of data)...")
        allowed = set(hacks) - {"UpbitHack"}
        graph, node_labels, hack_membership = filter_to_hacks(
            graph, node_labels, hack_membership, allowed,
        )
        print(f"  After exclude: {graph.number_of_nodes():,} nodes / "
              f"{graph.number_of_edges():,} edges")

    n_heist = sum(1 for v in node_labels.values() if v == 1)
    print(f"  Labeled: {len(node_labels):,} "
          f"(heist: {n_heist:,}, benign: {len(node_labels) - n_heist:,})")

    true_clusters = build_true_clusters(node_labels, hack_membership)
    n_true_hacks = len({true_clusters[a] for a in node_labels if node_labels[a] == 1})
    print(f"  True actor clusters: {n_true_hacks} hacks + "
          f"{sum(1 for v in node_labels.values() if v == 0):,} benign singletons")

    print(f"\nBuilding {args.num_exchanges} exchange views (random partition)...")
    views = build_heist_views(graph, args.num_exchanges, seed=SEED)
    for v in views:
        print(f"  {v.name}: {len(v.visible_addresses):,} addrs, "
              f"{v.visible_subgraph.number_of_edges():,} edges")

    train_labels = dict(node_labels)
    print(f"  Training labels passed: {len(train_labels):,}")
    print()

    results: dict = {
        "dataset": "EthereumHeist (Wu 2023, real ETH hacks)",
        "scope": scope,
        "seed": SEED,
        "num_exchanges": args.num_exchanges,
        "n_graph_nodes": int(graph.number_of_nodes()),
        "n_graph_edges": int(graph.number_of_edges()),
        "n_labeled": int(len(node_labels)),
        "n_heist_labeled": int(n_heist),
        "n_true_hack_clusters": int(n_true_hacks),
    }

    # --- Cosine baseline ---
    print("=" * 78)
    print("BASELINE: MultiAgentDetector (cosine)")
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

    # --- LLM defender ---
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

    # --- Verdict ---
    print("=" * 78)
    print("HEAD-TO-HEAD (ARI)")
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
