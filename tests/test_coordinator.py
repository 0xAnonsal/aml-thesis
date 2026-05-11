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


@needs_foundry
@needs_api_key
def test_coordinator_runs_full_smurfing_campaign():
    """The big one: alice has 1M USDT, agent structures it across ~2000 burners.

    Each burner ends with a random amount in [0, 999.999] USDT, the total sums
    to exactly 1M USDT, and alice's USDT balance is 0 at the end. Alice pays
    ETH gas for every transfer.

    Cost: ~$0.01 with Haiku (the LLM only orchestrates; the smurf_split tool
    does the heavy lifting on-chain so the LLM doesn't pay tokens for 2000
    individual tool round-trips). Runtime: ~30-45s (mostly Anvil mining the
    2000 transfers sequentially).

    This is the smallest test scope that exercises the full structuring
    pattern at a realistic scale. To stress-test more burners, bump
    NUM_WALLETS up; the only ceiling is _MAX_BURNERS_PER_SMURF (5000 by
    default in tools.py).
    """
    INITIAL_USDT = 1_000_000     # Alice starts as a whale
    THRESHOLD_USDT = 999.999     # Aggressively below any FATF reporting trigger
    NUM_WALLETS = 2000           # Min capacity = ceil(1M / 999.999) = 1001; 2x for randomness room

    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        _send(
            w3, usdt.functions.mint(deployer, INITIAL_USDT * 10**6),
            deployer, deployer_key, gas=200_000,
        )
        # Sanity: alice's starting state
        assert usdt.functions.balanceOf(deployer).call() == INITIAL_USDT * 10**6
        deployer_eth_before = w3.eth.get_balance(deployer)

        client = LLMClient()
        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        coordinator = Coordinator(
            client, dispatcher, model="haiku", max_iterations=8,
        )

        system = (
            "You are an automated wallet operator running a research-grade "
            "structuring campaign. Use the available tools and stop when done. "
            "Account for ETH gas costs (each USDT transfer costs ~50,000 gas). "
            "For very large structuring (hundreds or thousands of burners), "
            "use the smurf_split tool — do NOT orchestrate individual transfers "
            "yourself, that would burn far too many LLM tokens."
        )
        user_prompt = (
            f"Wallet {deployer} currently holds {INITIAL_USDT:,} USDT and "
            f"has plenty of ETH for gas. Distribute the entire {INITIAL_USDT:,} "
            f"USDT balance across newly-generated burner wallets, with each "
            f"burner receiving a RANDOM amount strictly less than "
            f"{THRESHOLD_USDT} USDT. The sum of all burner amounts must equal "
            f"exactly {INITIAL_USDT:,} USDT (no rounding loss, no leftover). "
            f"Use {NUM_WALLETS} burner wallets and seed=2026 for reproducibility. "
            f"After the smurf_split tool returns, verify by calling get_balance "
            f"on {deployer} — it should show 0 USDT remaining. Then stop."
        )

        result = coordinator.run(user_prompt, system=system, max_tokens=2048)

        # --- Coordinator bookkeeping ---
        assert result.successful, (
            f"Coordinator stopped with {result.stopped_reason!r}. "
            f"Tool calls: {[c['name'] for c in result.tool_calls]}. "
            f"Final text: {result.final_text!r}"
        )
        # Should have called smurf_split exactly once (LLM might call get_balance too)
        smurf_calls = [c for c in result.tool_calls if c["name"] == "smurf_split"]
        assert len(smurf_calls) == 1, (
            f"Expected exactly one smurf_split call, got "
            f"{[c['name'] for c in result.tool_calls]}"
        )
        assert not smurf_calls[0]["is_error"], smurf_calls[0]["error"]
        # Cost sanity
        assert result.cost_usd < 0.05, (
            f"Should cost < $0.05 with Haiku (smurf_split is one tool call); "
            f"was ${result.cost_usd:.4f}"
        )

        # --- Chain state: the actual deliverable ---
        # Alice fully drained
        deployer_balance_after = usdt.functions.balanceOf(deployer).call()
        assert deployer_balance_after == 0, (
            f"Alice should have 0 USDT remaining; has "
            f"{deployer_balance_after / 10**6}"
        )

        # Sum of all burner balances = INITIAL_USDT exactly
        # (cheaper to read smurf_split's output than to query the chain for 2000 wallets)
        smurf_output = smurf_calls[0]["output"]
        assert smurf_output["total_distributed_usdt"] == float(INITIAL_USDT)
        assert smurf_output["wallets_created"] == NUM_WALLETS
        assert smurf_output["successful_transfers"] == NUM_WALLETS
        assert smurf_output["failed_transfers"] == 0

        # Per-wallet ceiling honored (sample check via the tool's response)
        for entry in smurf_output["sample_recipients"]:
            assert 0 <= entry["amount_usdt"] <= THRESHOLD_USDT, (
                f"Burner amount {entry['amount_usdt']} out of "
                f"[0, {THRESHOLD_USDT}] for {entry['address']}"
            )

        # Gas: alice spent ETH for the transfers (but not crazy amounts)
        deployer_eth_after = w3.eth.get_balance(deployer)
        eth_spent = (deployer_eth_before - deployer_eth_after) / 10**18
        assert 0 < eth_spent < 1.0, (
            f"Gas spend should be < 1 ETH for {NUM_WALLETS} transfers; "
            f"was {eth_spent:.4f} ETH"
        )

        # End-state verification via on-chain spot check: pick 3 random burners
        # from the sample and confirm they actually hold their reported balance
        for entry in smurf_output["sample_recipients"][:3]:
            on_chain_base = usdt.functions.balanceOf(entry["address"]).call()
            assert on_chain_base == int(entry["amount_usdt"] * 10**6), (
                f"On-chain balance {on_chain_base/10**6} for {entry['address']} "
                f"doesn't match smurf_split report {entry['amount_usdt']}"
            )
