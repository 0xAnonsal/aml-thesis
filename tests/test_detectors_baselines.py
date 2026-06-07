"""Tests for the community-detection baseline + evaluation harness.

All structural / synthetic — small handcrafted graphs, no Anvil, no
real runs. Covers: label conversion, Louvain training (community
voting, prior fallback), prediction interface (transductive setup,
unknown-node handling), per-exchange wrapper, evaluation metrics
(confusion-matrix counts, F1, ROC-AUC, edge cases).
"""
from __future__ import annotations

import networkx as nx
import pytest

from aml.detectors.baselines import (
    LABEL_ATTACKER_INT,
    LABEL_BENIGN_INT,
    Detector,
    LouvainDetector,
    PerExchangeDetector,
    derive_binary_labels,
)
from aml.detectors.eval import DetectorMetrics, evaluate, pretty_print
from aml.detectors.graph import (
    LABEL_ATTACKER_BURNER,
    LABEL_ATTACKER_SOURCE,
    LABEL_BENIGN_USER,
    LABEL_CLEAN_EXIT_FUNDED,
    LABEL_CLEAN_EXIT_UNUSED,
    LABEL_CONTRACT,
    LABEL_INFRASTRUCTURE,
    LABEL_UNKNOWN,
)


# --- label conversion ---------------------------------------------------


def test_derive_binary_labels_only_includes_trainable_kinds():
    """Contracts, infrastructure, unknown → dropped. Attacker → 1. Benign → 0.

    Notable: clean_exit_FUNDED counts as attacker (the agent actually
    sent funds there — it's part of the laundering trail). But
    clean_exit_UNUSED counts as BENIGN — the agent designated it as a
    potential exit but never touched it, so it's structurally
    benign-looking and the detector should NOT flag it. The split is
    intentional: it makes unused exits hard-negative distractors that
    test the detector's ability to distinguish *actually-used* from
    *merely-labeled*.
    """
    labels = {
        "0xa": LABEL_ATTACKER_SOURCE,
        "0xb": LABEL_ATTACKER_BURNER,
        "0xc": LABEL_BENIGN_USER,
        "0xd": LABEL_INFRASTRUCTURE,
        "0xe": LABEL_CONTRACT,
        "0xf": LABEL_UNKNOWN,
        "0xg": LABEL_CLEAN_EXIT_FUNDED,
        "0xh": LABEL_CLEAN_EXIT_UNUSED,
    }
    out = derive_binary_labels(labels)
    assert out["0xa"] == LABEL_ATTACKER_INT
    assert out["0xb"] == LABEL_ATTACKER_INT
    assert out["0xc"] == LABEL_BENIGN_INT
    # Infrastructure / contract / unknown are not training targets
    assert "0xd" not in out
    assert "0xe" not in out
    assert "0xf" not in out
    # Funded clean exit → attacker (agent actually used it)
    assert out["0xg"] == LABEL_ATTACKER_INT
    # Unused clean exit → benign distractor
    assert out["0xh"] == LABEL_BENIGN_INT


def test_derive_binary_labels_empty_input():
    assert derive_binary_labels({}) == {}


# --- LouvainDetector ----------------------------------------------------


def _two_cluster_graph():
    """Two cleanly separated clusters of 4 nodes each, joined by 1 bridge edge.

    Cluster A = attackers (a0..a3), cluster B = benign (b0..b3).
    """
    g = nx.MultiDiGraph()
    A = [f"a{i}" for i in range(4)]
    B = [f"b{i}" for i in range(4)]
    # Dense cluster A
    for i, u in enumerate(A):
        for v in A[i + 1:]:
            g.add_edge(u, v)
    # Dense cluster B
    for i, u in enumerate(B):
        for v in B[i + 1:]:
            g.add_edge(u, v)
    # Single bridge — keeps Louvain from collapsing them
    g.add_edge(A[0], B[0])
    return g, A, B


def test_louvain_fits_two_clean_clusters():
    """With two clean clusters, Louvain finds them and labels correctly."""
    g, A, B = _two_cluster_graph()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    det = LouvainDetector(seed=0).fit(g, train_labels)
    # Each cluster ends up in its own community
    cid_A = det.community_index["a0"]
    cid_B = det.community_index["b0"]
    assert cid_A != cid_B, "clusters should be in different communities"
    assert det.community_pred[cid_A] == 1
    assert det.community_pred[cid_B] == 0


def test_louvain_predict_matches_community_verdict():
    """predict() returns the community verdict for each queried node."""
    g, A, B = _two_cluster_graph()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    det = LouvainDetector(seed=0).fit(g, train_labels)
    preds = det.predict(A + B)
    assert preds[:4] == [1, 1, 1, 1]
    assert preds[4:] == [0, 0, 0, 0]


def test_louvain_predict_proba_returns_community_fraction():
    """A community with 3/4 attackers gets P(attacker) = 0.75."""
    g, A, B = _two_cluster_graph()
    # Label only 3 of 4 attackers; the 4th is "unknown" at training time
    train_labels = {a: 1 for a in A[:3]}
    train_labels.update({b: 0 for b in B})
    det = LouvainDetector(seed=0).fit(g, train_labels)
    cid_A = det.community_index["a0"]
    # 3 of 3 labelled training nodes in cluster A are attackers → 1.0
    assert det.community_proba[cid_A] == 1.0


def test_louvain_unknown_community_falls_back_to_prior():
    """A community with NO training labels → predict probability == prior."""
    g, A, B = _two_cluster_graph()
    # Only label cluster B; cluster A has no training labels
    train_labels = {b: 0 for b in B}
    det = LouvainDetector(seed=0, prior=0.3).fit(g, train_labels)
    cid_A = det.community_index["a0"]
    assert det.community_proba[cid_A] == 0.3
    # And predict respects prior threshold (0.3 < 0.5 → predict 0)
    assert det.community_pred[cid_A] == 0


def test_louvain_predict_unknown_address_falls_back_to_prior():
    """predict() on an address not in the training graph returns the prior.

    Uses prior=0.3 (not 0.5) on purpose — the exact 0.5 boundary is
    inherently ambiguous (>= vs > convention), so this test exercises
    the actual "falls back to prior" behaviour cleanly: prior=0.3
    → predict_proba=0.3, predict=0 (because 0.3 < 0.5 threshold).
    """
    g, A, B = _two_cluster_graph()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    det = LouvainDetector(seed=0, prior=0.3).fit(g, train_labels)
    # "z" wasn't in the training graph
    assert det.predict_proba(["z"])[0] == 0.3
    assert det.predict(["z"])[0] == 0


def test_louvain_handles_empty_graph():
    """Empty graph fit is a no-op; predict returns prior."""
    det = LouvainDetector(seed=0, prior=0.7).fit(nx.MultiDiGraph(), {})
    assert det.predict_proba(["anything"]) == [0.7]
    assert det.predict(["anything"]) == [1]


def test_louvain_unfitted_predict_raises():
    """Predicting without fit is a programming error → loud RuntimeError."""
    det = LouvainDetector(seed=0)
    with pytest.raises(RuntimeError, match="not fitted"):
        det.predict(["a"])


def test_louvain_is_a_detector():
    """LouvainDetector implements the Detector ABC."""
    assert isinstance(LouvainDetector(), Detector)


# --- PerExchangeDetector ------------------------------------------------


class _ExchangeViewStub:
    """Tiny stand-in for dataset.ExchangeView — avoids pulling dataset.py
    into a baseline test."""
    def __init__(self, name, visible_addresses, visible_subgraph):
        self.name = name
        self.visible_addresses = visible_addresses
        self.visible_subgraph = visible_subgraph


def test_per_exchange_fits_one_detector_per_view():
    """fit_per_view trains an independent detector for each ExchangeView."""
    g, A, B = _two_cluster_graph()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}

    # Exchange X sees cluster A; exchange Y sees cluster B
    view_X = _ExchangeViewStub("X", set(A), g.subgraph(A).copy())
    view_Y = _ExchangeViewStub("Y", set(B), g.subgraph(B).copy())

    multi = PerExchangeDetector(
        detector_factory=lambda: LouvainDetector(seed=0),
    ).fit_per_view([view_X, view_Y], train_labels)

    assert set(multi.detectors.keys()) == {"X", "Y"}
    # X's detector should have learned cluster A → attacker
    assert multi.detectors["X"].predict(A) == [1, 1, 1, 1]
    assert multi.detectors["Y"].predict(B) == [0, 0, 0, 0]


def test_per_exchange_predict_routes_to_visible_exchange():
    """A node visible only to one exchange uses that exchange's prediction."""
    g, A, B = _two_cluster_graph()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}

    view_X = _ExchangeViewStub("X", set(A), g.subgraph(A).copy())
    view_Y = _ExchangeViewStub("Y", set(B), g.subgraph(B).copy())

    multi = PerExchangeDetector(
        detector_factory=lambda: LouvainDetector(seed=0),
    ).fit_per_view([view_X, view_Y], train_labels)

    # a0 only visible to X → uses X's prediction (attacker)
    # b0 only visible to Y → uses Y's prediction (benign)
    preds = multi.predict(["a0", "b0"])
    assert preds == [1, 0]


def test_per_exchange_node_visible_nowhere_uses_prior():
    """Address visible to NO exchange → prior."""
    g, _, _ = _two_cluster_graph()
    view_X = _ExchangeViewStub("X", {"a0"}, g.subgraph(["a0"]).copy())
    multi = PerExchangeDetector(
        detector_factory=lambda: LouvainDetector(seed=0),
        prior=0.3,
    ).fit_per_view([view_X], {"a0": 1})
    # "ghost" not visible to X
    assert multi.predict_proba(["ghost"]) == [0.3]


def test_per_exchange_direct_fit_raises():
    """Calling fit() instead of fit_per_view() is a misuse → loud error."""
    multi = PerExchangeDetector(detector_factory=lambda: LouvainDetector())
    with pytest.raises(RuntimeError, match="fit_per_view"):
        multi.fit(nx.MultiDiGraph(), {})


# --- evaluate -----------------------------------------------------------


def test_evaluate_perfect_classifier_gives_f1_one():
    """All predictions correct → precision = recall = F1 = 1.0."""
    y_true = [1, 1, 1, 0, 0, 0]
    y_pred = [1, 1, 1, 0, 0, 0]
    m = evaluate(y_true, y_pred)
    assert m.precision == 1.0
    assert m.recall == 1.0
    assert m.f1 == 1.0
    assert m.accuracy == 1.0
    assert m.tp == 3 and m.tn == 3 and m.fp == 0 and m.fn == 0


def test_evaluate_confusion_counts_match():
    """tp/fp/tn/fn match a hand-computed example."""
    #                   t=1 t=1 t=1 t=0 t=0 t=0 t=0
    y_true = [1, 1, 1, 0, 0, 0, 0]
    y_pred = [1, 0, 1, 1, 0, 0, 0]
    m = evaluate(y_true, y_pred)
    assert m.tp == 2 and m.fn == 1
    assert m.fp == 1 and m.tn == 3
    # precision = 2/3, recall = 2/3, F1 = 2/3
    assert abs(m.precision - 2/3) < 1e-9
    assert abs(m.recall - 2/3) < 1e-9
    assert abs(m.f1 - 2/3) < 1e-9


def test_evaluate_no_positives_predicted_gives_zero_precision():
    """All predictions = 0 → precision undefined; we return 0.0."""
    m = evaluate([1, 1, 0, 0], [0, 0, 0, 0])
    assert m.precision == 0.0
    assert m.recall == 0.0
    assert m.f1 == 0.0


def test_evaluate_roc_auc_perfect_separator():
    """Probabilities that perfectly separate classes → AUC = 1.0."""
    y_true = [0, 0, 0, 1, 1, 1]
    y_proba = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]
    m = evaluate(y_true, [0, 0, 0, 1, 1, 1], y_proba)
    assert m.roc_auc == 1.0


def test_evaluate_roc_auc_random_classifier():
    """Random probabilities → AUC ~ 0.5 in expectation."""
    y_true = [0] * 50 + [1] * 50
    # Reversed probas: perfectly WRONG → AUC = 0.0
    y_proba = [0.9] * 50 + [0.1] * 50
    m = evaluate(y_true, [1] * 100, y_proba)
    assert m.roc_auc == 0.0


def test_evaluate_roc_auc_returns_none_for_single_class():
    """Single-class y_true → AUC is undefined."""
    m = evaluate([1, 1, 1], [1, 1, 0], [0.6, 0.7, 0.4])
    assert m.roc_auc is None


def test_evaluate_length_mismatch_raises():
    with pytest.raises(ValueError, match="length mismatch"):
        evaluate([1, 0], [1])
    with pytest.raises(ValueError, match="length mismatch"):
        evaluate([1, 0], [1, 0], [0.5])


def test_evaluate_empty_input():
    """Empty input → all-zero metrics, no crash."""
    m = evaluate([], [])
    assert m.n_total == 0
    assert m.f1 == 0.0


def test_pretty_print_renders_a_confusion_matrix():
    """pretty_print returns a string mentioning all the key numbers."""
    m = evaluate([1, 1, 0, 0], [1, 0, 1, 0])
    s = pretty_print(m)
    assert "Confusion matrix" in s
    assert "Attacker class" in s
    assert "Benign" in s


def test_metrics_dataclass_carries_per_class():
    """per_class dict has both classes with precision/recall/f1/support."""
    m = evaluate([1, 1, 0, 0], [1, 0, 1, 0])
    assert set(m.per_class.keys()) == {"attacker", "benign"}
    for cls in ("attacker", "benign"):
        for k in ("precision", "recall", "f1", "support"):
            assert k in m.per_class[cls]


def test_metrics_is_a_dataclass():
    """DetectorMetrics is a dataclass — confirms the public surface."""
    m = evaluate([1, 0], [1, 0])
    assert isinstance(m, DetectorMetrics)
