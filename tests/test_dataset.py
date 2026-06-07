"""Tests for the dataset combiner + partial-visibility split.

All structural — synthetic run dirs written to tmp_path. No Anvil,
no API. Covers: label priority resolution, node/edge union semantics,
attacker-vs-benign separation, exchange split correctness (assignment,
contracts shared/not, determinism, induced subgraph property), and
train/val/test split (sums, determinism, disjointness, edge cases).
"""
from __future__ import annotations

import json

import networkx as nx
import pytest

from aml.detectors.dataset import (
    CombinedDataset,
    ExchangeView,
    _highest_priority_label,
    combine_runs,
    partial_visibility_split,
    train_val_test_split,
)
from aml.detectors.graph import (
    LABEL_ATTACKER_BURNER,
    LABEL_ATTACKER_SOURCE,
    LABEL_BENIGN_USER,
    LABEL_CLEAN_EXIT_FUNDED,
    LABEL_CLEAN_EXIT_UNUSED,
    LABEL_CONTRACT,
    LABEL_INFRASTRUCTURE,
)


# --- synthetic run-dir helpers ------------------------------------------


def _addr(n: int) -> str:
    """Deterministic test address (0x followed by 40-char hex of n)."""
    return "0x" + f"{n:040x}"


def _write_run(
    base, run_name, *, kind, addresses, trace=None, meta_extras=None,
):
    """Write a synthetic run dir; return its path."""
    d = base / run_name
    d.mkdir()
    meta = {"run_name": run_name}
    if kind == "benign":
        meta["kind"] = "benign"
    else:
        meta["scenario"] = "defi-exploit"
    if meta_extras:
        meta.update(meta_extras)
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "addresses.json").write_text(json.dumps(addresses))
    with (d / "chain_trace.jsonl").open("w") as f:
        for rec in (trace or []):
            f.write(json.dumps(rec) + "\n")
    return d


def _attacker_run(base, name, *, source, burners, exits_funded,
                  exits_unused=None, operator=None, contracts=None, trace=None):
    """Shortcut to write an attacker-style run with the expected schema."""
    operator = operator or _addr(900)
    contracts = contracts or {
        "usdt": _addr(997), "pool": _addr(998), "tornado": _addr(999),
    }
    exits_unused = exits_unused or []
    addresses = {
        "source_wallet": source,
        "operator_wallet": operator,
        "burners_generated_during_campaign": burners,
        "clean_exit_wallets": exits_funded + exits_unused,
        "clean_exits_funded": exits_funded,
        "attacker_wallets": [source] + burners,
        "contracts": contracts,
    }
    return _write_run(base, name, kind="attacker", addresses=addresses, trace=trace)


def _benign_run(base, name, *, users, operator=None, contracts=None, trace=None):
    """Shortcut to write a benign-style run with the expected schema."""
    operator = operator or _addr(900)
    contracts = contracts or {"usdt": _addr(997), "pool": _addr(998)}
    labels = {u: "benign_user" for u in users}
    labels[operator] = "infrastructure"
    for addr in contracts.values():
        if addr:
            labels[addr] = "contract"
    addresses = {
        "benign_users": users,
        "operator_wallet": operator,
        "contracts": contracts,
        "labels": labels,
    }
    return _write_run(base, name, kind="benign", addresses=addresses, trace=trace)


def _tx(tx_from, tx_to, *, tx_hash, block=1, value_wei=0, events=None):
    return {
        "tx_hash": tx_hash, "block": block, "status": 1,
        "from": tx_from, "to": tx_to,
        "value_wei": str(value_wei), "value_eth": value_wei / 10**18,
        "gas_used": 21000, "events": events or [],
    }


# --- label priority -----------------------------------------------------


def test_highest_priority_label_contracts_win_over_everything():
    """Contract beats any role label."""
    assert _highest_priority_label(
        [LABEL_CONTRACT, LABEL_ATTACKER_SOURCE, LABEL_BENIGN_USER],
    ) == LABEL_CONTRACT


def test_highest_priority_label_attacker_beats_benign():
    """Any attacker role beats benign_user."""
    assert _highest_priority_label(
        [LABEL_BENIGN_USER, LABEL_ATTACKER_BURNER],
    ) == LABEL_ATTACKER_BURNER


def test_highest_priority_label_source_beats_burner():
    """attacker_source is more specific than attacker_burner."""
    assert _highest_priority_label(
        [LABEL_ATTACKER_BURNER, LABEL_ATTACKER_SOURCE],
    ) == LABEL_ATTACKER_SOURCE


# --- combine_runs -------------------------------------------------------


def test_combine_empty_input_returns_empty_dataset():
    """No runs in → empty CombinedDataset with empty graph and labels."""
    ds = combine_runs([])
    assert isinstance(ds, CombinedDataset)
    assert ds.graph.number_of_nodes() == 0
    assert ds.graph.number_of_edges() == 0
    assert ds.node_labels == {}
    assert ds.runs == []
    assert ds.attacker_run_names == []
    assert ds.benign_run_names == []


def test_combine_runs_separates_attacker_and_benign_names(tmp_path):
    """attacker_run_names / benign_run_names properties bucket by run kind."""
    _attacker_run(tmp_path, "atk_01", source=_addr(1),
                  burners=[_addr(10)], exits_funded=[_addr(2)])
    _benign_run(tmp_path, "ben_01", users=[_addr(50), _addr(51)])
    _attacker_run(tmp_path, "atk_02", source=_addr(3),
                  burners=[_addr(11)], exits_funded=[_addr(4)])
    ds = combine_runs(sorted(tmp_path.iterdir()))
    assert sorted(ds.attacker_run_names) == ["atk_01", "atk_02"]
    assert ds.benign_run_names == ["ben_01"]
    assert sorted(ds.all_run_names) == ["atk_01", "atk_02", "ben_01"]


def test_combine_unions_shared_addresses_into_one_node(tmp_path):
    """The shared operator/contract appears in both runs → 1 node, runs attr lists both."""
    shared_op = _addr(900)
    shared_usdt = _addr(997)
    _attacker_run(tmp_path, "atk_01", source=_addr(1),
                  burners=[_addr(10)], exits_funded=[_addr(2)],
                  operator=shared_op,
                  contracts={"usdt": shared_usdt, "pool": _addr(998),
                             "tornado": _addr(999)})
    _benign_run(tmp_path, "ben_01", users=[_addr(50)],
                operator=shared_op,
                contracts={"usdt": shared_usdt, "pool": _addr(998)})
    ds = combine_runs(sorted(tmp_path.iterdir()))

    # Operator appears in both runs → 1 node, both run names in attr
    assert shared_op in ds.graph
    assert sorted(ds.graph.nodes[shared_op]["runs"]) == ["atk_01", "ben_01"]
    # USDT contract too
    assert shared_usdt in ds.graph
    assert sorted(ds.graph.nodes[shared_usdt]["runs"]) == ["atk_01", "ben_01"]


def test_combine_resolves_label_conflicts_by_priority(tmp_path):
    """Same address labelled differently in 2 runs → highest-priority label wins."""
    # An address that's a benign_user in one run and a contract in another
    # (contrived but exercises the priority machinery)
    addr = _addr(123)
    _benign_run(tmp_path, "ben_01", users=[addr])   # → benign_user
    _attacker_run(tmp_path, "atk_01",
                  source=_addr(1), burners=[_addr(10)],
                  exits_funded=[_addr(2)],
                  contracts={"usdt": addr, "pool": _addr(998),   # → contract
                             "tornado": _addr(999)})
    ds = combine_runs(sorted(tmp_path.iterdir()))
    assert ds.node_labels[addr] == LABEL_CONTRACT
    assert ds.graph.nodes[addr]["label"] == LABEL_CONTRACT


def test_combine_tags_edges_with_source_run(tmp_path):
    """Each edge carries the run_name it came from for downstream filtering."""
    a, b = _addr(1), _addr(2)
    _attacker_run(tmp_path, "atk_01",
                  source=a, burners=[b], exits_funded=[_addr(3)],
                  trace=[_tx(a, b, tx_hash="0x1", value_wei=10**18)])
    ds = combine_runs(sorted(tmp_path.iterdir()))
    a_b_edges = [d for _, _, d in ds.graph.edges(data=True)
                 if d.get("source_run") == "atk_01"]
    assert len(a_b_edges) >= 1


def test_combine_two_attacker_runs_accumulates_edges(tmp_path):
    """Two runs each contributing an a→b edge → 2 parallel edges in combined graph."""
    a, b = _addr(1), _addr(10)
    _attacker_run(tmp_path, "atk_01",
                  source=a, burners=[b], exits_funded=[_addr(2)],
                  trace=[_tx(a, b, tx_hash="0x1", value_wei=10**18)])
    _attacker_run(tmp_path, "atk_02",
                  source=a, burners=[b], exits_funded=[_addr(2)],
                  trace=[_tx(a, b, tx_hash="0x2", value_wei=2 * 10**18)])
    ds = combine_runs(sorted(tmp_path.iterdir()))
    parallel = list(ds.graph[a][b].values())
    assert len(parallel) == 2
    sources = {d["source_run"] for d in parallel}
    assert sources == {"atk_01", "atk_02"}


# --- partial_visibility_split -------------------------------------------


def _small_dataset(tmp_path):
    """Build a tiny combined dataset useful for visibility-split tests."""
    _attacker_run(tmp_path, "atk_01",
                  source=_addr(1), burners=[_addr(10), _addr(11)],
                  exits_funded=[_addr(2)],
                  exits_unused=[_addr(3)])
    _benign_run(tmp_path, "ben_01", users=[_addr(50), _addr(51), _addr(52)])
    return combine_runs(sorted(tmp_path.iterdir()))


def test_partial_visibility_split_assigns_every_non_contract_address(tmp_path):
    """Every non-contract address appears in exactly one exchange (default mode)."""
    ds = _small_dataset(tmp_path)
    views = partial_visibility_split(ds, num_exchanges=3, seed=1)
    # Contracts are shared by default, so they appear in every view.
    contracts = {
        addr for addr, lab in ds.node_labels.items() if lab == LABEL_CONTRACT
    }
    non_contracts = set(ds.node_labels) - contracts
    counts: dict[str, int] = {addr: 0 for addr in non_contracts}
    for view in views:
        for addr in view.visible_addresses:
            if addr in non_contracts:
                counts[addr] += 1
    for addr, c in counts.items():
        assert c == 1, f"address {addr} visible to {c} exchanges (expected 1)"


def test_partial_visibility_split_contracts_shared_by_default(tmp_path):
    """Contracts (USDT, pool, tornado) appear in EVERY exchange when shared=True."""
    ds = _small_dataset(tmp_path)
    views = partial_visibility_split(ds, num_exchanges=3, seed=1, contracts_shared=True)
    contracts = {
        addr for addr, lab in ds.node_labels.items() if lab == LABEL_CONTRACT
    }
    assert contracts, "test fixture should have at least one contract"
    for view in views:
        assert contracts <= view.visible_addresses, (
            f"{view.name} missing shared contracts: "
            f"{contracts - view.visible_addresses}"
        )


def test_partial_visibility_split_contracts_disjoint_when_not_shared(tmp_path):
    """contracts_shared=False → each contract assigned to exactly one exchange."""
    ds = _small_dataset(tmp_path)
    views = partial_visibility_split(ds, num_exchanges=3, seed=1, contracts_shared=False)
    contracts = {
        addr for addr, lab in ds.node_labels.items() if lab == LABEL_CONTRACT
    }
    for c in contracts:
        seen_in = sum(1 for v in views if c in v.visible_addresses)
        assert seen_in == 1, f"contract {c} visible in {seen_in} exchanges"


def test_partial_visibility_split_deterministic_with_seed(tmp_path):
    """Same seed → identical address assignments across two runs of the split."""
    ds = _small_dataset(tmp_path)
    v1 = partial_visibility_split(ds, num_exchanges=3, seed=42)
    v2 = partial_visibility_split(ds, num_exchanges=3, seed=42)
    assert [v.name for v in v1] == [v.name for v in v2]
    for a, b in zip(v1, v2):
        assert a.visible_addresses == b.visible_addresses


def test_partial_visibility_split_subgraph_is_induced(tmp_path):
    """visible_subgraph contains only edges where BOTH endpoints are visible."""
    # Build a dataset with a known edge a→b
    a, b = _addr(1), _addr(10)
    _attacker_run(tmp_path, "atk_01",
                  source=a, burners=[b], exits_funded=[_addr(2)],
                  trace=[_tx(a, b, tx_hash="0xab", value_wei=10**18)])
    ds = combine_runs(sorted(tmp_path.iterdir()))
    views = partial_visibility_split(ds, num_exchanges=4, seed=7)
    for view in views:
        for u, v, _ in view.visible_subgraph.edges(data=True):
            assert u in view.visible_addresses
            assert v in view.visible_addresses


def test_partial_visibility_split_rejects_bad_num_exchanges(tmp_path):
    """num_exchanges outside [1, 26] is a programming error → ValueError."""
    ds = _small_dataset(tmp_path)
    with pytest.raises(ValueError, match="num_exchanges"):
        partial_visibility_split(ds, num_exchanges=0)
    with pytest.raises(ValueError, match="num_exchanges"):
        partial_visibility_split(ds, num_exchanges=27)


# --- train_val_test_split -----------------------------------------------


def test_train_val_test_split_sums_to_input_and_is_disjoint():
    """Splits cover every name exactly once."""
    names = [f"run_{i:02d}" for i in range(20)]
    train, val, test = train_val_test_split(names, seed=0)
    assert set(train) | set(val) | set(test) == set(names)
    assert len(train) + len(val) + len(test) == len(names)
    assert not (set(train) & set(val))
    assert not (set(val) & set(test))
    assert not (set(train) & set(test))


def test_train_val_test_split_respects_ratios():
    """20 runs at (0.7, 0.15, 0.15) → 14 / 3 / 3."""
    names = [f"run_{i:02d}" for i in range(20)]
    train, val, test = train_val_test_split(names, seed=0)
    assert len(train) == 14
    assert len(val) == 3
    assert len(test) == 3


def test_train_val_test_split_deterministic_with_seed():
    """Same seed → same split."""
    names = [f"run_{i:02d}" for i in range(20)]
    a = train_val_test_split(names, seed=42)
    b = train_val_test_split(names, seed=42)
    assert a == b


def test_train_val_test_split_different_seeds_yield_different_splits():
    """Different seeds → different shuffles (with high probability for N=20)."""
    names = [f"run_{i:02d}" for i in range(20)]
    a = train_val_test_split(names, seed=1)
    b = train_val_test_split(names, seed=2)
    # Train sets should not be identical (probability ≈ 1/C(20,14))
    assert a[0] != b[0]


def test_train_val_test_split_rejects_invalid_ratios():
    """ratios that don't sum to 1, or are negative, raise ValueError."""
    with pytest.raises(ValueError, match="sum to 1"):
        train_val_test_split(["a", "b"], ratios=(0.5, 0.5, 0.5))
    with pytest.raises(ValueError, match="non-negative"):
        train_val_test_split(["a", "b"], ratios=(1.5, -0.5, 0.0))


def test_train_val_test_split_handles_small_n_gracefully():
    """N=3 + (0.7, 0.15, 0.15) → train=2, val=0, test=1. No crash."""
    names = ["a", "b", "c"]
    train, val, test = train_val_test_split(names, seed=0)
    assert len(train) == 2
    assert len(val) == 0
    assert len(test) == 1
    assert set(train) | set(val) | set(test) == set(names)


def test_train_val_test_split_empty_input_returns_three_empty_lists():
    """Empty input doesn't crash; returns three empty lists."""
    train, val, test = train_val_test_split([], seed=0)
    assert train == [] and val == [] and test == []
