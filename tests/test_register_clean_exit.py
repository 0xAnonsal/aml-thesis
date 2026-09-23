"""Tests for register_clean_exit — the dynamic clean-exit tool (PR #46).

register_clean_exit replaces the pre-allocated clean_exits parameter.
The Integration sub-agent calls it to create labeled off-ramp wallets on
demand, deciding count and platform spread based on the laundered value
and the sub-$999 cap. run_campaign reads dispatcher.registered_clean_exits
at end-of-campaign to write ground-truth labels into addresses.json.

Two layers of tests:
  - Schema + surface (no chain, instant) — confirms the tool is wired
    into the dispatcher, has the right schema, and is exposed only to
    the Integration sub-agent (not Placement or Layering).
  - Live dispatch on Anvil — confirms register_clean_exit creates a
    fresh wallet, auto-seeds gas dust, appends a metadata entry to
    registered_clean_exits, and returns the documented shape.
"""
from __future__ import annotations

import shutil

import pytest
from web3 import Web3

from aml.attackers import ToolDispatcher, coordinator as coord_mod
from aml.chains import AnvilNode


needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH",
)


# --- schema / surface (no chain) ---------------------------------------


def test_register_clean_exit_is_in_dispatcher_tool_definitions():
    """The dispatcher exposes register_clean_exit as one of its tools."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    names = {t["name"] for t in dispatcher.tool_definitions}
    assert "register_clean_exit" in names


def test_register_clean_exit_schema_requires_exchange_platform():
    """exchange_platform is required; note is optional."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    schema = next(
        t for t in dispatcher.tool_definitions
        if t["name"] == "register_clean_exit"
    )
    assert schema["input_schema"]["required"] == ["exchange_platform"]
    props = schema["input_schema"]["properties"]
    assert "exchange_platform" in props
    assert "note" in props


def test_register_clean_exit_in_integration_scope_only():
    """The Coordinator hands register_clean_exit to Integration only.

    Placement and Layering must NOT see it — keeping the role separation
    honest. Placement's job is to position stolen funds; Layering's job
    is obfuscation; creating labeled off-ramps is squarely Integration's
    responsibility.
    """
    assert "register_clean_exit" in coord_mod._INTEGRATION_TOOLS
    assert "register_clean_exit" not in coord_mod._PLACEMENT_TOOLS
    assert "register_clean_exit" not in coord_mod._LAYERING_TOOLS


def test_dispatcher_starts_with_empty_registered_clean_exits():
    """A fresh dispatcher has no registered exits — the log only grows when
    the tool is called explicitly."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    assert dispatcher.registered_clean_exits == []


def test_register_clean_exit_rejects_empty_platform():
    """An empty / whitespace-only platform string is a clean error."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    result = dispatcher.dispatch("register_clean_exit", {"exchange_platform": ""})
    assert result.is_error
    assert "non-empty" in result.error.lower()

    result2 = dispatcher.dispatch(
        "register_clean_exit", {"exchange_platform": "   "},
    )
    assert result2.is_error


# --- live dispatch on Anvil ---------------------------------------------


@needs_foundry
def test_register_clean_exit_creates_seeded_wallet_with_metadata():
    """End-to-end: tool creates a fresh wallet, seeds gas, and logs metadata."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={deployer: deployer_key},
        )
        result = dispatcher.dispatch(
            "register_clean_exit",
            {"exchange_platform": "Binance", "note": "primary KYC account"},
        )
        assert not result.is_error, result.error
        out = result.output

        # Output shape
        assert "address" in out
        assert out["exchange_platform"] == "Binance"
        # P1-29: clean exits are TERMINAL wallets and are no longer auto-
        # seeded with gas. They only ever receive funds (the sender pays the
        # gas), so gas_seed_eth is 0.0 and the wallet starts empty on chain.
        assert out["gas_seed_eth"] == 0.0

        # No gas seeded on chain (terminal wallet — receives only).
        balance = w3.eth.get_balance(out["address"])
        assert balance == 0

        # The wallet is now in dispatcher.wallets (signing registry)
        # AND in registered_clean_exits (label registry)
        assert out["address"] in dispatcher.wallets
        assert len(dispatcher.registered_clean_exits) == 1
        entry = dispatcher.registered_clean_exits[0]
        assert entry["address"] == out["address"]
        assert entry["exchange_platform"] == "Binance"
        assert entry["note"] == "primary KYC account"


@needs_foundry
def test_register_clean_exit_accumulates_across_calls():
    """Multiple calls produce distinct wallets, all logged in order."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={deployer: deployer_key},
        )
        platforms = ["Binance", "Coinbase", "Binance", "Kraken"]
        for p in platforms:
            r = dispatcher.dispatch(
                "register_clean_exit", {"exchange_platform": p},
            )
            assert not r.is_error

        assert len(dispatcher.registered_clean_exits) == 4
        recorded_platforms = [
            e["exchange_platform"] for e in dispatcher.registered_clean_exits
        ]
        assert recorded_platforms == platforms
        addresses = [e["address"] for e in dispatcher.registered_clean_exits]
        assert len(set(addresses)) == 4, "wallets must all be distinct"
