"""Tests for inspect_chain — the Coordinator's reflection tool (PR #42).

inspect_chain is a read-only audit added to ToolDispatcher and exposed to
the Coordinator alongside the three delegate_to_* tools. The Coordinator
can call it BETWEEN phase delegations to verify what's actually on-chain
against a sub-agent's self-reported success.

Two layers of tests:
  - Schema + surface (no chain, instant) — confirms the tool is wired
    into both the dispatcher's tool_definitions and the Coordinator's
    coordinator_tool_definitions.
  - Live dispatch on Anvil — confirms inspect_chain returns the expected
    structured shape with real chain state.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3

from aml.attackers import Coordinator, LLMClient, ToolDispatcher
from aml.chains import AnvilNode
from aml.chains.eth_stack import deploy_usdt


REPO_ROOT = Path(__file__).resolve().parents[1]


needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH",
)


# --- schema / surface (no chain) ---------------------------------------


def test_inspect_chain_is_in_dispatcher_tool_definitions():
    """The dispatcher exposes inspect_chain as one of its tools."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    names = {t["name"] for t in dispatcher.tool_definitions}
    assert "inspect_chain" in names


def test_inspect_chain_schema_has_optional_args_only():
    """inspect_chain's input_schema requires nothing — both args optional."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    schema = next(
        t for t in dispatcher.tool_definitions if t["name"] == "inspect_chain"
    )
    assert schema["input_schema"]["required"] == []
    props = schema["input_schema"]["properties"]
    assert "since_block" in props
    assert "max_wallets_sample" in props


def test_coordinator_full_tool_surface_includes_inspect_chain():
    """The Coordinator's coordinator_tool_definitions = 3 delegate + inspect_chain."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    coordinator = Coordinator(LLMClient(api_key="dummy"), dispatcher)
    names = {t["name"] for t in coordinator.coordinator_tool_definitions}
    assert names == {
        "delegate_to_placement",
        "delegate_to_layering",
        "delegate_to_integration",
        "inspect_chain",
    }


def test_coordinator_delegate_tool_definitions_unchanged():
    """The legacy delegate_tool_definitions property still returns just 3 tools.

    Backwards compat — existing callers / docs reference this property. Adding
    inspect_chain to the full surface must not break it.
    """
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    coordinator = Coordinator(LLMClient(api_key="dummy"), dispatcher)
    names = {t["name"] for t in coordinator.delegate_tool_definitions}
    assert names == {
        "delegate_to_placement",
        "delegate_to_layering",
        "delegate_to_integration",
    }


# --- live dispatch on Anvil ---------------------------------------------


@needs_foundry
def test_inspect_chain_returns_structured_audit():
    """inspect_chain on a freshly-deployed chain returns the documented shape."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        # Generate a burner so there are at least two registered wallets.
        dispatcher.dispatch("generate_burner_wallet", {})

        result = dispatcher.dispatch("inspect_chain", {})
        assert not result.is_error, result.error
        out = result.output

        # Top-level shape
        assert "block_number" in out and isinstance(out["block_number"], int)
        assert "num_registered_wallets" in out
        assert out["num_registered_wallets"] >= 2

        # Balance totals
        assert "total_eth_in_wallets" in out
        assert "total_usdt_in_wallets" in out
        assert out["total_eth_in_wallets"] > 0   # deployer has Anvil's 10000 ETH

        # Wallets sample (sorted by ETH descending; deployer first)
        assert "wallets_sample" in out
        assert len(out["wallets_sample"]) >= 1
        for entry in out["wallets_sample"]:
            assert "address" in entry
            assert "eth" in entry
            assert "usdt" in entry

        # Contracts dictionary
        assert "contracts" in out
        assert out["contracts"]["usdt"] == usdt.address
        assert out["contracts"]["pool"] is None
        assert out["contracts"]["tornado"] is None

        # Events: USDT was deployed but no transfers yet
        assert "events_since_block" in out
        assert out["events_since_block"]["usdt_transfers"] >= 0

        # Detection signals: presence required even when no warning fires
        assert "detection_signals" in out
        signals = out["detection_signals"]
        assert "num_registered_wallets" in signals
        assert "num_active_wallets" in signals
        assert "mixer_used" in signals
        assert signals["mixer_used"] is False   # no tornado deployed


@needs_foundry
def test_inspect_chain_to_content_is_valid_json():
    """The audit serialises cleanly via ToolResult.to_content() — what the LLM sees."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = deploy_usdt(w3, deployer, deployer_key)
        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        result = dispatcher.dispatch("inspect_chain", {})
        content = result.to_content()
        # Round-trip through json.loads — must parse cleanly.
        parsed = json.loads(content)
        assert parsed["num_registered_wallets"] == 1
