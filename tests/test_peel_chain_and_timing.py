"""Tests for PR #52 — peel_chain and advance_blocks tools.

peel_chain is the canonical crypto laundering technique (Merkle Science:
appears in ~70% of Bitcoin theft cases). advance_blocks simulates timing
delays between phases, reproducing real APT patterns like Lazarus's
multi-week wait or the HTX Bridge attacker's 4-month dormancy.

Two layers of tests:
  - Schema + surface (no chain, instant) — confirms both tools are wired
    into the dispatcher and exposed to the Layering sub-agent and the
    Coordinator (advance_blocks only).
  - Live dispatch on Anvil — confirms peel_chain produces the expected
    linear topology and advance_blocks moves the chain forward.
"""
from __future__ import annotations

import shutil

import pytest
from web3 import Web3

from aml.attackers import ToolDispatcher, coordinator as coord_mod
from aml.attackers.prompts import COORDINATOR_SYSTEM, LAYERING_SYSTEM
from aml.chains import AnvilNode
from aml.chains.eth_stack import deploy_usdt


needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH",
)


# --- schema / surface (no chain) ---------------------------------------


def test_peel_chain_in_dispatcher_tool_definitions():
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    names = {t["name"] for t in dispatcher.tool_definitions}
    assert "peel_chain" in names


def test_advance_blocks_in_dispatcher_tool_definitions():
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    names = {t["name"] for t in dispatcher.tool_definitions}
    assert "advance_blocks" in names


def test_peel_chain_schema_required_args():
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    schema = next(
        t for t in dispatcher.tool_definitions if t["name"] == "peel_chain"
    )
    required = set(schema["input_schema"]["required"])
    assert required == {"from_address", "asset", "initial_amount"}
    props = schema["input_schema"]["properties"]
    assert set(props) >= {"from_address", "asset", "initial_amount", "num_hops", "peel_pct"}


def test_advance_blocks_schema_required_args():
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    schema = next(
        t for t in dispatcher.tool_definitions if t["name"] == "advance_blocks"
    )
    assert schema["input_schema"]["required"] == ["num_blocks"]


def test_peel_chain_in_layering_scope_only():
    assert "peel_chain" in coord_mod._LAYERING_TOOLS
    assert "peel_chain" not in coord_mod._PLACEMENT_TOOLS
    assert "peel_chain" not in coord_mod._INTEGRATION_TOOLS


def test_advance_blocks_in_layering_and_coordinator():
    # Layering can insert delays within its phase; Coordinator can
    # insert delays between phases (via inspect_chain-style direct tools).
    assert "advance_blocks" in coord_mod._LAYERING_TOOLS


def test_layering_prompt_documents_peel_chain_and_delays():
    assert "peel_chain" in LAYERING_SYSTEM
    assert "PEEL CHAIN" in LAYERING_SYSTEM
    assert "advance_blocks" in LAYERING_SYSTEM
    assert "TIMING DELAYS" in LAYERING_SYSTEM


def test_coordinator_prompt_documents_timing_delays():
    assert "TIMING DELAYS" in COORDINATOR_SYSTEM
    assert "advance_blocks" in COORDINATOR_SYSTEM
    assert "Lazarus" in COORDINATOR_SYSTEM


def test_peel_chain_rejects_bad_asset():
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    result = dispatcher.dispatch("peel_chain", {
        "from_address": "0x" + "1" * 40,
        "asset": "BTC",
        "initial_amount": 1.0,
    })
    assert result.is_error
    assert "asset" in result.error


def test_peel_chain_rejects_out_of_range_pct():
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    result = dispatcher.dispatch("peel_chain", {
        "from_address": "0x" + "1" * 40,
        "asset": "ETH",
        "initial_amount": 1.0,
        "peel_pct": 0.5,
    })
    assert result.is_error
    assert "peel_pct" in result.error


def test_advance_blocks_rejects_below_100():
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    result = dispatcher.dispatch("advance_blocks", {"num_blocks": 50})
    assert result.is_error


# --- live dispatch on Anvil ---------------------------------------------


@needs_foundry
def test_peel_chain_eth_produces_linear_topology():
    """peel_chain(ETH, 3.0, 10 hops, 7%) leaves ~1.4 ETH at the tail."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={deployer: deployer_key, alice: alice_key},
        )
        result = dispatcher.dispatch("peel_chain", {
            "from_address": alice,
            "asset": "ETH",
            "initial_amount": 3.0,
            "num_hops": 10,
            "peel_pct": 0.07,
        })
        assert not result.is_error, result.error
        out = result.output

        assert out["asset"] == "ETH"
        assert out["num_hops"] == 10
        assert len(out["hop_wallets"]) == 10
        assert len(out["peel_wallets"]) == 10

        # Tail amount matches formula: 3.0 * (1-0.07)^10 = ~1.451 ETH
        expected_tail = 3.0 * (0.93 ** 10)
        assert abs(out["tail_amount"] - expected_tail) < 0.01

        # Total peeled + tail = initial
        assert abs(
            out["tail_amount"] + out["total_peeled"] - 3.0
        ) < 0.001

        # Every hop and peel wallet is now in dispatcher registry
        for addr in out["hop_wallets"]:
            assert addr in dispatcher.wallets
        for peel in out["peel_wallets"]:
            assert peel["address"] in dispatcher.wallets


@needs_foundry
def test_peel_chain_usdt_works_with_stablecoin():
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        usdt = deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt,
            wallets={deployer: deployer_key, alice: alice_key},
        )
        # Mint 10000 USDT to alice
        dispatcher.dispatch("mint_usdt", {
            "to_address": alice, "amount_usdt": 10000.0,
        })

        result = dispatcher.dispatch("peel_chain", {
            "from_address": alice,
            "asset": "USDT",
            "initial_amount": 10000.0,
            "num_hops": 8,
            "peel_pct": 0.10,
        })
        assert not result.is_error, result.error
        out = result.output
        assert out["asset"] == "USDT"
        # Tail formula: 10000 * (0.9)^8 = ~4305
        assert 4200 < out["tail_amount"] < 4400


@needs_foundry
def test_advance_blocks_moves_chain_forward():
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={deployer: deployer_key},
        )
        block_before = w3.eth.block_number
        result = dispatcher.dispatch("advance_blocks", {"num_blocks": 5000})
        assert not result.is_error, result.error
        out = result.output
        assert out["block_before"] == block_before
        assert out["blocks_advanced"] == 5000
        assert w3.eth.block_number == block_before + 5000
        # 5000 blocks * 12 seconds/block = 60000 seconds ~= 16.67 hours
        assert out["approx_ethereum_seconds"] == 60_000
