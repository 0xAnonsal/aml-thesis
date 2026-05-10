"""Smoke tests for ToolDispatcher.

Spins up a fresh Anvil per Anvil-needing test, deploys MockUSDT, exercises
get_balance + transfer_usdt + error paths. No LLM API calls — pure on-chain
operations, costs $0.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3

from aml.attackers import ToolDispatcher, ToolResult
from aml.chains import AnvilNode

REPO_ROOT = Path(__file__).resolve().parents[1]
USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"


needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH",
)


def _raw_tx(signed):
    return getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")


def _send(w3, fn, sender, key, gas=2_000_000, value=0):
    tx = fn.build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
        "value": value,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    return w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(_raw_tx(signed)))


def _deploy_usdt(w3, deployer, deployer_key):
    if not USDT_ARTIFACT.exists():
        subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)
    with USDT_ARTIFACT.open() as f:
        a = json.load(f)
    factory = w3.eth.contract(abi=a["abi"], bytecode=a["bytecode"]["object"])
    receipt = _send(w3, factory.constructor(), deployer, deployer_key)
    return w3.eth.contract(address=receipt.contractAddress, abi=a["abi"])


# --- Schema-only tests (no Anvil needed) -------------------------------------


def test_tool_definitions_match_anthropic_schema():
    """Tool definitions are the right shape for Anthropic's tools= parameter."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})

    defs = dispatcher.tool_definitions
    names = {d["name"] for d in defs}
    assert "get_balance" in names
    assert "transfer_usdt" in names

    for d in defs:
        assert "name" in d and isinstance(d["name"], str)
        assert "description" in d and len(d["description"]) > 20
        assert "input_schema" in d
        s = d["input_schema"]
        assert s["type"] == "object"
        assert "properties" in s
        assert "required" in s


def test_unknown_tool_returns_error_not_crash():
    """Dispatching a nonexistent tool returns an error, never raises."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    result = dispatcher.dispatch("nonexistent_tool", {})
    assert result.is_error
    assert "unknown tool" in result.error.lower()


def test_tool_result_to_content_serializes_success_and_error():
    """ToolResult.to_content() produces valid JSON for success, prefixed string for errors."""
    ok = ToolResult(output={"foo": "bar", "n": 42})
    assert json.loads(ok.to_content()) == {"foo": "bar", "n": 42}

    err = ToolResult(error="something broke")
    assert err.to_content().startswith("Error:")
    assert "something broke" in err.to_content()


def test_register_wallet_normalizes_address():
    """Addresses stored in the registry are checksummed regardless of input case."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    # All-lowercase address — register_wallet should checksum it
    lower = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
    dispatcher.register_wallet(lower, "0xdeadbeef")
    checksum = Web3.to_checksum_address(lower)
    assert checksum in dispatcher.wallets
    assert lower not in dispatcher.wallets   # only checksum form is stored


# --- Anvil-backed tests -----------------------------------------------------


@needs_foundry
def test_get_balance_eth_returns_anvil_default():
    """Anvil's default accounts start with 10000 ETH."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        result = dispatcher.dispatch(
            "get_balance", {"address": deployer, "asset": "ETH"},
        )
        assert not result.is_error, result.error
        assert result.output["asset"] == "ETH"
        # 10000 ETH minus the gas the deployer paid for the USDT contract deploy
        assert 9990 < result.output["balance"] <= 10000


@needs_foundry
def test_get_balance_usdt_after_mint():
    """Reads USDT balance correctly in human units after a mint."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        # Mint 1000 USDT (with 6 decimals)
        _send(w3, usdt.functions.mint(deployer, 1000 * 10**6),
              deployer, deployer_key, gas=200_000)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        result = dispatcher.dispatch(
            "get_balance", {"address": deployer, "asset": "USDT"},
        )
        assert not result.is_error
        assert result.output == {"asset": "USDT", "balance": 1000.0}


@needs_foundry
def test_transfer_usdt_round_trip():
    """The full write path: dispatcher signs and sends; balances change accordingly."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        bob = node.accounts[1]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        _send(w3, usdt.functions.mint(deployer, 1000 * 10**6),
              deployer, deployer_key, gas=200_000)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )

        # Pre-state
        assert dispatcher.dispatch(
            "get_balance", {"address": deployer, "asset": "USDT"},
        ).output["balance"] == 1000.0
        assert dispatcher.dispatch(
            "get_balance", {"address": bob, "asset": "USDT"},
        ).output["balance"] == 0.0

        # Transfer 250 USDT
        result = dispatcher.dispatch("transfer_usdt", {
            "from_address": deployer,
            "to_address": bob,
            "amount_usdt": 250.0,
        })
        assert not result.is_error, result.error
        assert "tx_hash" in result.output
        assert result.output["amount_usdt"] == 250.0
        assert result.output["gas_used"] > 0

        # Post-state
        assert dispatcher.dispatch(
            "get_balance", {"address": deployer, "asset": "USDT"},
        ).output["balance"] == 750.0
        assert dispatcher.dispatch(
            "get_balance", {"address": bob, "asset": "USDT"},
        ).output["balance"] == 250.0


@needs_foundry
def test_transfer_unknown_sender_returns_error():
    """Transfer from an address not in the wallet registry: structured error, no crash."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        bob = node.accounts[1]
        eve = node.accounts[2]   # NOT registered
        usdt = _deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        result = dispatcher.dispatch("transfer_usdt", {
            "from_address": eve,
            "to_address": bob,
            "amount_usdt": 100.0,
        })
        assert result.is_error
        assert "private key" in result.error.lower()


@needs_foundry
def test_transfer_negative_amount_rejected():
    """Negative or zero amount: rejected before any chain call."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        bob = node.accounts[1]
        usdt = _deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        for bad in (0, -1, -0.5):
            result = dispatcher.dispatch("transfer_usdt", {
                "from_address": deployer,
                "to_address": bob,
                "amount_usdt": bad,
            })
            assert result.is_error
            assert "positive" in result.error.lower()
