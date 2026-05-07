"""Boot Anvil, compile contracts via Foundry, deploy and bootstrap mocks.

Sanity check that proves the full toolchain (Foundry + Anvil + web3.py) works
end-to-end. Anvil tears down on exit; deployments are ephemeral by design.

Currently deploys:
    - MockUSDT (ERC-20, 6 decimals)
    - MockUniswapV2Pool (ETH/USDT constant-product AMM, bootstrapped with
      500 ETH + 1,000,000 USDT)
    - MockTornado (1-ETH-denomination mixer)

More mocks land as their PRs merge: MockBridge.

Prereqs:
    - Foundry installed (curl -L https://foundry.paradigm.xyz | bash; foundryup)
    - aml package installed in editable mode (pip install -e .)

Usage:
    python scripts/deploy_eth_mocks.py
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from web3 import Web3

from aml.chains import AnvilNode

REPO_ROOT = Path(__file__).resolve().parents[1]
USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"
POOL_ARTIFACT = REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"

BOOTSTRAP_USDT = 1_000_000 * 10**6
BOOTSTRAP_ETH_WEI = 500 * 10**18
TORNADO_DENOMINATION_WEI = 10**18  # 1 ETH


def _raw_tx(signed) -> bytes:
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes attribute")
    return raw


def _send(w3, fn, sender: str, key: str, gas: int = 2_000_000, value: int = 0):
    tx = fn.build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
        "value": value,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    return w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(_raw_tx(signed)))


def ensure_compiled() -> None:
    if all(p.exists() for p in [USDT_ARTIFACT, POOL_ARTIFACT, TORNADO_ARTIFACT]):
        return
    print("Compiling contracts (forge build)...")
    subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)


def load_artifact(path: Path) -> tuple[list, str]:
    with path.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


def main():
    ensure_compiled()
    usdt_abi, usdt_bytecode = load_artifact(USDT_ARTIFACT)
    pool_abi, pool_bytecode = load_artifact(POOL_ARTIFACT)
    tornado_abi, tornado_bytecode = load_artifact(TORNADO_ARTIFACT)

    with AnvilNode() as node:
        print(f"Anvil: {node.rpc_url}  (chain_id={node.chain_id})\n")
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, key = node.accounts[0], node.private_keys[0]

        # --- USDT ---
        usdt_factory = w3.eth.contract(abi=usdt_abi, bytecode=usdt_bytecode)
        usdt = w3.eth.contract(
            address=_send(w3, usdt_factory.constructor(), deployer, key).contractAddress,
            abi=usdt_abi,
        )
        print(f"MockUSDT:           {usdt.address}")

        # --- Uniswap-style pool ---
        pool_factory = w3.eth.contract(abi=pool_abi, bytecode=pool_bytecode)
        pool = w3.eth.contract(
            address=_send(w3, pool_factory.constructor(usdt.address), deployer, key).contractAddress,
            abi=pool_abi,
        )
        print(f"MockUniswapV2Pool:  {pool.address}")

        _send(w3, usdt.functions.mint(deployer, BOOTSTRAP_USDT), deployer, key, gas=200_000)
        _send(w3, usdt.functions.approve(pool.address, BOOTSTRAP_USDT), deployer, key, gas=200_000)
        _send(w3, pool.functions.bootstrap(BOOTSTRAP_USDT), deployer, key, value=BOOTSTRAP_ETH_WEI)
        eth_r, usdt_r = pool.functions.getReserves().call()
        print(
            f"  pool reserves:      {eth_r / 10**18:.2f} ETH / {usdt_r / 10**6:,.2f} USDT"
            f" (spot: 1 ETH = {(usdt_r / 10**6) / (eth_r / 10**18):,.2f} USDT)"
        )

        # --- Tornado mixer ---
        tornado_factory = w3.eth.contract(abi=tornado_abi, bytecode=tornado_bytecode)
        tornado = w3.eth.contract(
            address=_send(w3, tornado_factory.constructor(), deployer, key).contractAddress,
            abi=tornado_abi,
        )
        print(f"MockTornado:        {tornado.address}")
        print(f"  denomination:       {tornado.functions.DENOMINATION().call() / 10**18:.0f} ETH")

        # Sanity: alice deposits, charlie withdraws — laundering primitive
        alice, alice_key = node.accounts[1], node.private_keys[1]
        charlie = node.accounts[2]
        secret = os.urandom(32)
        nullifier = os.urandom(32)
        commitment = Web3.keccak(secret + nullifier)

        _send(w3, tornado.functions.deposit(commitment), alice, alice_key,
              value=TORNADO_DENOMINATION_WEI)
        charlie_before = w3.eth.get_balance(charlie)
        _send(w3, tornado.functions.withdraw(secret, nullifier, charlie), alice, alice_key)
        charlie_delta = w3.eth.get_balance(charlie) - charlie_before
        print(
            f"  sanity laundering:  alice deposited 1 ETH, charlie received "
            f"{charlie_delta / 10**18:.4f} ETH (anon set: "
            f"{tornado.functions.depositCount().call()})"
        )

        print("\nDeployment OK. Anvil tears down when this script exits.")


if __name__ == "__main__":
    main()
