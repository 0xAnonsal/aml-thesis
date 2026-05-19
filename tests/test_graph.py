"""Tests for the run-directory loader + transaction-graph extractor.

All structural — synthetic in-memory artifacts written to tmp_path. No
Anvil, no API. Each test focuses on one slice of the extraction logic:
label synthesis, edge generation per event type, distractor handling,
multi-edge preservation, summary stats.
"""
from __future__ import annotations

import json

import networkx as nx
import pytest

from aml.detectors.graph import (
    LABEL_ATTACKER_BURNER,
    LABEL_ATTACKER_SOURCE,
    LABEL_BENIGN_USER,
    LABEL_CLEAN_EXIT_FUNDED,
    LABEL_CLEAN_EXIT_UNUSED,
    LABEL_CONTRACT,
    LABEL_INFRASTRUCTURE,
    LABEL_UNKNOWN,
    RunData,
    load_run,
    summarize_graph,
    synthesize_attacker_labels,
    to_networkx,
)


# --- handy helpers for synthetic artifact dirs --------------------------


def _addr(n: int) -> str:
    """Make a deterministic test address: 0x<n>...<n> (lower hex)."""
    h = f"{n:040x}"
    return "0x" + h


def _write_run(
    tmp_path, *, kind: str, meta_extras=None, addresses=None, trace=None,
    campaign=None,
):
    """Write a synthetic run-dir under tmp_path; return the path."""
    run_dir = tmp_path / "synthetic-run"
    run_dir.mkdir()
    meta = {"kind": kind} if kind == "benign" else {"scenario": "defi-exploit"}
    if meta_extras:
        meta.update(meta_extras)
    (run_dir / "meta.json").write_text(json.dumps(meta))
    (run_dir / "addresses.json").write_text(json.dumps(addresses or {}))
    with (run_dir / "chain_trace.jsonl").open("w") as f:
        for rec in (trace or []):
            f.write(json.dumps(rec) + "\n")
    if campaign is not None:
        (run_dir / "campaign.json").write_text(json.dumps(campaign))
    return run_dir


# --- load_run -----------------------------------------------------------


def test_load_run_benign_uses_supplied_labels_dict(tmp_path):
    """Benign runs ship `labels` directly — extractor must trust it as-is."""
    bob, alice, op = _addr(1), _addr(2), _addr(3)
    usdt = _addr(99)
    run = load_run(_write_run(
        tmp_path, kind="benign",
        addresses={
            "benign_users": [bob, alice],
            "operator_wallet": op,
            "contracts": {"usdt": usdt, "pool": None},
            "labels": {
                bob: "benign_user", alice: "benign_user",
                op: "infrastructure", usdt: "contract",
            },
        },
        trace=[],
    ))
    assert run.kind == "benign"
    assert run.labels[bob] == LABEL_BENIGN_USER
    assert run.labels[alice] == LABEL_BENIGN_USER
    assert run.labels[op] == LABEL_INFRASTRUCTURE
    assert run.labels[usdt] == LABEL_CONTRACT
    assert run.campaign is None


def test_load_run_attacker_synthesizes_labels_from_structured_fields(tmp_path):
    """Attacker runs ship structured fields, not a labels dict — must collapse."""
    src, op = _addr(1), _addr(2)
    burner1, burner2 = _addr(3), _addr(4)
    exit_funded, exit_unused = _addr(5), _addr(6)
    usdt, pool, tornado = _addr(97), _addr(98), _addr(99)
    run = load_run(_write_run(
        tmp_path, kind="attacker",
        addresses={
            "source_wallet": src,
            "operator_wallet": op,
            "burners_generated_during_campaign": [burner1, burner2],
            "clean_exit_wallets": [exit_funded, exit_unused],
            "clean_exits_funded": [exit_funded],
            "attacker_wallets": [src, op, burner1, burner2],
            "contracts": {"usdt": usdt, "pool": pool, "tornado": tornado},
        },
        trace=[],
        campaign={"successful": True},
    ))
    assert run.kind == "attacker"
    assert run.labels[src] == LABEL_ATTACKER_SOURCE
    assert run.labels[op] == LABEL_INFRASTRUCTURE
    assert run.labels[burner1] == LABEL_ATTACKER_BURNER
    assert run.labels[burner2] == LABEL_ATTACKER_BURNER
    assert run.labels[exit_funded] == LABEL_CLEAN_EXIT_FUNDED
    assert run.labels[exit_unused] == LABEL_CLEAN_EXIT_UNUSED
    assert run.labels[usdt] == LABEL_CONTRACT
    assert run.labels[pool] == LABEL_CONTRACT
    assert run.labels[tornado] == LABEL_CONTRACT
    assert run.campaign == {"successful": True}


def test_load_run_missing_artifact_raises(tmp_path):
    """Required artifact missing → FileNotFoundError with the offending path."""
    run_dir = tmp_path / "broken"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text("{}")
    (run_dir / "addresses.json").write_text("{}")
    # chain_trace.jsonl deliberately absent
    with pytest.raises(FileNotFoundError, match="chain_trace.jsonl"):
        load_run(run_dir)


# --- synthesize_attacker_labels priorities ------------------------------


def test_attacker_label_priority_contracts_win_over_role():
    """An address that's both a contract AND in attacker_wallets → contract."""
    weird = _addr(1)
    out = synthesize_attacker_labels({
        "contracts": {"usdt": weird},
        "attacker_wallets": [weird],
        "source_wallet": weird,   # tries to override; should NOT win
    })
    assert out[weird] == LABEL_CONTRACT


def test_attacker_label_priority_source_wins_over_burner():
    """source_wallet listed in burners_generated → keep source label."""
    src = _addr(1)
    out = synthesize_attacker_labels({
        "source_wallet": src,
        "burners_generated_during_campaign": [src],
        "attacker_wallets": [src],
    })
    assert out[src] == LABEL_ATTACKER_SOURCE


def test_attacker_label_unused_exits_get_distractor_label():
    """Labeled clean exits the agent never used → clean_exit_unused."""
    funded, unused = _addr(1), _addr(2)
    out = synthesize_attacker_labels({
        "clean_exit_wallets": [funded, unused],
        "clean_exits_funded": [funded],
    })
    assert out[funded] == LABEL_CLEAN_EXIT_FUNDED
    assert out[unused] == LABEL_CLEAN_EXIT_UNUSED


# --- to_networkx: edge generation per event kind ------------------------


def _tx(events=None, *, tx_from=None, tx_to=None,
        value_wei=0, block=1, tx_hash="0xabc", status=1):
    return {
        "tx_hash": tx_hash, "block": block, "status": status,
        "from": tx_from, "to": tx_to, "value_wei": str(value_wei),
        "value_eth": value_wei / 10**18, "gas_used": 21000,
        "events": events or [],
    }


def _attacker_run(tmp_path, *, trace, **address_overrides):
    """Shorthand for building an attacker-flavoured RunData via load_run."""
    addresses = {
        "source_wallet": _addr(1),
        "operator_wallet": _addr(2),
        "burners_generated_during_campaign": [],
        "clean_exit_wallets": [],
        "clean_exits_funded": [],
        "attacker_wallets": [_addr(1), _addr(2)],
        "contracts": {"usdt": _addr(97), "pool": _addr(98), "tornado": _addr(99)},
    }
    addresses.update(address_overrides)
    return load_run(_write_run(tmp_path, kind="attacker",
                               addresses=addresses, trace=trace))


def test_to_networkx_native_eth_transfer_makes_one_edge(tmp_path):
    """value > 0, no events → single edge from→to with asset=ETH."""
    a, b = _addr(1), _addr(10)
    run = _attacker_run(
        tmp_path, source_wallet=a,
        attacker_wallets=[a, b],
        burners_generated_during_campaign=[b],
        trace=[_tx(tx_from=a, tx_to=b, value_wei=2 * 10**18)],
    )
    g = to_networkx(run)
    # exactly one edge a→b, asset ETH, value 2.0
    a_b = [(u, v, d) for u, v, k, d in g.edges(data=True, keys=True)
           if u == a and v == b]
    assert len(a_b) == 1
    attrs = a_b[0][2]
    assert attrs["asset"] == "ETH"
    assert attrs["value"] == 2.0
    assert attrs["kind"] == "transfer_eth"


def test_to_networkx_usdt_transfer_event_makes_one_edge(tmp_path):
    """An ERC-20 Transfer event → one edge keyed by the event's from/to."""
    sender, recipient = _addr(1), _addr(10)
    usdt = _addr(97)
    run = _attacker_run(
        tmp_path,
        source_wallet=sender,
        attacker_wallets=[sender, recipient],
        burners_generated_during_campaign=[recipient],
        contracts={"usdt": usdt, "pool": _addr(98), "tornado": _addr(99)},
        trace=[_tx(
            tx_from=sender, tx_to=usdt, value_wei=0,
            events=[{
                "contract": "usdt", "event": "Transfer",
                "args": {
                    "from": sender, "to": recipient,
                    "value": str(250 * 10**6),
                },
            }],
        )],
    )
    g = to_networkx(run)
    usdt_edges = [(u, v, d) for u, v, _, d in g.edges(data=True, keys=True)
                  if d.get("kind") == "transfer_usdt"]
    assert len(usdt_edges) == 1
    u, v, d = usdt_edges[0]
    assert u == sender and v == recipient
    assert d["asset"] == "USDT"
    assert d["value"] == 250.0
    # And the tx-level (sender → usdt-contract) edge was NOT also emitted
    sender_usdt_edges = [
        (u, v) for u, v, _ in g.edges(keys=True) if u == sender and v == usdt
    ]
    assert sender_usdt_edges == []


def test_to_networkx_mixer_deposit_and_withdraw_make_directed_edges(tmp_path):
    """Mixer deposit → user→mixer, withdraw → mixer→recipient."""
    depositor, recipient = _addr(1), _addr(10)
    tornado = _addr(99)
    run = _attacker_run(
        tmp_path,
        source_wallet=depositor,
        attacker_wallets=[depositor, recipient],
        burners_generated_during_campaign=[recipient],
        contracts={"usdt": _addr(97), "pool": _addr(98), "tornado": tornado},
        trace=[
            _tx(
                tx_from=depositor, tx_to=tornado, value_wei=10**18,
                events=[{
                    "contract": "tornado", "event": "Deposit",
                    "args": {"commitment": "0xdead", "leafIndex": 0},
                }],
            ),
            _tx(
                tx_from=_addr(2), tx_to=tornado, value_wei=0, block=2,
                tx_hash="0xdef",
                events=[{
                    "contract": "tornado", "event": "Withdrawal",
                    "args": {"to": recipient, "nullifierHash": "0xbeef"},
                }],
            ),
        ],
    )
    g = to_networkx(run)
    kinds = {d["kind"] for _, _, d in g.edges(data=True)}
    assert "mixer_deposit" in kinds
    assert "mixer_withdraw" in kinds

    deposits = [(u, v) for u, v, d in g.edges(data=True)
                if d["kind"] == "mixer_deposit"]
    withdraws = [(u, v) for u, v, d in g.edges(data=True)
                 if d["kind"] == "mixer_withdraw"]
    assert deposits == [(depositor, tornado)]
    assert withdraws == [(tornado, recipient)]


def test_to_networkx_swap_event_makes_in_and_out_edges(tmp_path):
    """Uniswap Swap event → user→pool (in) + pool→user (out)."""
    user = _addr(1)
    pool = _addr(98)
    run = _attacker_run(
        tmp_path,
        source_wallet=user,
        attacker_wallets=[user],
        contracts={"usdt": _addr(97), "pool": pool, "tornado": _addr(99)},
        trace=[_tx(
            tx_from=user, tx_to=pool, value_wei=10**18,
            events=[{
                "contract": "pool", "event": "Swap",
                "args": {
                    "sender": user,
                    "ethIn": str(10**18), "usdtIn": "0",
                    "ethOut": "0", "usdtOut": str(1990 * 10**6),
                },
            }],
        )],
    )
    g = to_networkx(run)
    swap_edges = [(u, v, d) for u, v, d in g.edges(data=True)
                  if d["kind"] == "swap"]
    # one in + one out
    assert len(swap_edges) == 2
    by_dir = {(u, v): d for u, v, d in swap_edges}
    assert (user, pool) in by_dir and by_dir[(user, pool)]["asset"] == "ETH"
    assert (pool, user) in by_dir and by_dir[(pool, user)]["asset"] == "USDT"
    assert by_dir[(pool, user)]["value"] == 1990.0


def test_to_networkx_reverted_txs_are_skipped(tmp_path):
    """status != 1 → tx dropped, no edges, no nodes added."""
    a, b = _addr(1), _addr(10)
    run = _attacker_run(
        tmp_path, source_wallet=a,
        attacker_wallets=[a, b],
        burners_generated_during_campaign=[b],
        trace=[_tx(tx_from=a, tx_to=b, value_wei=10**18, status=0)],
    )
    g = to_networkx(run)
    assert g.number_of_edges() == 0


def test_to_networkx_distractor_clean_exits_appear_as_isolated_nodes(tmp_path):
    """Unused clean exits get a node + label even if no edges touch them."""
    src, funded, unused = _addr(1), _addr(2), _addr(3)
    run = _attacker_run(
        tmp_path, source_wallet=src,
        clean_exit_wallets=[funded, unused],
        clean_exits_funded=[funded],
        attacker_wallets=[src],
        trace=[],
    )
    g = to_networkx(run)
    assert g.nodes[unused]["label"] == LABEL_CLEAN_EXIT_UNUSED
    assert g.nodes[funded]["label"] == LABEL_CLEAN_EXIT_FUNDED
    assert g.in_degree(unused) == 0 and g.out_degree(unused) == 0


def test_to_networkx_multi_edge_between_same_pair_is_preserved(tmp_path):
    """Two payments a→b → two parallel edges in the multigraph."""
    a, b = _addr(1), _addr(10)
    run = _attacker_run(
        tmp_path, source_wallet=a,
        attacker_wallets=[a, b],
        burners_generated_during_campaign=[b],
        trace=[
            _tx(tx_from=a, tx_to=b, value_wei=10**18,
                tx_hash="0xa1", block=1),
            _tx(tx_from=a, tx_to=b, value_wei=2 * 10**18,
                tx_hash="0xa2", block=2),
        ],
    )
    g = to_networkx(run)
    parallel = [(k, d) for k, d in g[a][b].items()]
    assert len(parallel) == 2
    hashes = {d["tx_hash"] for _, d in parallel}
    assert hashes == {"0xa1", "0xa2"}


def test_to_networkx_unknown_addresses_get_unknown_label(tmp_path):
    """An address appearing in trace but absent from addresses → unknown."""
    a = _addr(1)
    rando = _addr(123)
    run = _attacker_run(
        tmp_path, source_wallet=a, attacker_wallets=[a],
        trace=[_tx(tx_from=a, tx_to=rando, value_wei=10**18)],
    )
    g = to_networkx(run)
    assert g.nodes[rando]["label"] == LABEL_UNKNOWN


# --- summary helper -----------------------------------------------------


def test_summarize_graph_counts_match_actual_structure(tmp_path):
    """summarize_graph counts nodes by label/kind and edges by kind/asset.

    Uses _addr(10), _addr(11) for burners (NOT _addr(2)/3) — _addr(2) is
    the default operator_wallet in _attacker_run, and reusing it as a
    burner triggers label priority (infrastructure wins over burner),
    which is correct semantics but unrelated to what this test asserts.
    """
    src = _addr(1)
    b1, b2 = _addr(10), _addr(11)
    run = _attacker_run(
        tmp_path, source_wallet=src,
        burners_generated_during_campaign=[b1, b2],
        attacker_wallets=[src, b1, b2],
        trace=[
            _tx(tx_from=src, tx_to=b1, value_wei=10**18,
                tx_hash="0x1", block=1),
            _tx(tx_from=src, tx_to=b2, value_wei=10**18,
                tx_hash="0x2", block=2),
        ],
    )
    g = to_networkx(run)
    s = summarize_graph(g)
    assert s["num_edges"] == 2
    assert s["edges_by_kind"]["transfer_eth"] == 2
    assert s["edges_by_asset"]["ETH"] == 2
    # nodes labelled: src + b1 + b2 + 3 contracts (pre-seeded) + operator
    # = 7. labels: 1 source, 2 burners, 3 contracts, 1 infra.
    assert s["nodes_by_label"][LABEL_ATTACKER_SOURCE] == 1
    assert s["nodes_by_label"][LABEL_ATTACKER_BURNER] == 2
    assert s["nodes_by_label"][LABEL_CONTRACT] == 3
    assert s["nodes_by_label"][LABEL_INFRASTRUCTURE] == 1
