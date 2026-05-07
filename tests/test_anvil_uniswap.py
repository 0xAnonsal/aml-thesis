"""Integration tests: MockUniswapV2Pool on Anvil.

Requires Foundry (anvil + forge) on PATH. Tests skip otherwise.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError

from aml.chains import AnvilNode

REPO_ROOT = Path(__file__).resolve().parents[1]
USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"
POOL_ARTIFACT = REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"

BOOTSTRAP_USDT = 1_000_000 * 10**6
BOOTSTRAP_ETH_WEI = 500 * 10**18

needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH; run `foundryup`",
)


def _raw_tx(signed):
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes attribute")
    return raw


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


def _build_artifact(path: Path) -> tuple[list, str]:
    if not path.exists():
        subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)
    with path.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


@pytest.fixture(scope="module")
def usdt_artifact():
    return _build_artifact(USDT_ARTIFACT)


@pytest.fixture(scope="module")
def pool_artifact():
    return _build_artifact(POOL_ARTIFACT)


def _deploy_usdt(w3, abi, bytecode, deployer, key):
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    r = _send(w3, factory.constructor(), deployer, key)
    return w3.eth.contract(address=r.contractAddress, abi=abi)


def _deploy_pool(w3, abi, bytecode, deployer, key, usdt_addr):
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    r = _send(w3, factory.constructor(usdt_addr), deployer, key)
    return w3.eth.contract(address=r.contractAddress, abi=abi)


def _setup(node, usdt_artifact, pool_artifact):
    """Boot Anvil-attached chain, deploy USDT + pool, bootstrap, return state."""
    w3 = Web3(Web3.HTTPProvider(node.rpc_url))
    deployer, deployer_key = node.accounts[0], node.private_keys[0]
    alice, alice_key = node.accounts[1], node.private_keys[1]
    usdt = _deploy_usdt(w3, *usdt_artifact, deployer, deployer_key)
    pool = _deploy_pool(w3, *pool_artifact, deployer, deployer_key, usdt.address)

    _send(w3, usdt.functions.mint(deployer, BOOTSTRAP_USDT), deployer, deployer_key, gas=200_000)
    _send(w3, usdt.functions.approve(pool.address, BOOTSTRAP_USDT), deployer, deployer_key, gas=200_000)
    _send(w3, pool.functions.bootstrap(BOOTSTRAP_USDT), deployer, deployer_key,
          value=BOOTSTRAP_ETH_WEI)
    return w3, deployer, deployer_key, alice, alice_key, usdt, pool


@needs_foundry
def test_bootstrap_sets_reserves(usdt_artifact, pool_artifact):
    with AnvilNode() as node:
        w3, _, _, _, _, _, pool = _setup(node, usdt_artifact, pool_artifact)
        eth_r, usdt_r = pool.functions.getReserves().call()
        assert eth_r == BOOTSTRAP_ETH_WEI
        assert usdt_r == BOOTSTRAP_USDT
        assert pool.functions.bootstrapped().call() is True


@needs_foundry
def test_double_bootstrap_reverts(usdt_artifact, pool_artifact):
    with AnvilNode() as node:
        w3, deployer, key, _, _, _, pool = _setup(node, usdt_artifact, pool_artifact)
        with pytest.raises(ContractLogicError, match="already bootstrapped"):
            pool.functions.bootstrap(1).call({"from": deployer, "value": 1})


@needs_foundry
def test_swap_eth_for_usdt(usdt_artifact, pool_artifact):
    """500 ETH + 1M USDT pool: 1 ETH -> ~1992 USDT (after 0.3% fee + slippage)."""
    with AnvilNode() as node:
        w3, _, _, alice, alice_key, usdt, pool = _setup(node, usdt_artifact, pool_artifact)

        one_eth = 10**18
        expected = pool.functions.getAmountOut(one_eth, BOOTSTRAP_ETH_WEI, BOOTSTRAP_USDT).call()
        # Sanity: in the 1900-2000 USDT range
        assert 1_900 * 10**6 < expected < 2_000 * 10**6, f"got {expected}"

        before = usdt.functions.balanceOf(alice).call()
        _send(w3, pool.functions.swapETHForUSDT(0), alice, alice_key, value=one_eth)
        after = usdt.functions.balanceOf(alice).call()
        assert after - before == expected

        eth_r, usdt_r = pool.functions.getReserves().call()
        assert eth_r == BOOTSTRAP_ETH_WEI + one_eth
        assert usdt_r == BOOTSTRAP_USDT - expected


@needs_foundry
def test_swap_usdt_for_eth(usdt_artifact, pool_artifact):
    with AnvilNode() as node:
        w3, _, _, alice, alice_key, usdt, pool = _setup(node, usdt_artifact, pool_artifact)

        alice_usdt_in = 5_000 * 10**6
        _send(w3, usdt.functions.mint(alice, alice_usdt_in), alice, alice_key, gas=200_000)
        _send(w3, usdt.functions.approve(pool.address, alice_usdt_in), alice, alice_key, gas=200_000)

        eth_before = w3.eth.get_balance(alice)
        expected = pool.functions.getAmountOut(
            alice_usdt_in, BOOTSTRAP_USDT, BOOTSTRAP_ETH_WEI
        ).call()

        receipt = _send(w3, pool.functions.swapUSDTForETH(alice_usdt_in, 0), alice, alice_key)
        eth_after = w3.eth.get_balance(alice)
        gas_cost = receipt.gasUsed * receipt.effectiveGasPrice
        # Net ETH delta = expected - gas
        assert eth_after - eth_before == expected - gas_cost

        eth_r, usdt_r = pool.functions.getReserves().call()
        assert eth_r == BOOTSTRAP_ETH_WEI - expected
        assert usdt_r == BOOTSTRAP_USDT + alice_usdt_in


@needs_foundry
def test_constant_product_grows_with_fees(usdt_artifact, pool_artifact):
    """k = reserveETH * reserveUSDT must grow after each swap (fee accrues)."""
    with AnvilNode() as node:
        w3, _, _, alice, alice_key, _, pool = _setup(node, usdt_artifact, pool_artifact)
        k_before = BOOTSTRAP_ETH_WEI * BOOTSTRAP_USDT
        _send(w3, pool.functions.swapETHForUSDT(0), alice, alice_key, value=10 * 10**18)
        eth_r, usdt_r = pool.functions.getReserves().call()
        k_after = eth_r * usdt_r
        assert k_after > k_before, "constant product should grow as fees accrue"


@needs_foundry
def test_slippage_protection_reverts(usdt_artifact, pool_artifact):
    """min_out higher than the pool will pay -> revert with 'slippage'."""
    with AnvilNode() as node:
        w3, _, _, alice, _, _, pool = _setup(node, usdt_artifact, pool_artifact)
        unrealistic_min = 10_000 * 10**6  # 10000 USDT for 1 ETH — pool quotes ~2000
        with pytest.raises(ContractLogicError, match="slippage"):
            pool.functions.swapETHForUSDT(unrealistic_min).call({
                "from": alice,
                "value": 10**18,
            })
