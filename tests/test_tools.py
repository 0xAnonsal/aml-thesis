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
POOL_ARTIFACT = REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"

# Pool bootstrap parameters: 500 ETH + 1M USDT → spot = $2000/ETH
POOL_BOOTSTRAP_ETH_WEI = 500 * 10**18
POOL_BOOTSTRAP_USDT_BASE = 1_000_000 * 10**6


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


def _deploy_bootstrapped_pool(w3, deployer, deployer_key, usdt):
    """Deploy MockUniswapV2Pool, mint+approve USDT, bootstrap with ETH+USDT.

    Returns the bootstrapped pool contract handle. Spot price after bootstrap:
    1 ETH = 2000 USDT (500 ETH / 1M USDT pool).
    """
    if not POOL_ARTIFACT.exists():
        subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)
    with POOL_ARTIFACT.open() as f:
        a = json.load(f)
    factory = w3.eth.contract(abi=a["abi"], bytecode=a["bytecode"]["object"])
    receipt = _send(w3, factory.constructor(usdt.address), deployer, deployer_key)
    pool = w3.eth.contract(address=receipt.contractAddress, abi=a["abi"])

    _send(w3, usdt.functions.mint(deployer, POOL_BOOTSTRAP_USDT_BASE),
          deployer, deployer_key, gas=200_000)
    _send(w3, usdt.functions.approve(pool.address, POOL_BOOTSTRAP_USDT_BASE),
          deployer, deployer_key, gas=200_000)
    _send(w3, pool.functions.bootstrap(POOL_BOOTSTRAP_USDT_BASE),
          deployer, deployer_key, value=POOL_BOOTSTRAP_ETH_WEI)
    return pool


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


# --- New tools (PR 5.4) ---------------------------------------------------


def test_generate_burner_wallet_no_anvil():
    """Burner generation is pure off-chain crypto — no Anvil required."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    result = dispatcher.dispatch("generate_burner_wallet", {})
    assert not result.is_error
    address = result.output["address"]
    assert address.startswith("0x") and len(address) == 42
    # Auto-registered in the dispatcher
    assert address in dispatcher.wallets
    # Distinct addresses on each call
    second = dispatcher.dispatch("generate_burner_wallet", {})
    assert second.output["address"] != address
    assert len(dispatcher.wallets) == 2


def test_random_split_base_units_sums_exactly():
    """The split planner returns integers in range that sum to total exactly."""
    from aml.attackers.tools import ToolDispatcher
    # 1,000,000 USDT in base units = 10^12; max per wallet 999.999 USDT = 999_999_000
    total = 10**12
    max_per = 999_999_000
    count = 2000
    amounts = ToolDispatcher._random_split_base_units(total, max_per, count, seed=42)
    assert len(amounts) == count
    assert sum(amounts) == total
    assert all(0 <= a <= max_per for a in amounts)


def test_random_split_base_units_capacity_check():
    """Capacity exceeded → raises before doing any work."""
    from aml.attackers.tools import ToolDispatcher
    with pytest.raises(ValueError, match="Capacity"):
        # 10 wallets × 100 base units = 1000 < total 5000
        ToolDispatcher._random_split_base_units(5000, 100, 10, seed=1)


def test_random_split_base_units_seed_reproducible():
    from aml.attackers.tools import ToolDispatcher
    a1 = ToolDispatcher._random_split_base_units(10**6, 10**4, 100, seed=7)
    a2 = ToolDispatcher._random_split_base_units(10**6, 10**4, 100, seed=7)
    assert a1 == a2


@needs_foundry
def test_mint_usdt_round_trip():
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        bob = node.accounts[1]
        usdt = _deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        result = dispatcher.dispatch("mint_usdt", {
            "to_address": bob, "amount_usdt": 500.0,
        })
        assert not result.is_error, result.error
        assert result.output["new_balance_usdt"] == 500.0
        assert dispatcher.dispatch(
            "get_balance", {"address": bob, "asset": "USDT"},
        ).output["balance"] == 500.0


@needs_foundry
def test_smurf_split_capacity_check_no_chain():
    """Validation runs before any chain call — bad inputs return error fast."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        # 5 wallets × 100 USDT = 500 < 1000 requested
        result = dispatcher.dispatch("smurf_split", {
            "from_address": deployer, "total_usdt": 1000.0,
            "num_wallets": 5, "max_per_wallet": 100.0,
        })
        assert result.is_error
        assert "capacity" in result.error.lower()


@needs_foundry
def test_smurf_split_cap_enforced():
    """num_wallets > _MAX_BURNERS_PER_SMURF: rejected, no chain calls."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        result = dispatcher.dispatch("smurf_split", {
            "from_address": deployer, "total_usdt": 1.0,
            "num_wallets": 10**9, "max_per_wallet": 1.0,
        })
        assert result.is_error
        assert "cap" in result.error.lower()


# --- Swap tools (PR 5.5) -------------------------------------------------


@needs_foundry
def test_get_swap_quote_eth_for_usdt():
    """Quote 1 ETH → USDT given the bootstrapped pool (500 ETH / 1M USDT)."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        pool = _deploy_bootstrapped_pool(w3, deployer, deployer_key, usdt)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, pool_contract=pool,
            wallets={deployer: deployer_key},
        )
        result = dispatcher.dispatch("get_swap_quote", {
            "from_asset": "ETH", "amount": 1.0,
        })
        assert not result.is_error, result.error
        # Spot at 500 ETH / 1M USDT = 2000 USDT/ETH
        assert abs(result.output["spot_price_usdt_per_eth"] - 2000) < 0.01
        # 1 ETH swap should yield ~1990 USDT (after 0.3% fee + slippage on 0.2% of pool)
        assert 1985 < result.output["expected_out_usdt"] < 2000
        # Total cost (fee + slippage) is small for 1 ETH out of 500
        assert 0 < result.output["total_cost_pct"] < 1.0


@needs_foundry
def test_swap_eth_for_usdt_matches_quote():
    """The actual swap result matches the quote within rounding."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        pool = _deploy_bootstrapped_pool(w3, deployer, deployer_key, usdt)
        alice = node.accounts[1]
        alice_key = node.private_keys[1]

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, pool_contract=pool,
            wallets={alice: alice_key},
        )

        quote = dispatcher.dispatch("get_swap_quote", {
            "from_asset": "ETH", "amount": 1.0,
        })
        result = dispatcher.dispatch("swap_eth_for_usdt", {
            "from_address": alice, "eth_amount": 1.0,
        })
        assert not result.is_error, result.error
        # Quote and actual should be within 0.01 USDT (Anvil mines instantly so
        # no other tx changed reserves between quote and swap)
        assert abs(result.output["usdt_received"] - quote.output["expected_out_usdt"]) < 0.01
        # On-chain USDT balance matches what we got back
        actual = usdt.functions.balanceOf(alice).call() / 10**6
        assert abs(actual - result.output["usdt_received"]) < 0.001


@needs_foundry
def test_swap_usdt_for_eth_round_trip():
    """Approve + swap done atomically inside the dispatcher; balances move correctly."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        pool = _deploy_bootstrapped_pool(w3, deployer, deployer_key, usdt)
        alice = node.accounts[1]
        alice_key = node.private_keys[1]
        # Mint 5000 USDT to alice so she has something to swap
        _send(w3, usdt.functions.mint(alice, 5000 * 10**6),
              deployer, deployer_key, gas=200_000)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, pool_contract=pool,
            wallets={alice: alice_key},
        )
        result = dispatcher.dispatch("swap_usdt_for_eth", {
            "from_address": alice, "usdt_amount": 5000.0,
        })
        assert not result.is_error, result.error
        # 5000 USDT / 2000 spot ≈ 2.5 ETH minus fee/slippage on 0.5% of pool
        assert 2.4 < result.output["eth_received"] < 2.5
        # Alice's USDT balance dropped exactly by 5000
        assert usdt.functions.balanceOf(alice).call() == 0


@needs_foundry
def test_swap_slippage_protection_reverts_cleanly():
    """min_usdt_out higher than actual output → tool reverts as a clean error, no crash."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        pool = _deploy_bootstrapped_pool(w3, deployer, deployer_key, usdt)
        alice = node.accounts[1]
        alice_key = node.private_keys[1]

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, pool_contract=pool,
            wallets={alice: alice_key},
        )
        # 1 ETH yields ~1990 USDT; asking for 5000 is impossible
        result = dispatcher.dispatch("swap_eth_for_usdt", {
            "from_address": alice, "eth_amount": 1.0, "min_usdt_out": 5000.0,
        })
        assert result.is_error
        assert "slippage" in result.error.lower() or "revert" in result.error.lower()


def test_swap_tools_no_pool_set_returns_error():
    """If pool_contract is None, swap tools fail cleanly without crashing."""
    dispatcher = ToolDispatcher(w3=Web3(), usdt_contract=None, wallets={})
    zero = "0x" + "0" * 40

    # Each swap tool gets only its own kwargs — dispatcher passes them through
    # as **kwargs, so spurious keys would raise TypeError before the pool check.
    cases = [
        ("get_swap_quote", {"from_asset": "ETH", "amount": 1.0}),
        ("swap_eth_for_usdt", {"from_address": zero, "eth_amount": 1.0}),
        ("swap_usdt_for_eth", {"from_address": zero, "usdt_amount": 1.0}),
    ]
    for tool, args in cases:
        result = dispatcher.dispatch(tool, args)
        assert result.is_error, f"{tool}: expected error, got {result.output}"
        assert "pool" in result.error.lower(), f"{tool}: {result.error}"


@needs_foundry
def test_smurf_split_small_round_trip():
    """End-to-end at small scale (50 burners): sums exact, balances match."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        # Mint 10,000 USDT to alice
        _send(w3, usdt.functions.mint(deployer, 10_000 * 10**6),
              deployer, deployer_key, gas=200_000)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt, wallets={deployer: deployer_key},
        )
        result = dispatcher.dispatch("smurf_split", {
            "from_address": deployer,
            "total_usdt": 10_000.0,
            "num_wallets": 50,
            "max_per_wallet": 999.999,
            "seed": 123,
        })
        assert not result.is_error, result.error
        assert result.output["wallets_created"] == 50
        assert result.output["successful_transfers"] == 50
        assert result.output["failed_transfers"] == 0
        assert result.output["total_distributed_usdt"] == 10_000.0
        # Alice fully drained
        assert result.output["from_address_remaining_usdt"] == 0.0
        # Per-burner ceiling enforced (sample check)
        for entry in result.output["sample_recipients"]:
            assert 0 <= entry["amount_usdt"] <= 999.999
        # Gas was paid
        assert result.output["total_gas_used"] > 0
        assert result.output["total_gas_eth"] > 0
