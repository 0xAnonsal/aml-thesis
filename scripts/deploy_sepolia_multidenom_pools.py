"""Incremental deploy of the 0.1 ETH + 10 ETH MockTornado pools on Sepolia.

Reuses the already-deployed MiMCSponge + Verifier (from deployments/sepolia.json)
— safe because both are pure cryptographic primitives with no per-pool state.
Only deploys the 2 new MockTornado contracts (~5M gas each). Updates
deployments/sepolia.json in-place with the new addresses.

Does NOT touch the existing 1 ETH pool at 0x199181...50c5 which stays live
and unchanged.

Usage: python scripts/deploy_sepolia_multidenom_pools.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENTS_JSON = REPO_ROOT / "deployments" / "sepolia.json"
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"

# Denominations to add (existing 1 ETH pool stays untouched)
NEW_DENOMS_WEI = [10**17, 10 * 10**18]   # 0.1 ETH, 10 ETH
MERKLE_DEPTH = 10
GAS_LIMIT = 10_000_000


def load_artifact(path: Path) -> tuple[list, str]:
    j = json.loads(path.read_text())
    return j["abi"], j["bytecode"]["object"]


def send_tx(w3, contract_call, deployer, key, *, value=0, gas=None,
            gas_price_boost=1.2, timeout=180):
    """Build, sign, send, wait — with proper Sepolia gas floor."""
    base_gas_price = w3.eth.gas_price
    boosted = int(base_gas_price * gas_price_boost)
    tx = contract_call.build_transaction({
        "from": deployer,
        "nonce": w3.eth.get_transaction_count(deployer),
        "gas": gas or 500_000,
        "gasPrice": boosted,
        "value": value,
        "chainId": 11155111,
    })
    signed = w3.eth.account.sign_transaction(tx, key)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    print(f"    tx {tx_hash.hex()[:20]}...  waiting up to {timeout}s...")
    return w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be deployed without sending txs")
    args = ap.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(REPO_ROOT / ".env.sepolia", override=True)
    rpc = os.environ["SEPOLIA_RPC_URL"]
    key = os.environ["SEPOLIA_DEPLOYER_PRIVATE_KEY"]
    deployer = Account.from_key(key).address

    w3 = Web3(Web3.HTTPProvider(rpc))
    assert w3.eth.chain_id == 11155111, f"wrong chain {w3.eth.chain_id}"

    deployments = json.loads(DEPLOYMENTS_JSON.read_text())
    verifier_addr = deployments["contracts"]["Verifier"]
    mimc_addr = deployments["contracts"]["MiMCSponge"]
    existing_pool_addr = deployments["contracts"]["MockTornado"]

    tornado_abi, tornado_bytecode = load_artifact(TORNADO_ARTIFACT)

    balance = w3.eth.get_balance(deployer) / 1e18
    print(f"=== Multi-denom pool deploy on Sepolia ===")
    print(f"Deployer:            {deployer}")
    print(f"Deployer balance:    {balance:.4f} ETH")
    print(f"Reusing MiMCSponge:  {mimc_addr}")
    print(f"Reusing Verifier:    {verifier_addr}")
    print(f"Existing 1 ETH pool: {existing_pool_addr}  (untouched)")
    print()
    print("New pools to deploy:")
    for d in NEW_DENOMS_WEI:
        print(f"  {d / 1e18:g} ETH")
    if args.dry_run:
        print("\n(dry-run — nothing sent)")
        return

    est_gas_per_pool = 0.02   # ~5M gas × 4 gwei
    if balance < 0.05:
        raise SystemExit(f"deployer balance {balance:.4f} < 0.05 ETH safety")

    factory = w3.eth.contract(abi=tornado_abi, bytecode=tornado_bytecode)

    new_addresses = {}
    for denom_wei in NEW_DENOMS_WEI:
        denom_eth = denom_wei / 1e18
        label = f"MockTornado_{denom_eth:g}ETH"
        print(f"\n=== Deploying {label} ===")
        r = send_tx(
            w3, factory.constructor(
                verifier_addr, mimc_addr, MERKLE_DEPTH, int(denom_wei),
            ),
            deployer, key, gas=GAS_LIMIT, timeout=180,
        )
        if r.status != 1:
            raise SystemExit(f"deploy {label} failed: tx {r.transactionHash.hex()}")
        addr = r.contractAddress
        # Sanity check: DENOMINATION correct on-chain
        pool = w3.eth.contract(address=addr, abi=tornado_abi)
        onchain = pool.functions.DENOMINATION().call()
        assert onchain == denom_wei, f"expected {denom_wei}, got {onchain}"
        print(f"  ✓ {label}: {addr}")
        print(f"    DENOMINATION = {onchain / 1e18} ETH")
        print(f"    tx block: {r.blockNumber}, gas used: {r.gasUsed:,}")
        new_addresses[label] = addr
        time.sleep(2)   # give provider a moment between deploys

    # Update deployments/sepolia.json in place
    for label, addr in new_addresses.items():
        deployments["contracts"][label] = addr
    # Record the multi-denom deploy timestamp
    from datetime import datetime, timezone
    deployments["multidenom_deployed_at_utc"] = (
        datetime.now(timezone.utc).isoformat()
    )
    DEPLOYMENTS_JSON.write_text(json.dumps(deployments, indent=2))
    print(f"\n✓ Updated {DEPLOYMENTS_JSON.relative_to(REPO_ROOT)} with new pool addresses.")
    print(f"\nFinal deployer balance: {w3.eth.get_balance(deployer)/1e18:.4f} ETH")


if __name__ == "__main__":
    main()
