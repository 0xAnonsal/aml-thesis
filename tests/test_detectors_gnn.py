"""Tests for the GCN baseline detector.

Split into two layers of skip-gating:
  - extract_features tests need only numpy + networkx → always run
  - GCNDetector tests need PyTorch + PyTorch Geometric → skip cleanly
    when those aren't installed (CI on the cheap, or a contributor
    who wants the community baseline without the ML deps)

Tests focus on the INTERFACE and STRUCTURAL properties (correct
shapes, deterministic with seed, handles empty/edge cases, integrates
with the Detector ABC + evaluation harness). We deliberately don't
assert on training accuracy: tiny synthetic graphs + 100 epochs are
too noisy for a meaningful accuracy bound, and brittle accuracy
asserts on a baseline classifier would be a maintenance burden.
"""
from __future__ import annotations

import networkx as nx
import numpy as np
import pytest

from aml.detectors.baselines import Detector
from aml.detectors.gnn import (
    EDGE_KINDS,
    FEATURE_DIM,
    FEATURE_NAMES,
    extract_features,
)


# Skip-gate the PyG-dependent tests if torch / torch_geometric missing.
try:
    import torch              # noqa: F401
    import torch_geometric    # noqa: F401
    _PYG_OK = True
except ImportError:
    _PYG_OK = False
needs_pyg = pytest.mark.skipif(
    not _PYG_OK, reason="PyTorch + PyTorch Geometric not installed",
)


# --- feature extraction (numpy/networkx only — always runs) -------------


def test_feature_dim_matches_feature_names():
    """FEATURE_DIM == len(FEATURE_NAMES) — protects against schema drift."""
    assert FEATURE_DIM == len(FEATURE_NAMES)


def test_feature_names_include_all_edge_kinds_in_both_directions():
    """Every kind in EDGE_KINDS shows up as both `<kind>_in` and `<kind>_out`."""
    for kind in EDGE_KINDS:
        assert f"{kind}_in" in FEATURE_NAMES
        assert f"{kind}_out" in FEATURE_NAMES


def test_extract_features_returns_correct_shape():
    """(N, FEATURE_DIM) for N nodes."""
    g = nx.MultiDiGraph()
    g.add_edge("a", "b")
    X = extract_features(g, ["a", "b", "c"])
    assert X.shape == (3, FEATURE_DIM)


def test_extract_features_empty_node_list_returns_empty_matrix():
    """Edge case: zero rows."""
    X = extract_features(nx.MultiDiGraph(), [])
    assert X.shape == (0, FEATURE_DIM)


def test_extract_features_isolated_node_is_all_zero():
    """A node with no incident edges should have all-zero features."""
    g = nx.MultiDiGraph()
    g.add_node("alone")
    X = extract_features(g, ["alone"])
    assert np.all(X == 0)


def test_extract_features_counts_edge_kinds_correctly():
    """3 transfer_usdt out-edges from a → transfer_usdt_out feature = log(1+3)."""
    g = nx.MultiDiGraph()
    for i, recipient in enumerate(["b", "c", "d"]):
        g.add_edge("a", recipient, kind="transfer_usdt", asset="USDT", value=10.0)
    X = extract_features(g, ["a", "b", "c", "d"])
    out_idx = FEATURE_NAMES.index("transfer_usdt_out")
    in_idx = FEATURE_NAMES.index("transfer_usdt_in")
    # a has 3 outgoing transfer_usdt → log1p(3)
    assert X[0, out_idx] == pytest.approx(np.log1p(3))
    # b/c/d each have 1 incoming → log1p(1)
    for i in (1, 2, 3):
        assert X[i, in_idx] == pytest.approx(np.log1p(1))


def test_extract_features_aggregates_value_correctly():
    """Total ETH value moved in/out is log1p of the SUM, not the max."""
    g = nx.MultiDiGraph()
    g.add_edge("a", "b", kind="transfer_eth", asset="ETH", value=1.5)
    g.add_edge("a", "b", kind="transfer_eth", asset="ETH", value=2.5)
    X = extract_features(g, ["a", "b"])
    out_eth_idx = FEATURE_NAMES.index("log_eth_out")
    in_eth_idx = FEATURE_NAMES.index("log_eth_in")
    # a's outgoing ETH total = 4.0 → log1p(4) ≈ 1.609
    assert X[0, out_eth_idx] == pytest.approx(np.log1p(4.0))
    assert X[1, in_eth_idx] == pytest.approx(np.log1p(4.0))


def test_extract_features_unique_counterparty_count():
    """log_unique_out reflects distinct recipients, not repeat edges."""
    g = nx.MultiDiGraph()
    # 3 edges a→b (same counterparty) + 1 edge a→c
    for _ in range(3):
        g.add_edge("a", "b", kind="transfer_eth", asset="ETH", value=1)
    g.add_edge("a", "c", kind="transfer_eth", asset="ETH", value=1)
    X = extract_features(g, ["a", "b", "c"])
    out_unique_idx = FEATURE_NAMES.index("log_unique_out")
    # a has 2 unique counterparties (b, c) → log1p(2)
    assert X[0, out_unique_idx] == pytest.approx(np.log1p(2))


def test_extract_features_skips_addresses_not_in_node_order():
    """If a node is in graph but absent from node_order, its edges are ignored."""
    g = nx.MultiDiGraph()
    g.add_edge("a", "b", kind="transfer_eth", asset="ETH", value=1.0)
    g.add_edge("a", "ghost", kind="transfer_eth", asset="ETH", value=99.0)
    X = extract_features(g, ["a", "b"])  # ghost excluded
    out_eth_idx = FEATURE_NAMES.index("log_eth_out")
    # a's tracked outgoing ETH = only the a→b edge (1.0), not the ghost edge
    assert X[0, out_eth_idx] == pytest.approx(np.log1p(1.0))


# --- GCNDetector (PyG-gated) --------------------------------------------


def _two_cluster_graph():
    """Same shape as in test_detectors_baselines: 4 attackers, 4 benign,
    one bridge edge. Useful to verify the GCN at least learns the
    cleanest possible signal."""
    g = nx.MultiDiGraph()
    A = [f"a{i}" for i in range(4)]
    B = [f"b{i}" for i in range(4)]
    for i, u in enumerate(A):
        for v in A[i + 1:]:
            g.add_edge(u, v, kind="transfer_eth", asset="ETH", value=1.0)
    for i, u in enumerate(B):
        for v in B[i + 1:]:
            g.add_edge(u, v, kind="transfer_eth", asset="ETH", value=1.0)
    g.add_edge(A[0], B[0], kind="transfer_eth", asset="ETH", value=0.1)
    return g, A, B


@needs_pyg
def test_gcn_detector_implements_detector_interface():
    """GCNDetector subclasses the Detector ABC."""
    from aml.detectors.gnn import GCNDetector
    assert isinstance(GCNDetector(), Detector)


@needs_pyg
def test_gcn_detector_fit_runs_and_returns_self():
    """fit() returns self (chaining convention)."""
    from aml.detectors.gnn import GCNDetector
    g, A, B = _two_cluster_graph()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    det = GCNDetector(epochs=10, seed=0)
    out = det.fit(g, train_labels)
    assert out is det


@needs_pyg
def test_gcn_detector_predict_proba_returns_probabilities_in_unit_interval():
    """After fit, every predicted probability is in [0, 1]."""
    from aml.detectors.gnn import GCNDetector
    g, A, B = _two_cluster_graph()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    det = GCNDetector(epochs=10, seed=0).fit(g, train_labels)
    probas = det.predict_proba(A + B)
    for p in probas:
        assert 0.0 <= p <= 1.0


@needs_pyg
def test_gcn_detector_predict_returns_binary_labels():
    """predict() returns 0/1 ints with same length as input."""
    from aml.detectors.gnn import GCNDetector
    g, A, B = _two_cluster_graph()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    det = GCNDetector(epochs=10, seed=0).fit(g, train_labels)
    preds = det.predict(A + B)
    assert len(preds) == 8
    assert all(p in (0, 1) for p in preds)


@needs_pyg
def test_gcn_detector_is_deterministic_with_seed():
    """Same seed + same data → identical predict_proba outputs."""
    from aml.detectors.gnn import GCNDetector
    g, A, B = _two_cluster_graph()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    p1 = GCNDetector(epochs=10, seed=42).fit(g, train_labels).predict_proba(A + B)
    p2 = GCNDetector(epochs=10, seed=42).fit(g, train_labels).predict_proba(A + B)
    for x, y in zip(p1, p2):
        assert x == pytest.approx(y, rel=1e-5, abs=1e-6)


@needs_pyg
def test_gcn_detector_unfitted_predict_raises():
    """Calling predict before fit is a programming error → loud RuntimeError."""
    from aml.detectors.gnn import GCNDetector
    det = GCNDetector()
    with pytest.raises(RuntimeError, match="not fitted"):
        det.predict(["any"])


@needs_pyg
def test_gcn_detector_unknown_address_returns_prior_proba():
    """Address not in fit graph → 0.5 prior probability."""
    from aml.detectors.gnn import GCNDetector
    g, A, B = _two_cluster_graph()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    det = GCNDetector(epochs=10, seed=0).fit(g, train_labels)
    proba = det.predict_proba(["ghost-address-not-in-graph"])
    assert proba == [0.5]


@needs_pyg
def test_gcn_detector_handles_empty_graph():
    """Empty graph + empty labels → no crash, predict returns prior.

    The probability is the actual contract (0.5 = uninformative prior
    when there's nothing to learn). The binary `predict` result at the
    exact 0.5 boundary depends on the >= vs > convention and isn't a
    useful thing to assert on.
    """
    from aml.detectors.gnn import GCNDetector
    det = GCNDetector(epochs=5, seed=0).fit(nx.MultiDiGraph(), {})
    assert det.predict_proba(["anything"]) == [0.5]
    # Just verify it's a valid binary label, don't pin to a boundary side.
    assert det.predict(["anything"])[0] in (0, 1)


@needs_pyg
def test_gcn_detector_handles_no_labels_overlap_with_graph():
    """Training labels for nodes not in the graph → no supervision but
    fit must still complete (untrained forward) and predictions valid."""
    from aml.detectors.gnn import GCNDetector
    g = nx.MultiDiGraph()
    g.add_edge("a", "b", kind="transfer_eth", asset="ETH", value=1.0)
    # Labels refer to addresses not in g
    train_labels = {"x": 1, "y": 0}
    det = GCNDetector(epochs=5, seed=0).fit(g, train_labels)
    probas = det.predict_proba(["a", "b"])
    for p in probas:
        assert 0.0 <= p <= 1.0


@needs_pyg
def test_gcn_detector_integrates_with_evaluation_harness():
    """GCNDetector predictions plug straight into evaluate() — smoke check
    of the shared Detector interface."""
    from aml.detectors.gnn import GCNDetector
    from aml.detectors.eval import evaluate
    g, A, B = _two_cluster_graph()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    det = GCNDetector(epochs=20, seed=0).fit(g, train_labels)
    y_true = [1] * 4 + [0] * 4
    nodes = A + B
    y_pred = det.predict(nodes)
    y_proba = det.predict_proba(nodes)
    m = evaluate(y_true, y_pred, y_proba)
    assert m.n_total == 8
    # Sanity: AUC computed (probas are valid), and accuracy >= chance.
    # We don't assert better than chance on tiny data — too noisy.
    assert 0.0 <= m.accuracy <= 1.0
    assert m.roc_auc is None or 0.0 <= m.roc_auc <= 1.0
