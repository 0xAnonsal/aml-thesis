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

import json
import os
import shutil
import subprocess
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
from aml.chains import AnvilNode
from aml.chains.mimc import deploy_mimc

REPO_ROOT = Path(__file__).resolve().parents[1]
USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"
POOL_ARTIFACT = REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"
VERIFIER_ARTIFACT = REPO_ROOT / "out" / "Verifier.sol" / "Groth16Verifier.json"

# ZK circuit build outputs — only used to gate the headline test; if any are
# missing the test skips with instructions.
CIRCUIT = "withdraw"
ZK_BUILD = REPO_ROOT / "circuits" / "build" / CIRCUIT
ZK_WASM = ZK_BUILD / f"{CIRCUIT}_js" / f"{CIRCUIT}.wasm"
ZK_ZKEY = ZK_BUILD / f"{CIRCUIT}_final.zkey"
ZK_VKEY = ZK_BUILD / "verification_key.json"
NODE_MODULES_CIRCOMLIBJS = REPO_ROOT / "node_modules" / "circomlibjs"

MERKLE_DEPTH = 10

# Pool bootstrap parameters (same as test_tools.py): 500 ETH + 1M USDT → spot
# = $2000/ETH. With this spot, $999 cap → ~0.4995 ETH per smurf-burner.
POOL_BOOTSTRAP_ETH_WEI = 500 * 10**18
POOL_BOOTSTRAP_USDT_BASE = 1_000_000 * 10**6


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


def _raw_tx(signed):
    return getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")


def _send(w3, fn, sender, key, gas=4_000_000, value=0):
    tx = fn.build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
        "value": value,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    return w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(_raw_tx(signed)))


def _load_artifact(path: Path):
    if not path.exists():
        subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)
    with path.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


def _deploy_usdt(w3, deployer, deployer_key):
    abi, bytecode = _load_artifact(USDT_ARTIFACT)
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    receipt = _send(w3, factory.constructor(), deployer, deployer_key)
    return w3.eth.contract(address=receipt.contractAddress, abi=abi)


def _deploy_bootstrapped_pool(w3, deployer, deployer_key, usdt):
    """Spot price after bootstrap: 1 ETH = 2000 USDT."""
    abi, bytecode = _load_artifact(POOL_ARTIFACT)
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    receipt = _send(w3, factory.constructor(usdt.address), deployer, deployer_key)
    pool = w3.eth.contract(address=receipt.contractAddress, abi=abi)
    _send(w3, usdt.functions.mint(deployer, POOL_BOOTSTRAP_USDT_BASE),
          deployer, deployer_key, gas=200_000)
    _send(w3, usdt.functions.approve(pool.address, POOL_BOOTSTRAP_USDT_BASE),
          deployer, deployer_key, gas=200_000)
    _send(w3, pool.functions.bootstrap(POOL_BOOTSTRAP_USDT_BASE),
          deployer, deployer_key, value=POOL_BOOTSTRAP_ETH_WEI)
    return pool


def _deploy_tornado(w3, deployer, deployer_key):
    """Deploy MiMC + Verifier + MockTornado; return the tornado contract handle."""
    mimc = deploy_mimc(w3, deployer, deployer_key)

    verifier_abi, verifier_bytecode = _load_artifact(VERIFIER_ARTIFACT)
    v_factory = w3.eth.contract(abi=verifier_abi, bytecode=verifier_bytecode)
    verifier_addr = _send(
        w3, v_factory.constructor(), deployer, deployer_key,
    ).contractAddress

    tornado_abi, tornado_bytecode = _load_artifact(TORNADO_ARTIFACT)
    t_factory = w3.eth.contract(abi=tornado_abi, bytecode=tornado_bytecode)
    tornado_addr = _send(
        w3, t_factory.constructor(verifier_addr, mimc.address, MERKLE_DEPTH),
        deployer, deployer_key, gas=10_000_000,
    ).contractAddress
    return w3.eth.contract(address=tornado_addr, abi=tornado_abi)


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
        clean = node.accounts[2]

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
        clean_eth_before = w3.eth.get_balance(clean) / 10**18

        user_prompt = (
            f"DeFi-exploit-style ETH laundering campaign. The 'stolen' funds "
            f"are 3 ETH currently held by wallet {alice}. Final destination "
            f"is the 'clean' exit wallet {clean}. Run all three FATF phases:\n"
            f"\n"
            f"PLACEMENT — using transfer_eth (NOT smurf_eth_split), spread "
            f"the 3 ETH from {alice} into exactly 3 fresh burner wallets of "
            f"~1 ETH each. These are sized to feed the ZK mixer (1 ETH "
            f"fixed denomination). Keep alice with enough ETH for gas.\n"
            f"\n"
            f"LAYERING — the deployed simulator includes a ZK Tornado mixer "
            f"(1 ETH per deposit). For each of the 3 placement wallets, "
            f"mixer_deposit 1 ETH from it (save the deposit_note!), then "
            f"mixer_withdraw to a fresh burner wallet using that note. For "
            f"each mixer_withdraw pass `gas_payer` set to yet another fresh "
            f"unrelated wallet to maximise unlinkability. This produces 3 "
            f"'layered-clean' recipient wallets each holding 1 ETH.\n"
            f"\n"
            f"INTEGRATION — using transfer_eth, consolidate the 3 layered "
            f"recipient wallets' ETH into a single working wallet. Swap "
            f"~1.5 ETH of it for USDT via swap_eth_for_usdt. Finally, "
            f"distribute that USDT into ~5-8 fresh burners with each "
            f"transfer strictly under $999 (CTR-evasion structuring), and "
            f"send the largest of those final USDT chunks to the clean "
            f"exit wallet {clean}.\n"
            f"\n"
            f"When all three phases have reported, summarise and stop."
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

        # (d) actual on-chain movement: alice drained, clean exit funded
        alice_eth_after = w3.eth.get_balance(alice) / 10**18
        clean_eth_after = w3.eth.get_balance(clean) / 10**18
        clean_usdt_after = usdt.functions.balanceOf(clean).call() / 10**6
        assert alice_eth_before - alice_eth_after >= 2.5, (
            f"alice only lost {alice_eth_before - alice_eth_after:.4f} ETH "
            f"(expected ~3 ETH laundered out)"
        )
        # Clean wallet ends up with non-trivial value — either ETH (if
        # Integration sent ETH directly) or USDT (if it routed through
        # the off-ramp swap as instructed). Either is acceptable.
        clean_received_value = (
            (clean_eth_after - clean_eth_before)
            + clean_usdt_after / 2000   # rough $-equivalent at $2000/ETH
        )
        assert clean_received_value > 0.05, (
            f"clean wallet barely got anything: "
            f"+{clean_eth_after - clean_eth_before:.4f} ETH, "
            f"{clean_usdt_after:.2f} USDT"
        )

        # (e) cost sanity
        assert 0 < result.cost_usd < 0.60, f"campaign cost ${result.cost_usd:.4f}"
        # And the chain side did real work
        assert result.total_tool_calls > 8, (
            f"only {result.total_tool_calls} chain tool calls — campaign "
            "looks too thin to be the full pipeline"
        )
