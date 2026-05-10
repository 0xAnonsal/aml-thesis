"""End-to-end test of the Coordinator agent loop.

Exercises the full path: LLM picks a tool → dispatcher executes on Anvil →
tool_result fed back → LLM sees the result → LLM produces final text.

The headline test costs ~$0.002 with Haiku. Skipped cleanly if either
ANTHROPIC_API_KEY or Foundry isn't available.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3

from aml.attackers import (
    CallResult,
    CampaignResult,
    Coordinator,
    LLMClient,
    ToolDispatcher,
)
from aml.chains import AnvilNode

REPO_ROOT = Path(__file__).resolve().parents[1]
USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"


needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH",
)


needs_api_key = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; cannot make live API calls",
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


# --- No-API tests -----------------------------------------------------------


def test_complete_rejects_both_prompt_and_messages():
    """LLMClient.complete: passing both `prompt` and `messages` is an error."""
    client = LLMClient(api_key="dummy")
    with pytest.raises(ValueError, match="OR `messages`"):
        client.complete(prompt="hi", messages=[{"role": "user", "content": "hi"}])


def test_complete_rejects_neither_prompt_nor_messages():
    """LLMClient.complete: missing both is an error."""
    client = LLMClient(api_key="dummy")
    with pytest.raises(ValueError, match="either `prompt` or `messages`"):
        client.complete()


def test_campaign_result_successful_flag():
    """CampaignResult.successful is True only when stopped_reason == 'end_turn'."""
    assert CampaignResult(final_text="", iterations=1, stopped_reason="end_turn").successful
    assert not CampaignResult(final_text="", iterations=1, stopped_reason="max_iterations").successful
    assert not CampaignResult(final_text="", iterations=1, stopped_reason="refusal").successful


# --- Live LLM-driven test ---------------------------------------------------


@needs_foundry
@needs_api_key
def test_coordinator_runs_simple_transfer_campaign():
    """The headline test: the LLM picks tool calls to execute alice->bob transfer.

    Costs ~$0.002 on Haiku. The test asserts both that the Coordinator's
    bookkeeping is sane (successful, called transfer_usdt) AND that the
    on-chain state actually changed (bob has 250 USDT after).
    """
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        bob = node.accounts[1]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        # Mint 1000 USDT to deployer (alice)
        _send(w3, usdt.functions.mint(deployer, 1000 * 10**6),
              deployer, deployer_key, gas=200_000)

        client = LLMClient()
        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        coordinator = Coordinator(
            client, dispatcher, model="haiku", max_iterations=8,
        )

        system = (
            "You are an automated wallet operator. Execute the user's "
            "instructions using the available tools, then stop. Do NOT ask "
            "for confirmation — execute directly. Verify results using "
            "get_balance after any state-changing action."
        )
        user_prompt = (
            f"You manage a wallet at address {deployer} which currently holds "
            f"USDT. Transfer exactly 250 USDT from {deployer} to {bob}. "
            f"After the transfer, verify success by checking bob's USDT "
            f"balance with get_balance. Then stop."
        )

        result = coordinator.run(user_prompt, system=system, max_tokens=2048)

        # --- Bookkeeping checks ---
        assert isinstance(result, CampaignResult)
        assert result.successful, (
            f"Coordinator stopped with reason={result.stopped_reason!r}, "
            f"iterations={result.iterations}, final_text={result.final_text!r}"
        )
        assert result.iterations >= 2, (
            f"Should take at least 2 iterations (tool_call + final text), "
            f"took {result.iterations}"
        )
        assert result.cost_usd > 0
        assert result.cost_usd < 0.01, (
            f"Should cost < $0.01 with Haiku for this tiny task, "
            f"was ${result.cost_usd:.4f}"
        )

        # --- The LLM actually called transfer_usdt ---
        transfer_calls = [c for c in result.tool_calls if c["name"] == "transfer_usdt"]
        assert len(transfer_calls) >= 1, (
            f"LLM should have called transfer_usdt; tool calls were: "
            f"{[c['name'] for c in result.tool_calls]}"
        )
        successful_transfers = [c for c in transfer_calls if not c["is_error"]]
        assert len(successful_transfers) >= 1, (
            f"At least one transfer_usdt call should succeed; errors were: "
            f"{[c['error'] for c in transfer_calls if c['is_error']]}"
        )

        # --- The actual chain state changed ---
        bob_balance_base = usdt.functions.balanceOf(bob).call()
        assert bob_balance_base == 250 * 10**6, (
            f"Bob should have 250 USDT on-chain, has "
            f"{bob_balance_base / 10**6}"
        )
        deployer_balance_base = usdt.functions.balanceOf(deployer).call()
        assert deployer_balance_base == 750 * 10**6, (
            f"Deployer should have 750 USDT remaining, has "
            f"{deployer_balance_base / 10**6}"
        )
