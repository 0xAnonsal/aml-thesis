"""Boot Anvil, compile contracts via Foundry, deploy MockUSDT.

This is a sanity check — proves the full toolchain (Foundry + Anvil + web3.py)
works end-to-end before we add more contracts. Anvil tears down on exit; the
deployment is ephemeral by design.

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
ARTIFACT_PATH = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"


def _raw_tx(signed) -> bytes:
    """web3.py v6 (rawTransaction) vs v7 (raw_transaction) attr-name compat."""
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes attribute")
    return raw


def ensure_compiled() -> None:
    if ARTIFACT_PATH.exists():
        return
    print("Compiling contracts (forge build)...")
    subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)


def load_artifact() -> tuple[list, str]:
    with ARTIFACT_PATH.open() as f:
        artifact = json.load(f)
    return artifact["abi"], artifact["bytecode"]["object"]


def main():
    ensure_compiled()
    abi, bytecode = load_artifact()

    with AnvilNode() as node:
        print(f"Anvil:    {node.rpc_url}  (chain_id={node.chain_id})")
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer = node.accounts[0]
        deployer_key = node.private_keys[0]

        factory = w3.eth.contract(abi=abi, bytecode=bytecode)
        tx = factory.constructor().build_transaction({
            "from": deployer,
            "nonce": w3.eth.get_transaction_count(deployer),
            "gas": 1_500_000,
            "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(tx, private_key=deployer_key)
        tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        usdt_address = receipt.contractAddress

        usdt = w3.eth.contract(address=usdt_address, abi=abi)
        print(f"MockUSDT: {usdt_address}")
        print(f"  name:        {usdt.functions.name().call()!r}")
        print(f"  symbol:      {usdt.functions.symbol().call()!r}")
        print(f"  decimals:    {usdt.functions.decimals().call()}")
        print(f"  totalSupply: {usdt.functions.totalSupply().call()}")
        print(f"  deployer:    {deployer}  (balance: {w3.eth.get_balance(deployer) / 10**18:.2f} ETH)")
        print("\nDeployment OK. Anvil tears down when this script exits.")


if __name__ == "__main__":
    main()
