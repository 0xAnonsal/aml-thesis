"""Direct unit tests for the shared chain helpers.

aml.chains.trace and aml.chains.eth_stack were extracted from
run_campaign.py in PR #28 and are exercised end-to-end through
test_run_campaign, test_graph, and test_coordinator. These dedicated
tests harden the small public helpers — pure-Python / numpy stuff —
without needing Anvil to run.

Anvil-needing helpers (deploy_usdt, deploy_pool, deploy_tornado, send_tx)
stay covered by the integration tests; running them here would just
duplicate setup cost.
"""
from __future__ import annotations

import pytest

from aml.chains.eth_stack import (
    MERKLE_DEPTH,
    POOL_BOOTSTRAP_ETH_WEI,
    POOL_BOOTSTRAP_USDT_BASE,
    raw_tx,
)
from aml.chains.trace import decode_event, jsonable


# --- chain.trace.jsonable ------------------------------------------------


def test_jsonable_passes_through_simple_types():
    """str / small int / None / float — returned as-is."""
    assert jsonable("hello") == "hello"
    assert jsonable(42) == 42
    assert jsonable(None) is None
    assert jsonable(1.5) == 1.5


def test_jsonable_bytes_become_hex_strings():
    """bytes and bytearray → 0x-prefixed hex (web3 receipt fields)."""
    assert jsonable(b"\xab\xcd") == "0xabcd"
    assert jsonable(bytearray(b"\x01\x02")) == "0x0102"


def test_jsonable_huge_int_becomes_string():
    """Above the JS 53-bit Number limit, ints are stringified to survive JSON."""
    big = 2**100
    out = jsonable(big)
    assert isinstance(out, str)
    assert out == str(big)


def test_jsonable_small_int_stays_int():
    """Below the 53-bit limit, ints stay numeric."""
    safe = 2**52
    assert jsonable(safe) == safe
    assert isinstance(jsonable(safe), int)


def test_jsonable_recurses_into_dicts():
    """Nested dicts are walked; inner bytes/bigints get coerced too."""
    big = 2**100
    out = jsonable({"a": b"\x01", "n": big, "x": [1, 2]})
    assert out == {"a": "0x01", "n": str(big), "x": [1, 2]}


def test_jsonable_coerces_tuples_to_lists():
    """Tuples become JSON arrays."""
    assert jsonable((b"\x00", 1, "x")) == ["0x00", 1, "x"]


# --- chain.trace.decode_event --------------------------------------------


def test_decode_event_empty_known_contracts_returns_none():
    """No known contracts → nothing to match against → None."""
    class FakeLog:
        address = "0x" + "f" * 40
    assert decode_event(FakeLog(), known_contracts={}) is None


def test_decode_event_skips_none_contracts():
    """A `None` entry in known_contracts (e.g., pool not deployed) is skipped."""
    class FakeLog:
        address = "0x" + "f" * 40
    assert decode_event(FakeLog(), known_contracts={"pool": None}) is None


# --- chain.eth_stack constants -------------------------------------------


def test_pool_bootstrap_constants_match_design():
    """5000 ETH + 10M USDT → spot price 1 ETH = $2000 (deep pool so campaign
    swaps of tens of ETH do not distort the price; see P1-20)."""
    assert POOL_BOOTSTRAP_ETH_WEI == 5_000 * 10**18
    assert POOL_BOOTSTRAP_USDT_BASE == 10_000_000 * 10**6
    spot = (POOL_BOOTSTRAP_USDT_BASE / 10**6) / (POOL_BOOTSTRAP_ETH_WEI / 10**18)
    assert spot == 2000


def test_merkle_depth_matches_zk_circuit_parameter():
    """MERKLE_DEPTH must match circuits/withdraw.circom's Withdraw(N) — or
    proofs won't verify on-chain."""
    assert MERKLE_DEPTH == 10


# --- chain.eth_stack.raw_tx ----------------------------------------------


def test_raw_tx_supports_web3_v6_attribute_name():
    """web3.py v6 named it `rawTransaction`."""
    class V6Signed:
        rawTransaction = b"\x01\x02"
    assert raw_tx(V6Signed()) == b"\x01\x02"


def test_raw_tx_supports_web3_v7_attribute_name():
    """web3.py v7 renamed it to `raw_transaction`."""
    class V7Signed:
        raw_transaction = b"\x03\x04"
    assert raw_tx(V7Signed()) == b"\x03\x04"


def test_raw_tx_prefers_v7_name_when_both_present():
    """If both attrs exist (defensive), v7 (`raw_transaction`) wins."""
    class BothSigned:
        raw_transaction = b"\x07\x07"
        rawTransaction = b"\x06\x06"
    assert raw_tx(BothSigned()) == b"\x07\x07"


def test_raw_tx_raises_on_missing_attrs():
    """No matching attribute → RuntimeError with a clear message."""
    class Broken:
        pass
    with pytest.raises(RuntimeError, match="missing raw bytes"):
        raw_tx(Broken())
