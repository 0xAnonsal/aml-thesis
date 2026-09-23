"""Tests for SubAgent — the tool-scoped FATF worker agent.

Structural tests run with no API key and no chain. The live tests drive a
real Haiku agent through a scoped tool loop ending in finish_task; they cost
~$0.002-0.005 each and skip cleanly without ANTHROPIC_API_KEY or Foundry.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3

from aml.attackers import LLMClient, SubAgent, SubAgentResult, ToolDispatcher
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


# --- Structural tests (no API, no chain) ---------------------------------


def test_sub_agent_result_ok_flag():
    """SubAgentResult.ok is True for success/partial, False otherwise."""
    assert SubAgentResult(status="success", summary="x").ok
    assert SubAgentResult(status="partial", summary="x").ok
    assert not SubAgentResult(status="failed", summary="x").ok
    assert not SubAgentResult(status="incomplete", summary="x").ok
    assert not SubAgentResult(status="error", summary="x").ok


def test_sub_agent_result_delegation_report_is_compact():
    """to_delegation_report() carries summary + key_facts but NOT the transcript."""
    r = SubAgentResult(
        status="success",
        summary="moved 100 USDT",
        key_facts={"recipient": "0xabc", "amount": 100},
        tool_calls=[{"name": "transfer_usdt"}, {"name": "get_balance"}],
        iterations=3,
        cost_usd=0.0123456,
        messages=[{"role": "user", "content": "internal transcript"}],
    )
    report = r.to_delegation_report()
    assert report["status"] == "success"
    assert report["summary"] == "moved 100 USDT"
    assert report["key_facts"] == {"recipient": "0xabc", "amount": 100}
    assert report["tool_calls"] == 2          # count, not the list
    assert report["iterations"] == 3
    assert report["cost_usd"] == 0.012346     # rounded to 6 dp
    assert "messages" not in report           # transcript stays internal


def test_sub_agent_rejects_unknown_tool_names():
    """Constructing a SubAgent with a tool the dispatcher doesn't have raises."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    with pytest.raises(ValueError, match="unknown tool"):
        SubAgent(
            client=LLMClient(api_key="dummy"),
            dispatcher=dispatcher,
            tool_names=["transfer_usdt", "not_a_real_tool"],
            name="placement",
        )


def test_sub_agent_scopes_tool_schemas():
    """A SubAgent exposes exactly its scoped subset plus finish_task."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    agent = SubAgent(
        client=LLMClient(api_key="dummy"),
        dispatcher=dispatcher,
        tool_names=["get_balance", "transfer_usdt"],
        name="placement",
    )
    exposed = {t["name"] for t in agent.tool_definitions}
    assert exposed == {"get_balance", "transfer_usdt", "finish_task"}
    # Tools outside this agent's scope are NOT visible to it.
    assert "smurf_split" not in exposed
    assert "mixer_deposit" not in exposed


# --- Live tests ----------------------------------------------------------


@needs_foundry
@needs_api_key
def test_sub_agent_read_only_scope_reports_via_finish_task():
    """A SubAgent scoped to just get_balance reads a balance and reports back.

    Cheapest possible live test (~$0.002 on Haiku): proves the scoped loop +
    finish_task interception + structured result all work end to end.
    """
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        _send(w3, usdt.functions.mint(deployer, 4242 * 10**6),
              deployer, deployer_key, gas=200_000)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        agent = SubAgent(
            LLMClient(), dispatcher, tool_names=["get_balance"],
            model="haiku", name="recon", max_iterations=6,
        )
        system = (
            "You are a read-only reconnaissance agent. Use get_balance to "
            "answer the objective, then call finish_task with the result. "
            "Always end by calling finish_task."
        )
        result = agent.run(
            objective=(
                f"Report the exact USDT balance of wallet {deployer}. "
                f"Put the number in key_facts under the key 'usdt_balance'."
            ),
            system=system,
            max_tokens=1024,
        )

        assert isinstance(result, SubAgentResult)
        assert result.stopped_reason == "finish_task", (
            f"expected finish_task, got {result.stopped_reason!r} "
            f"(summary: {result.summary!r})"
        )
        assert result.status == "success"
        assert result.ok
        assert result.cost_usd > 0
        # The agent actually called get_balance
        assert any(c["name"] == "get_balance" for c in result.tool_calls)
        # ...and reported the balance it read (4242) somewhere in key_facts
        assert "4242" in json.dumps(result.key_facts), (
            f"expected balance 4242 in key_facts, got {result.key_facts}"
        )


@needs_foundry
@needs_api_key
def test_sub_agent_placement_scope_executes_transfer():
    """A Placement-scoped SubAgent funds a burner and reports its address.

    Exercises a state-changing scoped loop end to end: the agent generates a
    burner, transfers USDT to it, and passes the burner address back through
    key_facts. ~$0.004 on Haiku.
    """
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        # The deployer is chain infrastructure and is rejected as a sender by
        # the dispatcher, so the agent's source of funds must be a non-deployer
        # wallet (a second Anvil account). The deployer stays first (infra).
        alice, alice_key = node.accounts[1], node.private_keys[1]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        _send(w3, usdt.functions.mint(alice, 1000 * 10**6),
              deployer, deployer_key, gas=200_000)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt,
            wallets={deployer: deployer_key, alice: alice_key},
        )
        agent = SubAgent(
            LLMClient(), dispatcher,
            tool_names=["get_balance", "generate_burner_wallet", "transfer_usdt"],
            model="haiku", name="placement", max_iterations=10,
        )
        system = (
            "You are the Placement agent in a research laundering simulation. "
            "Execute the objective with your tools, then call finish_task. "
            "Report every wallet address you create in key_facts. Always end "
            "by calling finish_task."
        )
        result = agent.run(
            objective=(
                f"Wallet {alice} holds 1000 USDT. Generate ONE fresh burner "
                f"wallet and transfer exactly 300 USDT from {alice} to it. "
                f"Put the burner's address in key_facts under 'burner_address'."
            ),
            system=system,
            max_tokens=1536,
        )

        assert result.stopped_reason == "finish_task", (
            f"expected finish_task, got {result.stopped_reason!r}"
        )
        assert result.status == "success", (
            f"status={result.status}, summary={result.summary!r}"
        )
        # The scoped tools were used
        names = [c["name"] for c in result.tool_calls]
        assert "generate_burner_wallet" in names
        assert "transfer_usdt" in names
        # The reported burner actually holds 300 USDT on-chain
        burner = result.key_facts.get("burner_address")
        assert burner and Web3.is_address(burner), (
            f"expected a burner address in key_facts, got {result.key_facts}"
        )
        burner = Web3.to_checksum_address(burner)
        assert usdt.functions.balanceOf(burner).call() == 300 * 10**6, (
            f"burner {burner} should hold 300 USDT on-chain"
        )
        assert usdt.functions.balanceOf(alice).call() == 700 * 10**6
        assert result.cost_usd < 0.02
