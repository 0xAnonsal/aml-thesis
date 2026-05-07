"""Boot Anvil, compile contracts via Foundry, deploy and bootstrap mocks.

Sanity check that proves the full toolchain (Foundry + Anvil + web3.py) works
end-to-end. Anvil tears down on exit; deployments are ephemeral by design.

Currently deploys:
    - MockUSDT (ERC-20, 6 decimals)
    - MockUniswapV2Pool (ETH/USDT constant-product AMM, bootstrapped with
      500 ETH + 1,000,000 USDT)

More mocks land as their PRs merge: MockTornado, MockBridge.

Prereqs:
    - Foundry installed (curl -L https://foundry.paradigm.xyz | bash; foundryup)
    - aml package installed in editable mode (pip install -e .)

Usage:
    python scripts/deploy_eth_mocks.py
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from web3 import Web3

from aml.chains import AnvilNode

REPO_ROOT = Path(__file__).resolve().parents[1]
USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"
POOL_ARTIFACT = REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"

# Bootstrap parameters — picks a clean ETH price ~$2000 (500 ETH x 1M USDT)
BOOTSTRAP_USDT = 1_000_000 * 10**6
BOOTSTRAP_ETH_WEI = 500 * 10**18


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
    if USDT_ARTIFACT.exists() and POOL_ARTIFACT.exists():
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

    with AnvilNode() as node:
        print(f"Anvil: {node.rpc_url}  (chain_id={node.chain_id})\n")
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, key = node.accounts[0], node.private_keys[0]

        # 1. Deploy MockUSDT
        usdt_factory = w3.eth.contract(abi=usdt_abi, bytecode=usdt_bytecode)
        receipt = _send(w3, usdt_factory.constructor(), deployer, key)
        usdt = w3.eth.contract(address=receipt.contractAddress, abi=usdt_abi)
        print(f"MockUSDT:           {usdt.address}")

        # 2. Deploy MockUniswapV2Pool(usdt)
        pool_factory = w3.eth.contract(abi=pool_abi, bytecode=pool_bytecode)
        receipt = _send(w3, pool_factory.constructor(usdt.address), deployer, key)
        pool = w3.eth.contract(address=receipt.contractAddress, abi=pool_abi)
        print(f"MockUniswapV2Pool:  {pool.address}")

        # 3. Mint USDT to deployer + approve pool + bootstrap
        _send(w3, usdt.functions.mint(deployer, BOOTSTRAP_USDT), deployer, key, gas=200_000)
        _send(w3, usdt.functions.approve(pool.address, BOOTSTRAP_USDT), deployer, key, gas=200_000)
        _send(w3, pool.functions.bootstrap(BOOTSTRAP_USDT), deployer, key, value=BOOTSTRAP_ETH_WEI)

        eth_r, usdt_r = pool.functions.getReserves().call()
        eth_per_usdt = (eth_r / 10**18) / (usdt_r / 10**6)
        print(
            f"\nPool bootstrapped:"
            f"\n  reserveETH:   {eth_r / 10**18:.2f} ETH"
            f"\n  reserveUSDT:  {usdt_r / 10**6:,.2f} USDT"
            f"\n  spot price:   1 ETH = {1 / eth_per_usdt:,.2f} USDT"
        )

        # 4. Sanity swap: alice swaps 1 ETH for USDT
        alice, alice_key = node.accounts[1], node.private_keys[1]
        one_eth = 10**18
        expected = pool.functions.getAmountOut(one_eth, eth_r, usdt_r).call()
        _send(w3, pool.functions.swapETHForUSDT(0), alice, alice_key, value=one_eth)
        alice_usdt = usdt.functions.balanceOf(alice).call()
        print(
            f"\nSanity swap (alice 1 ETH -> USDT):"
            f"\n  quoted out:   {expected / 10**6:,.4f} USDT"
            f"\n  alice USDT:   {alice_usdt / 10**6:,.4f}"
        )
        print("\nDeployment OK. Anvil tears down when this script exits.")


if __name__ == "__main__":
    main()
