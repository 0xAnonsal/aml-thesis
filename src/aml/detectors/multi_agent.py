"""Multi-agent collaborative detector — the thesis's novelty claim.

Each "exchange" trains its own local detector on its partial view of
the combined transaction graph (Louvain or GCN — configurable). Then
all exchanges share per-address FEATURE FINGERPRINTS (just the
numeric vectors from gnn.extract_features, not the raw graph data) to
a coordinator that builds a cross-exchange similarity graph. Connected
components in that similarity graph = SUSPECTED SAME-ACTOR groups.

This is the locked-scope novelty: actor-level smurfing detection
under partial visibility. No single exchange can recover the full
laundering actor; the multi-agent detector recovers them by
combining local predictions with cross-exchange embedding similarity.

Public surface:

  MultiAgentDetector(detector_factory, similarity_threshold=0.95)
    .fit_per_view(views, train_labels) -> self
    .predict(nodes)                    -> list[int]     (binary)
    .predict_proba(nodes)              -> list[float]   (binary)
    .predict_actors(nodes)             -> list[int]     (cluster ids; -1 = unknown)
    .actor_clusters                    -> dict[address, cluster_id]

  cluster_by_similarity(features, threshold) -> dict[address, cluster_id]
    Standalone clustering primitive — exposed for testing and reuse.

  true_actor_clusters(addresses_dict, run_kinds) -> dict[address, int]
    Build the ground-truth actor map from per-address run membership.
    Each attacker run = one actor (its source + burners + funded exits).
    Benign users are singletons (each its own "actor").

  adjusted_rand_index(true_labels, pred_labels) -> float
    Standalone ARI implementation (no sklearn dep) for evaluating
    the clustering output against ground truth.

  actor_clustering_metrics(true_clusters, pred_clusters) -> dict
    Bundled metrics (n_clusters, ARI, homogeneity, completeness)
    for the actor-clustering output.

The binary attacker classification still uses the same Detector ABC,
so the eval harness from #36 works unchanged on the per-node side.
The new ARI + clustering metrics are exposed separately because they
score a fundamentally different output type (cluster assignments,
not binary labels).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

import networkx as nx
import numpy as np

from aml.detectors.baselines import Detector, PerExchangeDetector
from aml.detectors.gnn import FEATURE_DIM, extract_features


# Sentinel for "this address has no cluster assignment" — used in
# predict_actors when an address isn't visible to any exchange the
# detector was fitted on.
NO_CLUSTER: int = -1


# --- standalone clustering primitive -------------------------------------


def cluster_by_similarity(
    features: dict[str, np.ndarray],
    *,
    threshold: float = 0.95,
) -> dict[str, int]:
    """Cluster addresses by cosine-similarity-of-features connectivity.

    Build a similarity graph: edge between two addresses if their
    feature vectors have cosine similarity >= threshold. Connected
    components = clusters. Returns {address: cluster_id}.

    O(N^2) in number of addresses — fine for the thesis scale (a few
    hundred per dataset); would need approximate-NN for production.

    Special cases:
      - empty input → {}
      - zero-norm features (all-zero) → that address is its own
        singleton (cosine undefined; we treat as dissimilar to all)
    """
    addresses = list(features.keys())
    if not addresses:
        return {}

    # Normalise features to unit norm so cosine = dot product. Handle
    # zero-norm vectors (isolated nodes with no edges) by leaving them
    # at zero — they'll have similarity 0 to everything else.
    matrix = np.array([features[a] for a in addresses], dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    safe_norms = np.where(norms > 1e-9, norms, 1.0)   # avoid div-by-zero
    matrix = matrix / safe_norms                       # divide all rows safely
    matrix = np.where(norms > 1e-9, matrix, 0.0)       # zero out the zero-norm rows

    # Build similarity graph
    g = nx.Graph()
    g.add_nodes_from(addresses)
    n = len(addresses)
    for i in range(n):
        for j in range(i + 1, n):
            sim = float(matrix[i] @ matrix[j])
            if sim >= threshold:
                g.add_edge(addresses[i], addresses[j])

    # Connected components → cluster ids
    clusters: dict[str, int] = {}
    for cid, component in enumerate(nx.connected_components(g)):
        for addr in component:
            clusters[addr] = cid
    return clusters


# --- detector ------------------------------------------------------------


@dataclass
class MultiAgentDetector(Detector):
    """Collaborative detector: per-exchange local + cross-exchange clustering.

    The binary classification (predict / predict_proba) is identical to
    PerExchangeDetector with the same factory. The novelty is
    predict_actors() which exposes the cross-exchange actor clustering
    computed at fit time.

    Args:
        detector_factory: zero-arg callable returning a fresh local
            Detector (e.g. lambda: GCNDetector(epochs=100, seed=0)).
            Used to instantiate one detector per ExchangeView.
        similarity_threshold: cosine-similarity cutoff for actor
            clustering. Higher = fewer, tighter clusters. Default
            0.95 is conservative; tune empirically per dataset.
        prior: fallback probability for addresses visible to no
            exchange. Default 0.5.
    """

    detector_factory: Callable[[], Detector]
    similarity_threshold: float = 0.95
    prior: float = 0.5

    # populated by fit_per_view
    _binary: Any = None                     # PerExchangeDetector
    actor_clusters: dict[str, int] = field(default_factory=dict)

    def fit_per_view(
        self, views, train_labels: dict[str, int],
    ) -> "MultiAgentDetector":
        """Train per-exchange detectors AND compute cross-exchange clusters."""
        # Phase 1: train binary classifiers per exchange (reuses #36).
        self._binary = PerExchangeDetector(
            detector_factory=self.detector_factory, prior=self.prior,
        ).fit_per_view(views, train_labels)

        # Phase 2: cross-exchange actor clustering on shared features.
        # Each exchange computes features for its addresses; coordinator
        # aggregates and clusters. The aggregation step is the
        # "privacy-respecting" sharing — fingerprints only, no raw graph.
        all_features: dict[str, np.ndarray] = {}
        for view in views:
            node_order = sorted(view.visible_addresses)
            X = extract_features(view.visible_subgraph, node_order)
            for i, addr in enumerate(node_order):
                # Address may appear in multiple exchanges (shared
                # contracts). Average the feature vectors across
                # exchanges in that case — the contract is what it is
                # regardless of which exchange sees it, so averaging
                # is appropriate.
                if addr in all_features:
                    all_features[addr] = (all_features[addr] + X[i]) / 2.0
                else:
                    all_features[addr] = X[i].astype(np.float64)

        # Restrict clustering to addresses the binary classifier
        # predicts as POSITIVE. Rationale: benign users share generic
        # transaction-shape features (transfers + swaps + low degree)
        # which, under cosine-similarity + connected-components
        # clustering, collapse all of them into a single giant
        # component. That over-clustering wrecks ARI on the actor
        # task because the truth is "N benign singletons + a small
        # number of attacker actors each with several wallets". By
        # only clustering predicted-positives, the similarity graph
        # is restricted to the wallets we actually want to group into
        # actor identities. Benigns (and false-negative attackers)
        # get singleton actor ids — which is the correct structure
        # for benigns and a downstream cost of the binary classifier
        # missing a positive for false negatives.
        all_addrs = sorted(all_features.keys())
        binary_preds = self._binary.predict(all_addrs) if all_addrs else []
        positive_features = {
            addr: all_features[addr]
            for addr, pred in zip(all_addrs, binary_preds)
            if pred == 1
        }

        clusters_on_positives = cluster_by_similarity(
            positive_features, threshold=self.similarity_threshold,
        )

        # Compose final actor_clusters: positives get their cluster id
        # from cosine-similarity grouping; negatives get a fresh
        # singleton id each. Preserves the truth-compatible
        # "one-actor-per-benign-user" structure.
        next_singleton_id = (
            max(clusters_on_positives.values()) + 1
            if clusters_on_positives else 0
        )
        final_clusters: dict[str, int] = dict(clusters_on_positives)
        for addr in all_addrs:
            if addr not in final_clusters:
                final_clusters[addr] = next_singleton_id
                next_singleton_id += 1

        self.actor_clusters = final_clusters
        return self

    # Detector ABC interface — same behaviour as PerExchangeDetector.
    def fit(self, graph, train_labels):
        raise RuntimeError(
            "Use fit_per_view(views, train_labels) — MultiAgentDetector "
            "needs the list of ExchangeView objects."
        )

    def predict(self, nodes: list[str]) -> list[int]:
        if self._binary is None:
            raise RuntimeError("MultiAgentDetector not fitted — call fit_per_view first.")
        return self._binary.predict(nodes)

    def predict_proba(self, nodes: list[str]) -> list[float]:
        if self._binary is None:
            raise RuntimeError("MultiAgentDetector not fitted — call fit_per_view first.")
        return self._binary.predict_proba(nodes)

    def predict_actors(self, nodes: list[str]) -> list[int]:
        """Return suspected actor cluster id per node. NO_CLUSTER if unknown."""
        if self._binary is None:
            raise RuntimeError("MultiAgentDetector not fitted — call fit_per_view first.")
        return [self.actor_clusters.get(a, NO_CLUSTER) for a in nodes]


# --- ground-truth actor map ---------------------------------------------


def true_actor_clusters(
    runs: list,                                # list[RunData] from dataset.combine_runs
) -> dict[str, int]:
    """Build the ground-truth address → actor_id map from RunData list.

    One ACTOR per attacker run — the attacker's source wallet, every
    burner the agent generated in that run, plus the funded clean exits
    that received its money. Benign users are individual actors (each
    their own cluster). Contracts and infrastructure are excluded.

    This is what predict_actors() is evaluated against via the
    Adjusted Rand Index.
    """
    next_actor_id = 0
    out: dict[str, int] = {}

    for run in runs:
        if run.kind == "attacker":
            addrs = run.addresses
            members: set[str] = set()
            src = addrs.get("source_wallet")
            if src:
                members.add(src)
            members.update(addrs.get("burners_generated_during_campaign") or [])
            members.update(addrs.get("clean_exits_funded") or [])
            for addr in members:
                out[addr] = next_actor_id
            next_actor_id += 1
        elif run.kind == "benign":
            # Each benign user is its own actor (no shared identity).
            for u in run.addresses.get("benign_users") or []:
                out[u] = next_actor_id
                next_actor_id += 1
    return out


# --- evaluation: clustering metrics --------------------------------------


def adjusted_rand_index(
    y_true: list[int], y_pred: list[int],
) -> float:
    """Adjusted Rand Index between two clusterings.

    Returns a value in [-1, 1]: 1.0 = identical clusterings, 0.0 =
    random labelling, < 0 = worse than random. Standard reference:
    Hubert & Arabie (1985). Implementation is the contingency-table /
    Counter-pair formula; no sklearn dependency.

    Returns 1.0 for a trivial perfect case (n <= 1).
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"y_true ({len(y_true)}) and y_pred ({len(y_pred)}) length mismatch"
        )
    n = len(y_true)
    if n <= 1:
        return 1.0   # trivially identical

    contingency: Counter = Counter(zip(y_true, y_pred))
    true_counts: Counter = Counter(y_true)
    pred_counts: Counter = Counter(y_pred)

    def _comb2(x: int) -> int:
        return x * (x - 1) // 2

    sum_c = sum(_comb2(v) for v in contingency.values())
    sum_t = sum(_comb2(v) for v in true_counts.values())
    sum_p = sum(_comb2(v) for v in pred_counts.values())

    total = _comb2(n)
    if total == 0:
        return 1.0

    expected = (sum_t * sum_p) / total
    max_index = (sum_t + sum_p) / 2.0
    if max_index == expected:
        # Degenerate case (all in one cluster on one side) — treat as 1.0
        return 1.0
    return (sum_c - expected) / (max_index - expected)


def actor_clustering_metrics(
    true_clusters: dict[str, int],
    pred_clusters: dict[str, int],
) -> dict:
    """Compare predicted actor clusters against the ground-truth map.

    Returns:
        n_addresses          — addresses with both true and pred labels
        n_clusters_true      — number of true actors over the common set
        n_clusters_pred      — number of predicted clusters
        ari                  — Adjusted Rand Index (or None if undefined)
        homogeneity          — fraction of predicted clusters that are
                               pure (single true actor)
        completeness         — fraction of true actors that are entirely
                               in one predicted cluster
    """
    common = sorted(set(true_clusters) & set(pred_clusters))
    if not common:
        return {
            "n_addresses": 0,
            "n_clusters_true": 0,
            "n_clusters_pred": 0,
            "ari": None,
            "homogeneity": None,
            "completeness": None,
        }

    y_true = [true_clusters[a] for a in common]
    y_pred = [pred_clusters[a] for a in common]

    # Homogeneity: of the predicted clusters, how many are pure?
    pred_to_trues: dict[int, set] = {}
    for p, t in zip(y_pred, y_true):
        pred_to_trues.setdefault(p, set()).add(t)
    pure_pred = sum(1 for trues in pred_to_trues.values() if len(trues) == 1)
    homogeneity = pure_pred / len(pred_to_trues) if pred_to_trues else None

    # Completeness: of the true actors, how many are entirely in one
    # predicted cluster?
    true_to_preds: dict[int, set] = {}
    for t, p in zip(y_true, y_pred):
        true_to_preds.setdefault(t, set()).add(p)
    complete_true = sum(1 for preds in true_to_preds.values() if len(preds) == 1)
    completeness = complete_true / len(true_to_preds) if true_to_preds else None

    return {
        "n_addresses": len(common),
        "n_clusters_true": len(set(y_true)),
        "n_clusters_pred": len(set(y_pred)),
        "ari": adjusted_rand_index(y_true, y_pred),
        "homogeneity": homogeneity,
        "completeness": completeness,
    }
