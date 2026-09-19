"""Tests for the multi-agent collaborative detector.

Structural — synthetic graphs, no Anvil, no API. Two layers of
testing:
  1. The clustering primitive (cluster_by_similarity) and the
     evaluation metrics (ARI, actor_clustering_metrics) — pure numpy
     + networkx, always run.
  2. The MultiAgentDetector class itself — uses LouvainDetector under
     the hood (no torch dep), so always runs. GCN-backed integration
     is left to the user's manual end-to-end smoke (it'd just compose
     PR #37's tests).

The thesis-novelty assertion ("multi-agent does better than baselines
in the partial-visibility setup") is a runtime evaluation outcome,
not a unit test — too data-dependent to bake into pytest.
"""
from __future__ import annotations

import networkx as nx
import numpy as np
import pytest

from aml.detectors.baselines import Detector, LouvainDetector
from aml.detectors.multi_agent import (
    NO_CLUSTER,
    MultiAgentDetector,
    actor_clustering_metrics,
    adjusted_rand_index,
    cluster_by_similarity,
    true_actor_clusters,
)


# --- helpers ------------------------------------------------------------


class _ExchangeViewStub:
    """Stand-in for dataset.ExchangeView (avoids importing dataset)."""
    def __init__(self, name, visible_addresses, visible_subgraph):
        self.name = name
        self.visible_addresses = visible_addresses
        self.visible_subgraph = visible_subgraph


class _RunDataStub:
    """Stand-in for graph.RunData (avoids importing dataset for ground-truth tests)."""
    def __init__(self, kind, addresses):
        self.kind = kind
        self.addresses = addresses


def _two_cluster_views():
    """Build 2 exchange views, each with one cluster of 4 nodes."""
    A = [f"a{i}" for i in range(4)]
    B = [f"b{i}" for i in range(4)]
    gA = nx.MultiDiGraph()
    for i, u in enumerate(A):
        for v in A[i + 1:]:
            gA.add_edge(u, v, kind="transfer_eth", asset="ETH", value=1.0)
    gB = nx.MultiDiGraph()
    for i, u in enumerate(B):
        for v in B[i + 1:]:
            gB.add_edge(u, v, kind="transfer_eth", asset="ETH", value=1.0)
    return (
        _ExchangeViewStub("X", set(A), gA),
        _ExchangeViewStub("Y", set(B), gB),
        A, B,
    )


# --- cluster_by_similarity ----------------------------------------------


def test_cluster_by_similarity_empty_input_returns_empty():
    assert cluster_by_similarity({}) == {}


def test_cluster_by_similarity_identical_features_cluster_together():
    """Two addresses with identical feature vectors → one cluster."""
    f = np.array([1.0, 2.0, 3.0])
    clusters = cluster_by_similarity(
        {"a": f, "b": f.copy()}, threshold=0.99,
    )
    assert clusters["a"] == clusters["b"]


def test_cluster_by_similarity_orthogonal_features_stay_separate():
    """Two addresses with orthogonal features → separate clusters."""
    clusters = cluster_by_similarity(
        {"a": np.array([1.0, 0.0]), "b": np.array([0.0, 1.0])},
        threshold=0.5,
    )
    assert clusters["a"] != clusters["b"]


def test_cluster_by_similarity_threshold_controls_grouping():
    """Higher threshold → stricter, fewer connections, more clusters."""
    fa = np.array([1.0, 0.0])
    fb = np.array([1.0, 0.05])   # cosine ~ 0.9988
    fc = np.array([0.0, 1.0])    # orthogonal to both
    feats = {"a": fa, "b": fb, "c": fc}

    # Loose threshold — a+b cluster together
    loose = cluster_by_similarity(feats, threshold=0.9)
    assert loose["a"] == loose["b"]
    assert loose["c"] != loose["a"]

    # Strict threshold — a+b also separate
    strict = cluster_by_similarity(feats, threshold=0.9999)
    assert strict["a"] != strict["b"]


def test_cluster_by_similarity_zero_norm_features_are_singletons():
    """All-zero feature vectors → not similar to anything → singletons."""
    clusters = cluster_by_similarity(
        {"a": np.array([0.0, 0.0]),
         "b": np.array([0.0, 0.0]),
         "c": np.array([1.0, 1.0])},
        threshold=0.5,
    )
    # Zero-norm should be in their own clusters (similarity to all = 0)
    assert clusters["a"] != clusters["c"]
    assert clusters["b"] != clusters["c"]


# --- MultiAgentDetector -------------------------------------------------


def test_multi_agent_detector_is_a_detector():
    """MultiAgentDetector is a subclass of Detector ABC."""
    assert isinstance(
        MultiAgentDetector(detector_factory=lambda: LouvainDetector()),
        Detector,
    )


def test_multi_agent_detector_fit_per_view_trains_binary_and_clusters():
    """fit_per_view populates both binary detectors and actor_clusters."""
    view_X, view_Y, A, B = _two_cluster_views()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    det = MultiAgentDetector(
        detector_factory=lambda: LouvainDetector(seed=0),
    ).fit_per_view([view_X, view_Y], train_labels)
    # Binary detectors trained — predict works on visible addresses
    preds = det.predict(A + B)
    assert len(preds) == 8
    assert all(p in (0, 1) for p in preds)
    # Actor clusters populated — every address gets a cluster id
    actors = det.predict_actors(A + B)
    assert all(a != NO_CLUSTER for a in actors)


def test_multi_agent_detector_predict_routes_through_per_exchange():
    """predict() on clean clusters → A predicted attacker, B predicted benign."""
    view_X, view_Y, A, B = _two_cluster_views()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    det = MultiAgentDetector(
        detector_factory=lambda: LouvainDetector(seed=0),
    ).fit_per_view([view_X, view_Y], train_labels)
    assert det.predict(A) == [1, 1, 1, 1]
    assert det.predict(B) == [0, 0, 0, 0]


def test_multi_agent_detector_predict_actors_unknown_address_returns_sentinel():
    """An address visible to no exchange → NO_CLUSTER (-1)."""
    view_X, view_Y, A, B = _two_cluster_views()
    train_labels = {**{a: 1 for a in A}, **{b: 0 for b in B}}
    det = MultiAgentDetector(
        detector_factory=lambda: LouvainDetector(seed=0),
    ).fit_per_view([view_X, view_Y], train_labels)
    assert det.predict_actors(["ghost-address"]) == [NO_CLUSTER]


def test_multi_agent_detector_unfitted_predict_raises():
    """All three predict methods raise before fit_per_view is called."""
    det = MultiAgentDetector(detector_factory=lambda: LouvainDetector())
    with pytest.raises(RuntimeError, match="not fitted"):
        det.predict(["a"])
    with pytest.raises(RuntimeError, match="not fitted"):
        det.predict_proba(["a"])
    with pytest.raises(RuntimeError, match="not fitted"):
        det.predict_actors(["a"])


def test_multi_agent_detector_direct_fit_raises():
    """Calling fit() instead of fit_per_view() is a misuse → loud error."""
    det = MultiAgentDetector(detector_factory=lambda: LouvainDetector())
    with pytest.raises(RuntimeError, match="fit_per_view"):
        det.fit(nx.MultiDiGraph(), {})


# --- true_actor_clusters ------------------------------------------------


def test_true_actor_clusters_groups_attacker_run_addresses():
    """All addresses in an attacker run get the SAME actor id."""
    run = _RunDataStub("attacker", {
        "source_wallet": "src",
        "burners_generated_during_campaign": ["b1", "b2", "b3"],
        "clean_exits_funded": ["e1", "e2"],
    })
    out = true_actor_clusters([run])
    ids = {out[a] for a in ["src", "b1", "b2", "b3", "e1", "e2"]}
    assert len(ids) == 1


def test_true_actor_clusters_separates_attacker_runs():
    """Different attacker runs get different actor ids."""
    run1 = _RunDataStub("attacker", {
        "source_wallet": "src1", "burners_generated_during_campaign": ["b1"],
    })
    run2 = _RunDataStub("attacker", {
        "source_wallet": "src2", "burners_generated_during_campaign": ["b2"],
    })
    out = true_actor_clusters([run1, run2])
    assert out["src1"] != out["src2"]
    assert out["src1"] == out["b1"]
    assert out["src2"] == out["b2"]


def test_true_actor_clusters_benign_users_are_singletons():
    """Each benign user gets its own unique actor id."""
    run = _RunDataStub("benign", {"benign_users": ["u1", "u2", "u3"]})
    out = true_actor_clusters([run])
    ids = {out["u1"], out["u2"], out["u3"]}
    assert len(ids) == 3   # all distinct


def test_true_actor_clusters_excludes_contracts_and_infra():
    """Contracts and infrastructure aren't actors — excluded from output."""
    run = _RunDataStub("attacker", {
        "source_wallet": "src",
        "operator_wallet": "operator",   # not an actor in our framing
        "contracts": {"usdt": "usdt-addr"},
    })
    out = true_actor_clusters([run])
    assert "operator" not in out
    assert "usdt-addr" not in out
    assert "src" in out


# --- adjusted_rand_index ------------------------------------------------


def test_ari_perfect_clustering_is_one():
    """Identical clusterings → ARI = 1.0."""
    y = [0, 0, 1, 1, 2, 2]
    assert adjusted_rand_index(y, y) == pytest.approx(1.0)


def test_ari_relabelled_clustering_is_one():
    """Cluster IDs don't matter, only the partition does."""
    y_true = [0, 0, 1, 1, 2, 2]
    y_pred = [7, 7, 3, 3, 9, 9]   # same partition, different ids
    assert adjusted_rand_index(y_true, y_pred) == pytest.approx(1.0)


def test_ari_completely_wrong_clustering_is_negative_or_zero():
    """A bad clustering (worse than random) gets ARI <= 0."""
    y_true = [0, 0, 0, 1, 1, 1]
    y_pred = [0, 1, 0, 1, 0, 1]   # alternating, ignores true structure
    score = adjusted_rand_index(y_true, y_pred)
    assert score <= 0.1


def test_ari_length_mismatch_raises():
    with pytest.raises(ValueError, match="length mismatch"):
        adjusted_rand_index([0, 1], [0])


def test_ari_trivial_single_point_returns_one():
    """n=1 → trivially identical, return 1.0."""
    assert adjusted_rand_index([0], [0]) == 1.0


# --- actor_clustering_metrics -------------------------------------------


def test_actor_metrics_perfect_match_gives_one():
    """Predicted clustering identical to ground truth → ARI = 1, homog = 1."""
    true_c = {"a": 0, "b": 0, "c": 1, "d": 1}
    pred_c = {"a": 0, "b": 0, "c": 1, "d": 1}
    m = actor_clustering_metrics(true_c, pred_c)
    assert m["ari"] == pytest.approx(1.0)
    assert m["homogeneity"] == 1.0
    assert m["completeness"] == 1.0
    assert m["n_addresses"] == 4


def test_actor_metrics_homogeneity_when_pred_splits_true():
    """Predicted cluster splits a true actor into two → completeness < 1
    but each pred cluster is still pure (homogeneity = 1)."""
    true_c = {"a": 0, "b": 0, "c": 0, "d": 0}
    pred_c = {"a": 0, "b": 0, "c": 1, "d": 1}   # splits one actor in two
    m = actor_clustering_metrics(true_c, pred_c)
    assert m["homogeneity"] == 1.0
    assert m["completeness"] < 1.0


def test_actor_metrics_completeness_when_pred_merges_two_true():
    """Two true actors merged into one predicted cluster → completeness
    of each true cluster is fine but homogeneity drops."""
    true_c = {"a": 0, "b": 0, "c": 1, "d": 1}
    pred_c = {"a": 0, "b": 0, "c": 0, "d": 0}   # merges 2 actors into 1
    m = actor_clustering_metrics(true_c, pred_c)
    assert m["completeness"] == 1.0
    assert m["homogeneity"] < 1.0


def test_actor_metrics_no_common_addresses_returns_zeros():
    """No address in both inputs → metrics return Nones / 0."""
    m = actor_clustering_metrics({"a": 0}, {"b": 0})
    assert m["n_addresses"] == 0
    assert m["ari"] is None
    assert m["homogeneity"] is None


# --- LLMDefenderCoordinator tests (Task #15) ---------------------------
# Tutor-validated architecture: ML filter → LLM agent. Mocks the LLM
# client so tests run offline / at zero API cost.

from aml.detectors.multi_agent import (
    LLMDefenderCoordinator,
    _build_llm_user_prompt,
    _parse_llm_clusters,
)
from aml.detectors.gnn import FEATURE_DIM


class _MockLLMResult:
    """Duck-types the CallResult returned by LLMClient.complete."""
    def __init__(self, text, input_tokens=100, output_tokens=50, cost=0.001):
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost


class _MockLLMClient:
    """Injectable client that returns a preset JSON payload."""
    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = []

    def complete(self, prompt=None, system=None, model=None, max_tokens=None):
        self.calls.append({
            "prompt": prompt, "system": system,
            "model": model, "max_tokens": max_tokens,
        })
        return _MockLLMResult(self.response_text)


def test_parse_llm_clusters_basic():
    """LLM returns clean JSON → parsed into {addr → cluster_id} + reasoning."""
    text = (
        '{"actor_clusters": ['
        '{"cluster_id": 0, "addresses": ["0xabc", "0xdef"], "reasoning": "same fp"},'
        '{"cluster_id": 1, "addresses": ["0xghi"], "reasoning": "singleton"}'
        '], "overall_reasoning": "clustered by fingerprint similarity"}'
    )
    clusters, reason = _parse_llm_clusters(text, {"0xabc", "0xdef", "0xghi"})
    assert clusters == {"0xabc": 0, "0xdef": 0, "0xghi": 1}
    assert reason == "clustered by fingerprint similarity"


def test_parse_llm_clusters_markdown_fence():
    """LLM wraps JSON in ```json ... ``` fence → still parsed."""
    text = (
        "Here is my analysis:\n"
        "```json\n"
        '{"actor_clusters": [{"cluster_id": 0, "addresses": ["0xa"], "reasoning": "x"}],'
        ' "overall_reasoning": "y"}\n'
        "```"
    )
    clusters, _ = _parse_llm_clusters(text, {"0xa"})
    assert clusters == {"0xa": 0}


def test_parse_llm_clusters_malformed_returns_empty():
    """Unparseable output → empty dict (caller triggers cosine fallback)."""
    clusters, reason = _parse_llm_clusters("this is not json at all", {"0xa"})
    assert clusters == {}
    assert reason == ""


def test_parse_llm_clusters_filters_hallucinated_addresses():
    """LLM invents addresses NOT in valid_addresses → those get dropped."""
    text = (
        '{"actor_clusters": ['
        '{"cluster_id": 0, "addresses": ["0xreal", "0xhallucinated"], "reasoning": "x"}'
        '], "overall_reasoning": "y"}'
    )
    clusters, _ = _parse_llm_clusters(text, {"0xreal"})
    assert clusters == {"0xreal": 0}


def test_build_llm_user_prompt_includes_exchange_and_confidence():
    """Prompt must contain exchange name + local_conf + FULL address + features."""
    import numpy as np
    fp = np.zeros(FEATURE_DIM, dtype=np.float32)
    fp[0] = 3.5   # in_degree
    fp[3] = 2.1   # log_eth_in
    full_addr = "0x1234567890abcdef1234567890abcdef12345678"
    prompt = _build_llm_user_prompt({
        "exchange_A": [(full_addr, fp, 0.87)],
    })
    assert "exchange_A" in prompt
    assert "local_conf=0.87" in prompt
    assert "in_degree=3.50" in prompt
    assert "log_eth_in=2.10" in prompt
    # FULL address must appear in prompt (not truncated) so LLM can echo it
    # back in its JSON output and the parser can match against the full-address
    # set. Truncation with `addr[:10]...addr[-4:]` caused a parse failure bug
    # discovered 2026-07-19 — see multi_agent.py comment.
    assert full_addr in prompt
    assert "..." not in prompt   # no truncation


def test_build_llm_user_prompt_all_zero_fingerprint():
    """Address with all-zero fingerprint → note about isolated node."""
    import numpy as np
    fp = np.zeros(FEATURE_DIM, dtype=np.float32)
    prompt = _build_llm_user_prompt({
        "exchange_A": [("0x1234567890abcdef1234", fp, 0.5)],
    })
    assert "isolated" in prompt


def test_llm_defender_fit_raises_without_views():
    """fit(graph, labels) must raise — force callers to use fit_per_view."""
    import pytest
    det = LLMDefenderCoordinator(
        detector_factory=lambda: None,
        llm_client=_MockLLMClient("{}"),
    )
    with pytest.raises(RuntimeError, match="fit_per_view"):
        det.fit(graph=None, train_labels={})


def test_llm_defender_predict_raises_before_fit():
    """predict() before fit → RuntimeError with helpful message."""
    import pytest
    det = LLMDefenderCoordinator(
        detector_factory=lambda: None,
        llm_client=_MockLLMClient("{}"),
    )
    with pytest.raises(RuntimeError, match="not fitted"):
        det.predict(["0xa"])


def test_llm_defender_no_flagged_short_circuits_llm_call():
    """If per-view classifiers flag nothing → LLM is NOT called, cost=$0."""
    import networkx as nx
    from aml.detectors.baselines import LouvainDetector
    from aml.detectors.dataset import ExchangeView

    view = ExchangeView(
        name="exchange_A",
        visible_addresses=set(),
        visible_subgraph=nx.MultiDiGraph(),
    )
    mock = _MockLLMClient("SHOULD NOT BE CALLED")
    det = LLMDefenderCoordinator(
        detector_factory=lambda: LouvainDetector(),
        llm_client=mock,
    )
    det.fit_per_view([view], train_labels={})
    assert len(mock.calls) == 0, "LLM should NOT be called when nothing is flagged"
    assert det.usage["cost_usd"] == 0.0
    assert det.actor_clusters == {}
