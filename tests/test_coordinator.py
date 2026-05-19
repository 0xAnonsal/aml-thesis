"""Tests for the multi-agent FATF Coordinator (the orchestrator).

The Coordinator delegates to tool-scoped Placement / Layering / Integration
sub-agents via delegate_to_* tools; it makes no chain calls itself.

Structural tests run with no API key and no chain. Two live tests:
- single-phase placement delegation (cheap sanity)
- the headline ETH laundering campaign: alice → smurfed-or-hopped into
  working wallets → ZK Tornado mixer → consolidated + swapped → sub-$999
  USDT off-ramp into a clean exit wallet. Exercises the full project
  pipeline end-to-end including the ZK mixer (the headline contribution).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from web3 import Web3

from aml.attackers import (
    CampaignResult,
    Coordinator,
    LLMClient,
    SubAgent,
    SubAgentResult,
    ToolDispatcher,
)
from aml.attackers.scenarios import DEFI_EXPLOIT
from aml.chains import AnvilNode
from aml.chains.eth_stack import (
    VERIFIER_ARTIFACT,
    deploy_pool as _deploy_bootstrapped_pool,
    deploy_tornado as _deploy_tornado,
    deploy_usdt as _deploy_usdt,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# ZK circuit build outputs — only used to gate the headline test; if any are
# missing the test skips with instructions.
CIRCUIT = "withdraw"
ZK_BUILD = REPO_ROOT / "circuits" / "build" / CIRCUIT
ZK_WASM = ZK_BUILD / f"{CIRCUIT}_js" / f"{CIRCUIT}.wasm"
ZK_ZKEY = ZK_BUILD / f"{CIRCUIT}_final.zkey"
ZK_VKEY = ZK_BUILD / "verification_key.json"
NODE_MODULES_CIRCOMLIBJS = REPO_ROOT / "node_modules" / "circomlibjs"


needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH",
)
needs_api_key = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; cannot make live API calls",
)
needs_zk_setup = pytest.mark.skipif(
    not (ZK_WASM.exists() and ZK_ZKEY.exists() and ZK_VKEY.exists()
         and VERIFIER_ARTIFACT.exists()),
    reason=f"ZK setup missing; run bash scripts/setup_zk.sh {CIRCUIT} && forge build",
)
needs_circomlibjs = pytest.mark.skipif(
    not NODE_MODULES_CIRCOMLIBJS.exists() or shutil.which("node") is None,
    reason="circomlibjs not installed or node missing",
)
needs_snarkjs = pytest.mark.skipif(
    shutil.which("snarkjs") is None,
    reason="snarkjs not on PATH",
)


# --- Structural tests (no API, no chain) ---------------------------------


def test_campaign_result_successful_flag():
    """CampaignResult.successful is True only when stopped_reason == 'end_turn'."""
    assert CampaignResult(final_text="", iterations=1, stopped_reason="end_turn").successful
    assert not CampaignResult(
        final_text="", iterations=1, stopped_reason="max_iterations",
    ).successful
    assert not CampaignResult(
        final_text="", iterations=1, stopped_reason="refusal",
    ).successful


def test_campaign_result_total_tool_calls_aggregates_sub_agents():
    """total_tool_calls sums chain tool calls across every sub-agent run."""
    result = CampaignResult(
        final_text="done", iterations=3,
        sub_agent_runs=[
            SubAgentResult(status="success", summary="p",
                           tool_calls=[{"name": "transfer_eth"}, {"name": "mixer_deposit"}]),
            SubAgentResult(status="success", summary="l",
                           tool_calls=[{"name": "smurf_split"}]),
        ],
    )
    assert result.total_tool_calls == 3


def test_coordinator_exposes_only_delegate_tools():
    """The Coordinator's tool surface is exactly the three delegate_to_* tools."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    coordinator = Coordinator(LLMClient(api_key="dummy"), dispatcher)
    names = {t["name"] for t in coordinator.delegate_tool_definitions}
    assert names == {
        "delegate_to_placement",
        "delegate_to_layering",
        "delegate_to_integration",
    }
    for t in coordinator.delegate_tool_definitions:
        props = t["input_schema"]["properties"]
        assert set(props) == {"objective", "context"}
        assert t["input_schema"]["required"] == ["objective", "context"]


def test_coordinator_builds_three_scoped_sub_agents():
    """Each FATF role gets a SubAgent scoped to its allowed chain tools.

    Per-role invariants: Placement can mint and structure but not mix.
    Layering owns the obfuscation tools (mixer, both smurf variants, swaps).
    Integration consolidates and off-ramps but neither mints nor mixes.
    Every role gets the gas-discipline primitives (get_gas_budget,
    transfer_eth, get_balance).
    """
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    coordinator = Coordinator(LLMClient(api_key="dummy"), dispatcher)

    assert set(coordinator.sub_agents) == {"placement", "layering", "integration"}
    for role, agent in coordinator.sub_agents.items():
        assert isinstance(agent, SubAgent)
        assert agent.name == role

    placement = set(coordinator.sub_agents["placement"].tool_names)
    layering = set(coordinator.sub_agents["layering"].tool_names)
    integration = set(coordinator.sub_agents["integration"].tool_names)

    # Every role gets the gas-discipline + read primitives.
    for role_tools in (placement, layering, integration):
        assert {"get_balance", "get_gas_budget", "transfer_eth"} <= role_tools

    # Placement: can position funds (mint, structure ETH) but no mixer.
    assert {"mint_usdt", "smurf_eth_split", "generate_burner_wallet"} <= placement
    assert "mixer_deposit" not in placement
    assert "mixer_withdraw" not in placement
    assert "smurf_split" not in placement   # USDT-side structuring is Layering's

    # Layering: owns the obfuscation tools.
    assert {"smurf_split", "smurf_eth_split", "mixer_deposit", "mixer_withdraw",
            "swap_eth_for_usdt", "swap_usdt_for_eth"} <= layering

    # Integration: consolidates and off-ramps; no minting, no mixing.
    assert {"transfer_usdt", "swap_eth_for_usdt", "swap_usdt_for_eth"} <= integration
    assert "mint_usdt" not in integration
    assert "mixer_deposit" not in integration
    assert "mixer_withdraw" not in integration
    assert "smurf_eth_split" not in integration   # structuring is Placement/Layering


# --- Live tests ----------------------------------------------------------


@needs_foundry
@needs_api_key
def test_coordinator_delegates_single_phase():
    """Cheap delegation-mechanism sanity check — placement-only USDT mint.

    Proves the orchestration loop end-to-end: Coordinator delegates ->
    SubAgent runs -> structured report fed back -> Coordinator stops.
    Haiku throughout (~$0.05). No mixer / pool / ZK toolchain needed.
    """
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice = node.accounts[1]
        usdt = _deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        coordinator = Coordinator(
            LLMClient(), dispatcher,
            model="haiku", sub_agent_model="haiku", max_iterations=8,
        )

        user_prompt = (
            f"This is a Placement-only campaign. Wallet {deployer} is the "
            f"operator. Establish the initial position: mint exactly 5000 "
            f"USDT to wallet {alice}. There is no layering or integration "
            f"work in this campaign — once placement reports success, the "
            f"campaign is complete."
        )
        result = coordinator.run(
            user_prompt, max_tokens=2048, sub_agent_max_tokens=1536,
        )

        assert result.successful, (
            f"stopped_reason={result.stopped_reason!r}, "
            f"delegations={result.delegations}"
        )
        roles = [d["role"] for d in result.delegations]
        assert "placement" in roles
        assert "layering" not in roles and "integration" not in roles
        assert len(result.sub_agent_runs) >= 1
        assert result.sub_agent_runs[0].ok
        assert 0 < result.cost_usd < 0.10
        assert usdt.functions.balanceOf(alice).call() == 5000 * 10**6


@needs_foundry
@needs_api_key
@needs_zk_setup
@needs_circomlibjs
@needs_snarkjs
def test_coordinator_runs_full_eth_laundering_campaign():
    """Headline 6.2 test: full ETH laundering through Placement → ZK mixer →
    Integration off-ramp, driven entirely by delegation.

    Scenario: DeFi-exploit-style ETH theft. Alice holds 3 ETH of "stolen"
    funds. The Coordinator must drive a full three-phase campaign:
      Placement   — split into ~3 working wallets sized for the mixer
      Layering    — mixer_deposit + mixer_withdraw cycles (this is the
                    headline: the ZK mixer is the project's signature
                    contribution and this test exercises it end-to-end)
      Integration — consolidate post-mixer ETH, swap a portion to USDT,
                    structure into sub-$999 chunks ending at a clean
                    exit wallet

    Asserts the multi-agent system:
      (a) successfully completed (end_turn, not max_iterations)
      (b) delegated all three FATF phases
      (c) actually exercised the mixer (mixer_deposit appears in tool calls)
      (d) actually moved value on-chain (alice drained, clean wallet funded)
      (e) cost stays sane (Haiku throughout, <$0.60)

    Setup is heavy (USDT + bootstrapped pool + MiMC + Verifier + Tornado);
    live run ~4-6 min and ~$0.20-0.50. Skips cleanly if ANTHROPIC_API_KEY,
    Foundry, ZK build outputs, circomlibjs, or snarkjs are missing.
    """
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        # Multiple labeled clean exit wallets — realistic launderers fan out
        # to many off-ramp accounts (different exchanges, different KYC'd
        # identities), so the agent picks a subset of these and routes
        # varied amounts. The detector's job is later to identify these
        # exits as belonging to the same actor — actor-level clustering,
        # which IS the locked-scope novelty claim. Using anvil accounts
        # 2-5 (4 exits total).
        clean_exits = list(node.accounts[2:2 + DEFI_EXPLOIT.default_num_clean_exits])

        usdt = _deploy_usdt(w3, deployer, deployer_key)
        pool = _deploy_bootstrapped_pool(w3, deployer, deployer_key, usdt)
        tornado = _deploy_tornado(w3, deployer, deployer_key)

        # Alice is the laundering source (registered so sub-agents can move
        # her ETH). Deployer is the faucet wallet — its first-in-registry
        # position is what `_seed_gas` and `_mint_usdt` rely on.
        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt,
            wallets={deployer: deployer_key, alice: alice_key},
            pool_contract=pool, tornado_contract=tornado,
        )
        coordinator = Coordinator(
            LLMClient(), dispatcher,
            model="haiku", sub_agent_model="haiku",
            max_iterations=12, sub_agent_max_iterations=20,
        )

        alice_eth_before = w3.eth.get_balance(alice) / 10**18
        exits_eth_before = {a: w3.eth.get_balance(a) / 10**18 for a in clean_exits}
        exits_usdt_before = {
            a: usdt.functions.balanceOf(a).call() / 10**6 for a in clean_exits
        }

        # Single source of truth for this scenario's prompt — same one the
        # run_campaign CLI uses, imported from aml.attackers.scenarios.
        user_prompt = DEFI_EXPLOIT.format_prompt(
            alice=alice, clean_exits=clean_exits, amount=3.0,
        )
        result = coordinator.run(
            user_prompt, max_tokens=2048, sub_agent_max_tokens=2048,
        )

        # (a) successful
        assert result.successful, (
            f"stopped_reason={result.stopped_reason!r}\n"
            f"delegations={[(d['role'], d['status']) for d in result.delegations]}\n"
            f"final_text={result.final_text!r}"
        )

        # (b) all three FATF phases delegated
        roles = {d["role"] for d in result.delegations}
        assert roles == {"placement", "layering", "integration"}, (
            f"expected all three FATF phases, got {roles}"
        )

        # (c) the mixer was actually exercised (mixer_deposit + mixer_withdraw
        # both appear in some sub-agent's tool calls)
        all_tool_names = {
            call.get("name")
            for run in result.sub_agent_runs
            for call in run.tool_calls
        }
        assert "mixer_deposit" in all_tool_names, (
            f"mixer was never deposited into; tool calls = {sorted(all_tool_names)}"
        )
        assert "mixer_withdraw" in all_tool_names, (
            f"mixer deposits were never withdrawn; tool calls = "
            f"{sorted(all_tool_names)}"
        )

        # (d) actual on-chain movement: alice drained AND value reached
        # the labeled clean exit set. We accept fan-out — only require
        # that AT LEAST ONE clean exit received value, AND the total
        # delivered to the exit set is non-trivial. The agent is free to
        # use any subset of the exits with any distribution.
        alice_eth_after = w3.eth.get_balance(alice) / 10**18
        assert alice_eth_before - alice_eth_after >= 2.5, (
            f"alice only lost {alice_eth_before - alice_eth_after:.4f} ETH "
            f"(expected ~3 ETH laundered out)"
        )
        exits_eth_after = {a: w3.eth.get_balance(a) / 10**18 for a in clean_exits}
        exits_usdt_after = {
            a: usdt.functions.balanceOf(a).call() / 10**6 for a in clean_exits
        }
        per_exit_value = {
            a: (exits_eth_after[a] - exits_eth_before[a])
               + (exits_usdt_after[a] - exits_usdt_before[a]) / 2000
            for a in clean_exits
        }
        funded_exits = [a for a, v in per_exit_value.items() if v > 0]
        total_to_exits = sum(per_exit_value.values())
        assert len(funded_exits) >= 1, (
            f"no clean exit received any value. Per-exit deltas: "
            f"{ {a: round(v, 4) for a, v in per_exit_value.items()} }"
        )
        # ~0.05 ETH-equivalent ≈ $100 — a sliver of the ~$6000 stolen.
        # If integration delivered ANYTHING meaningful, this passes.
        assert total_to_exits > 0.05, (
            f"only {total_to_exits:.4f} ETH-equiv ({total_to_exits * 2000:.2f} "
            f"USDT-equiv) reached the {len(clean_exits)} clean exits combined; "
            f"per-exit: { {a[:10]: round(v, 4) for a, v in per_exit_value.items()} }"
        )

        # (e) cost sanity + real chain work
        assert 0 < result.cost_usd < 0.60, f"campaign cost ${result.cost_usd:.4f}"
        assert result.total_tool_calls > 8, (
            f"only {result.total_tool_calls} chain tool calls — campaign "
            "looks too thin to be the full pipeline"
        )
