"""ETH-side chain deploy + tx-sending helpers — shared by attackers,
detectors, and tests.

Centralises the small boilerplate of pushing transactions on Anvil and
deploying the four mock contracts the project uses: MockUSDT,
MockUniswapV2Pool, and MockTornado (which also pulls in MiMC + the
Groth16 Verifier). Pulled out of attackers/run_campaign and the test
helpers in Week 7.2 once a third caller (detectors/run_benign) needed
the same code — three copies was the trigger to refactor.

All functions return the live contract handle (or receipt) so callers
can immediately drive on-chain state. `forge build` is invoked
on-the-fly when an artifact JSON is missing.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from aml.chains.mimc import deploy_mimc


REPO_ROOT = Path(__file__).resolve().parents[3]
USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"
POOL_ARTIFACT = REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"
VERIFIER_ARTIFACT = REPO_ROOT / "out" / "Verifier.sol" / "Groth16Verifier.json"

# Pool bootstrap: 5000 ETH + 10M USDT → spot price 1 ETH = 2000 USDT.
# Bumped 10× from 500/1M on 2026-08-14 to reduce swap slippage on typical
# laundering volumes (8-10 ETH swaps): slippage 1.86% → 0.46%. Matches a
# medium-liquidity V2 or long-tail V3 pool on mainnet (~$20M TVL).
POOL_BOOTSTRAP_ETH_WEI = 5_000 * 10**18
POOL_BOOTSTRAP_USDT_BASE = 10_000_000 * 10**6

# Depth-10 Tornado tree (1024-leaf capacity); matches what was deployed
# in week 4 and what circuits/withdraw.circom was compiled against.
MERKLE_DEPTH = 10


def raw_tx(signed) -> bytes:
    """web3.py v6 (rawTransaction) vs v7 (raw_transaction) compat."""
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes attribute")
    return raw


def send_tx(w3, fn, sender, key, *, gas: int = 4_000_000, value: int = 0):
    """Build + sign + submit a contract-call tx; wait for receipt."""
    tx = fn.build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
        "value": value,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    tx_hash = w3.eth.send_raw_transaction(raw_tx(signed))
    return w3.eth.wait_for_transaction_receipt(tx_hash)


def load_artifact(path: Path) -> tuple[list, str]:
    """Load a Foundry artifact (abi + bytecode); run `forge build` if absent."""
    if not path.exists():
        subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)
    with path.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


def deploy_usdt(w3, deployer: str, deployer_key: str) -> Any:
    """Deploy MockUSDT; return the contract handle."""
    abi, bytecode = load_artifact(USDT_ARTIFACT)
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    receipt = send_tx(w3, factory.constructor(), deployer, deployer_key)
    return w3.eth.contract(address=receipt.contractAddress, abi=abi)


def deploy_pool(
    w3, deployer: str, deployer_key: str, usdt: Any, *,
    bootstrap_eth_wei: int = POOL_BOOTSTRAP_ETH_WEI,
    bootstrap_usdt_base: int = POOL_BOOTSTRAP_USDT_BASE,
) -> Any:
    """Deploy MockUniswapV2Pool, mint+approve+bootstrap. Returns live pool.

    Default bootstrap sets spot at 1 ETH = 2000 USDT (500 ETH / 1M USDT
    reserves). Override via the bootstrap_* kwargs if a different spot
    price is needed.
    """
    abi, bytecode = load_artifact(POOL_ARTIFACT)
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    receipt = send_tx(w3, factory.constructor(usdt.address), deployer, deployer_key)
    pool = w3.eth.contract(address=receipt.contractAddress, abi=abi)
    send_tx(w3, usdt.functions.mint(deployer, bootstrap_usdt_base),
            deployer, deployer_key, gas=200_000)
    send_tx(w3, usdt.functions.approve(pool.address, bootstrap_usdt_base),
            deployer, deployer_key, gas=200_000)
    send_tx(w3, pool.functions.bootstrap(bootstrap_usdt_base),
            deployer, deployer_key, value=bootstrap_eth_wei)
    return pool


def deploy_tornado(
    w3, deployer: str, deployer_key: str, *, depth: int = MERKLE_DEPTH,
) -> Any:
    """Deploy MiMC + Verifier + MockTornado; return the tornado handle.

    The mixer's denomination is hard-wired in MockTornado.sol (1 ETH).
    `depth` must match circuits/withdraw.circom's `Withdraw(N)` parameter
    or proofs will fail to verify on-chain.
    """
    mimc = deploy_mimc(w3, deployer, deployer_key)

    verifier_abi, verifier_bytecode = load_artifact(VERIFIER_ARTIFACT)
    v_factory = w3.eth.contract(abi=verifier_abi, bytecode=verifier_bytecode)
    verifier_addr = send_tx(
        w3, v_factory.constructor(), deployer, deployer_key,
    ).contractAddress

    tornado_abi, tornado_bytecode = load_artifact(TORNADO_ARTIFACT)
    t_factory = w3.eth.contract(abi=tornado_abi, bytecode=tornado_bytecode)
    tornado_addr = send_tx(
        w3, t_factory.constructor(verifier_addr, mimc.address, depth),
        deployer, deployer_key, gas=10_000_000,
    ).contractAddress
    return w3.eth.contract(address=tornado_addr, abi=tornado_abi)
