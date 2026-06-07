"""Baseline detectors — structural / community-detection only.

This module ships the **community-detection baseline** for AML on the
combined transaction graph. It is the first published comparison point
the thesis's multi-agent detector must beat: Louvain (Blondel et al.,
2008) is the standard graph-clustering algorithm cited in nearly every
AML-GNN paper as a non-ML baseline.

The detector framing:
    fit(graph, train_labels)  — learn which communities are attacker-heavy
    predict(nodes)            — for each address, return 0 (benign) or 1 (attacker)
    predict_proba(nodes)      — same but as a float in [0, 1] (fraction
                                of attacker-labelled nodes in the
                                address's community at training time)

The signal: pure topology. Louvain finds densely-connected subgraphs
(`communities`); training assigns each community a verdict based on
the labelled training nodes that fell inside it; prediction returns
the verdict of the community a query address belongs to.

This is intentionally interpretable: "address X is flagged because it
shares a community with K known attacker addresses." The thesis's
*novedad* claim — actor-level clustering under partial visibility —
will need to outperform this baseline on the same dataset.

GNN baselines (GCN/GAT) live in a separate module so callers without
PyTorch installed can still use the community detector.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import networkx as nx
from networkx.algorithms.community import louvain_communities

from aml.detectors.graph import (
    LABEL_ATTACKER_BURNER,
    LABEL_ATTACKER_OTHER,
    LABEL_ATTACKER_SOURCE,
    LABEL_BENIGN_USER,
    LABEL_CLEAN_EXIT_FUNDED,
    LABEL_CLEAN_EXIT_UNUSED,
)


# Labels used internally as binary classification targets.
LABEL_ATTACKER_INT = 1
LABEL_BENIGN_INT = 0


# Which canonical labels count as positives (attacker) for the binary
# classification target. Addresses with confirmed attacker activity on
# chain — source wallet, generated burners, funded clean exits.
ATTACKER_LABELS: frozenset[str] = frozenset({
    LABEL_ATTACKER_SOURCE,
    LABEL_ATTACKER_BURNER,
    LABEL_ATTACKER_OTHER,
    LABEL_CLEAN_EXIT_FUNDED,
})

# Negatives. Note: clean_exit_UNUSED counts as benign — the agent
# designated it as a potential exit but never sent funds to it, so
# it's structurally indistinguishable from a fresh benign address and
# the detector SHOULD NOT flag it. It's a hard-negative distractor:
# the address is "attacker-aware" but "attacker-untouched", and a
# good detector should learn the difference.
BENIGN_LABELS: frozenset[str] = frozenset({
    LABEL_BENIGN_USER,
    LABEL_CLEAN_EXIT_UNUSED,
})


def derive_binary_labels(
    node_labels: dict[str, str],
) -> dict[str, int]:
    """Map canonical address labels → binary {0 = benign, 1 = attacker}.

    Only attacker- or benign-eligible addresses are returned; contracts,
    infrastructure, and unknown addresses are dropped from the
    supervised target set (they aren't the thing the detector is
    classifying — a USDT contract isn't "an attacker", and lumping
    them in as benign would distort the class balance).
    """
    out: dict[str, int] = {}
    for addr, lab in node_labels.items():
        if lab in ATTACKER_LABELS:
            out[addr] = LABEL_ATTACKER_INT
        elif lab in BENIGN_LABELS:
            out[addr] = LABEL_BENIGN_INT
        # else: contracts, infrastructure, unknown → dropped
    return out


# --- interface ----------------------------------------------------------


class Detector(ABC):
    """Abstract base class for all baseline + multi-agent detectors.

    All detectors expose the same `fit` / `predict` / `predict_proba`
    interface so the evaluation harness can swap them freely. This
    matches scikit-learn's contract for binary classifiers.
    """

    @abstractmethod
    def fit(
        self, graph: nx.MultiDiGraph, train_labels: dict[str, int],
    ) -> "Detector":
        """Learn from a labelled subset of the graph's nodes. Returns self."""

    @abstractmethod
    def predict(self, nodes: list[str]) -> list[int]:
        """Return 0/1 predictions in the same order as `nodes`."""

    @abstractmethod
    def predict_proba(self, nodes: list[str]) -> list[float]:
        """Return per-node probability of class 1 (attacker)."""


# --- Louvain community detector ----------------------------------------


@dataclass
class LouvainDetector(Detector):
    """Louvain community detection + per-community majority-vote labelling.

    Args:
        seed: RNG seed passed to `louvain_communities` for reproducibility.
        resolution: Louvain resolution parameter; higher values yield
            more, smaller communities. Default 1.0 (standard).
        prior: fallback class probability for nodes in communities with
            no training labels (or that don't appear in the graph at
            fit time). Default 0.5 (uninformative).

    State after fit:
        community_index: maps each address → its community ID
        community_proba: maps each community ID → P(attacker)
        community_pred:  maps each community ID → 0/1 verdict (proba >= 0.5)
    """

    seed: int = 0
    resolution: float = 1.0
    prior: float = 0.5

    # populated by fit()
    community_index: dict[str, int] = field(default_factory=dict)
    community_proba: dict[int, float] = field(default_factory=dict)
    community_pred: dict[int, int] = field(default_factory=dict)
    _is_fitted: bool = False

    def fit(
        self, graph: nx.MultiDiGraph, train_labels: dict[str, int],
    ) -> "LouvainDetector":
        """Run Louvain on the undirected projection; vote each community
        from the training-labelled nodes that landed inside it."""
        # Reset state so successive fits don't carry stale communities.
        self.community_index = {}
        self.community_proba = {}
        self.community_pred = {}
        self._is_fitted = True

        if graph.number_of_nodes() == 0:
            return self

        # Louvain runs on undirected graphs. Project to a simple
        # undirected graph by collapsing parallel edges; this loses
        # multiplicity but preserves the connectivity structure that
        # community detection cares about.
        undirected = nx.Graph(graph.to_undirected(as_view=False))

        communities = louvain_communities(
            undirected, seed=self.seed, resolution=self.resolution,
        )

        # Build the address → community index.
        for cid, members in enumerate(communities):
            for addr in members:
                self.community_index[addr] = cid

        # Vote each community from its training-labelled nodes.
        for cid, members in enumerate(communities):
            labelled = [
                train_labels[a] for a in members if a in train_labels
            ]
            if not labelled:
                # No training labels in this community — fall back to
                # the prior. Avoids confidently mispredicting on
                # totally-unseen communities.
                self.community_proba[cid] = self.prior
                self.community_pred[cid] = int(self.prior >= 0.5)
                continue
            proba = sum(labelled) / len(labelled)
            self.community_proba[cid] = proba
            self.community_pred[cid] = int(proba >= 0.5)

        return self

    def predict(self, nodes: list[str]) -> list[int]:
        self._check_fitted()
        return [
            self.community_pred.get(self.community_index.get(a, -1),
                                    int(self.prior >= 0.5))
            for a in nodes
        ]

    def predict_proba(self, nodes: list[str]) -> list[float]:
        self._check_fitted()
        return [
            self.community_proba.get(self.community_index.get(a, -1),
                                     self.prior)
            for a in nodes
        ]

    def _check_fitted(self) -> None:
        if not self._is_fitted:
            raise RuntimeError(
                "LouvainDetector not fitted yet — call .fit(graph, train_labels) first."
            )


# --- helper: per-exchange detector wrapper -----------------------------


@dataclass
class PerExchangeDetector(Detector):
    """Train one Detector per ExchangeView, then aggregate predictions.

    Models the partial-visibility setup: each exchange trains a
    detector ONLY on what it can see (its own induced subgraph). At
    prediction time, an address is routed to the exchange(s) that have
    it in their visible set, and per-exchange predictions are
    aggregated by averaging probabilities (then thresholded at 0.5).

    For non-contract addresses (visible to exactly one exchange) the
    aggregation is a no-op — the single host exchange's prediction is
    the final answer. The aggregation only matters for contracts
    visible to multiple exchanges.

    Args:
        detector_factory: zero-arg callable that returns a fresh
            Detector instance for each exchange (e.g.
            `lambda: LouvainDetector(seed=0)`).
        prior: fallback probability for addresses visible to NO
            exchange (shouldn't happen in normal use; defensive).
    """

    detector_factory: object   # Callable[[], Detector] — typed loosely
    prior: float = 0.5

    # populated by fit()
    detectors: dict[str, Detector] = field(default_factory=dict)
    visibility: dict[str, set[str]] = field(default_factory=dict)   # exchange → addrs

    def fit_per_view(
        self,
        views,                      # list[ExchangeView] from dataset.partial_visibility_split
        train_labels: dict[str, int],
    ) -> "PerExchangeDetector":
        """Train one detector per view, using only the labels of nodes
        visible to that view."""
        self.detectors = {}
        self.visibility = {}
        for view in views:
            visible_train = {
                a: l for a, l in train_labels.items()
                if a in view.visible_addresses
            }
            det = self.detector_factory()
            det.fit(view.visible_subgraph, visible_train)
            self.detectors[view.name] = det
            self.visibility[view.name] = set(view.visible_addresses)
        return self

    # Detector interface — but the user is expected to call
    # fit_per_view, not fit. fit() raises so the API misuse is loud.
    def fit(self, graph, train_labels):
        raise RuntimeError(
            "Use fit_per_view(views, train_labels) — PerExchangeDetector "
            "needs the list of ExchangeView objects to know what each "
            "exchange can see."
        )

    def predict(self, nodes: list[str]) -> list[int]:
        return [int(p >= 0.5) for p in self.predict_proba(nodes)]

    def predict_proba(self, nodes: list[str]) -> list[float]:
        out: list[float] = []
        for addr in nodes:
            probas = [
                self.detectors[name].predict_proba([addr])[0]
                for name, visible in self.visibility.items()
                if addr in visible
            ]
            if not probas:
                out.append(self.prior)
            else:
                out.append(sum(probas) / len(probas))
        return out
