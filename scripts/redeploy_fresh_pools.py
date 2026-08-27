"""Redeploy all 3 MockTornado pools (0.1 / 1 / 10 ETH) fresh on Sepolia.

Motivation: the accumulated leaves in the existing 1 ETH pool (27+
historical deposits) make _mixer_collect_leaves scan ~15k blocks per
withdrawal, causing 20-30 min delays under Alchemy throttling.
Redeploying gives a fresh deploy block, so future collect_leaves calls
scan from ~current_block-100 forward, cutting overhead by ~150x.

Reuses existing MiMCSponge + Verifier (stateless crypto primitives).
Overwrites deployments/sepolia.json with the new pool addresses.
Cost: ~0.006 ETH per pool × 3 = ~0.018 ETH ≈ $45.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

REPO_ROOT = Path("/home/anon/aml-thesis")
DEPLOYMENTS_JSON = REPO_ROOT / "deployments" / "sepolia.json"
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"

# All 3 denominations to redeploy fresh
POOLS_TO_DEPLOY = [
    ("MockTornado", 10**18),               # 1 ETH pool
    ("MockTornado_0.1ETH", 10**17),        # 0.1 ETH pool
    ("MockTornado_10ETH", 10 * 10**18),    # 10 ETH pool
]
MERKLE_DEPTH = 10
GAS_LIMIT = 10_000_000


def load_artifact(path):
    j = json.loads(path.read_text())
    return j["abi"], j["bytecode"]["object"]


def send_tx(w3, call, deployer, key, *, value=0, gas=None, gas_price_boost=1.5, timeout=180):
    base = w3.eth.gas_price
    tx = call.build_transaction({
        "from": deployer,
        "nonce": w3.eth.get_transaction_count(deployer, "pending"),
        "gas": gas or 500_000,
        "gasPrice": int(base * gas_price_boost),
        "value": value,
        "chainId": 11155111,
    })
    signed = w3.eth.account.sign_transaction(tx, key)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    print(f"    tx {tx_hash.hex()[:20]}...  waiting up to {timeout}s...")
    return w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout, poll_latency=1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(REPO_ROOT / ".env.sepolia", override=True)
    rpc = os.environ["SEPOLIA_RPC_URL"]
    key = os.environ["SEPOLIA_DEPLOYER_PRIVATE_KEY"]
    deployer = Account.from_key(key).address
    w3 = Web3(Web3.HTTPProvider(rpc))

    deployments = json.loads(DEPLOYMENTS_JSON.read_text())
    verifier_addr = deployments["contracts"]["Verifier"]
    mimc_addr = deployments["contracts"]["MiMCSponge"]
    old_pool_addrs = {name: deployments["contracts"].get(name) for name, _ in POOLS_TO_DEPLOY}

    tornado_abi, tornado_bytecode = load_artifact(TORNADO_ARTIFACT)
    balance = w3.eth.get_balance(deployer) / 1e18

    print(f"=== FRESH REDEPLOY of all 3 mixer pools ===")
    print(f"Deployer:            {deployer}")
    print(f"Deployer balance:    {balance:.4f} ETH")
    print(f"Reusing MiMCSponge:  {mimc_addr}")
    print(f"Reusing Verifier:    {verifier_addr}")
    print(f"Current block:       {w3.eth.block_number:,}")
    print(f"Old pool addresses (will be ORPHANED — any note in them stays trapped):")
    for name, denom in POOLS_TO_DEPLOY:
        print(f"  {name:20s} {old_pool_addrs.get(name)}  (denom {denom/1e18} ETH)")
    print()
    if args.dry_run:
        print("(dry-run — nothing sent)")
        return

    if balance < 0.05:
        raise SystemExit(f"deployer balance {balance:.4f} < 0.05 ETH safety")

    factory = w3.eth.contract(abi=tornado_abi, bytecode=tornado_bytecode)
    new_addresses = {}
    for name, denom_wei in POOLS_TO_DEPLOY:
        denom_eth = denom_wei / 1e18
        print(f"\n=== Deploying fresh {name} ({denom_eth} ETH) ===")
        r = send_tx(
            w3,
            factory.constructor(verifier_addr, mimc_addr, MERKLE_DEPTH, int(denom_wei)),
            deployer, key, gas=GAS_LIMIT, timeout=180,
        )
        if r.status != 1:
            raise SystemExit(f"deploy {name} failed: tx {r.transactionHash.hex()}")
        addr = r.contractAddress
        # Sanity: check DENOMINATION() on-chain
        pool = w3.eth.contract(address=addr, abi=tornado_abi)
        onchain = pool.functions.DENOMINATION().call()
        assert onchain == denom_wei, f"expected {denom_wei}, got {onchain}"
        print(f"  ✓ {name}: {addr}  (deploy_block: {r.blockNumber})")
        new_addresses[name] = addr
        time.sleep(2)

    # Update deployments in place — record the OLD tornado_deploy_block
    # (we no longer use it, but keep for reference) and add fresh ones
    deployments["contracts"]["MockTornado_OLD"] = old_pool_addrs["MockTornado"]
    deployments["contracts"]["MockTornado_0.1ETH_OLD"] = old_pool_addrs.get("MockTornado_0.1ETH")
    deployments["contracts"]["MockTornado_10ETH_OLD"] = old_pool_addrs.get("MockTornado_10ETH")
    for name, addr in new_addresses.items():
        deployments["contracts"][name] = addr
    # Update deploy block to the newest one (roughly = current)
    deployments["tornado_deploy_block"] = w3.eth.block_number
    deployments["multidenom_redeployed_at_utc"] = datetime.now(timezone.utc).isoformat()

    DEPLOYMENTS_JSON.write_text(json.dumps(deployments, indent=2))
    print(f"\n✓ Updated {DEPLOYMENTS_JSON}")
    print(f"Final deployer balance: {w3.eth.get_balance(deployer)/1e18:.4f} ETH")
    print(f"New tornado_deploy_block: {deployments['tornado_deploy_block']}")


if __name__ == "__main__":
    main()
