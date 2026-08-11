"""Reclaim residual ETH + USDT from a Sepolia campaign back to the deployer.

Sepolia campaigns generate many wallets (burners, clean exits) that end
up holding small residual amounts of ETH and USDT. On mainnet these
would be permanently stranded once the private keys are lost; on Sepolia
the money has no dollar value, but reclaiming it preserves the deployer
budget for the next campaign run.

Reads:
  - <run-dir>/wallets_keys.json  (gitignored, contains private keys)
  - <run-dir>/addresses.json     (public addresses + contract handles)
  - deployments/sepolia.json     (MockUSDT contract for balance queries)
  - .env.sepolia                 (RPC URL — deployer key not needed)

For each wallet in wallets_keys.json (other than the deployer itself):
  1. Query ETH balance. If > estimated_gas_cost, send ETH transfer_eth
     to deployer, leaving just enough for the tx itself.
  2. Query USDT balance. If > 0, send transfer to deployer.

Uses EIP-1559 gas with 3 gwei priority (aligned with the run_sepolia_campaign
gas floor). Rate-limits between wallets to avoid overwhelming free-tier RPC.

Usage:
  python scripts/sweep_sepolia.py --run-dir results/sepolia_campaign/<run-name>
  python scripts/sweep_sepolia.py --run-dir <run-dir> --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENTS_JSON = REPO_ROOT / "deployments" / "sepolia.json"

MIN_GAS_PRICE_GWEI = 3
ETH_TRANSFER_GAS = 21_000
ERC20_TRANSFER_GAS = 65_000
INTER_WALLET_SLEEP_S = 0.5   # avoid Alchemy rate-limit

USDT_ABI_PATH = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"


def _redact_rpc(rpc: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(rpc)
    parts = [x for x in p.path.split("/") if x]
    if parts:
        parts[-1] = "<redacted>"
    return f"{p.scheme}://{p.netloc}/" + "/".join(parts)


def _raw_tx(signed):
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


def _send_eth(w3, from_addr, from_key, to_addr, value_wei, chain_id):
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or w3.eth.gas_price
    priority = w3.to_wei(MIN_GAS_PRICE_GWEI, "gwei")
    max_fee = base_fee * 2 + priority
    tx = {
        "from": from_addr, "to": to_addr, "value": value_wei,
        "nonce": w3.eth.get_transaction_count(from_addr),
        "gas": ETH_TRANSFER_GAS,
        "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority,
        "chainId": chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, private_key=from_key)
    tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return tx_hash, receipt


def _send_usdt(w3, usdt, from_addr, from_key, to_addr, amount_base, chain_id):
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or w3.eth.gas_price
    priority = w3.to_wei(MIN_GAS_PRICE_GWEI, "gwei")
    max_fee = base_fee * 2 + priority
    tx = usdt.functions.transfer(to_addr, amount_base).build_transaction({
        "from": from_addr,
        "nonce": w3.eth.get_transaction_count(from_addr),
        "gas": ERC20_TRANSFER_GAS,
        "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority,
        "chainId": chain_id,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=from_key)
    tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return tx_hash, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", required=True, type=Path,
                        help="Path to a campaign run directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report balances only, don't send txs")
    args = parser.parse_args()

    keys_path = args.run_dir / "wallets_keys.json"
    if not keys_path.exists():
        raise SystemExit(
            f"Missing {keys_path}. Only campaigns with wallets_keys.json "
            f"can be swept (feature added after 2026-08-11).")

    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(REPO_ROOT / ".env.sepolia", override=True)
    rpc = os.environ["SEPOLIA_RPC_URL"]

    wallets_data = json.loads(keys_path.read_text())
    deployer = wallets_data["deployer"]
    wallets = wallets_data["wallets"]

    if not DEPLOYMENTS_JSON.exists():
        raise SystemExit(f"Missing {DEPLOYMENTS_JSON}")
    deployment = json.loads(DEPLOYMENTS_JSON.read_text())
    usdt_addr = deployment["contracts"]["MockUSDT"]

    w3 = Web3(Web3.HTTPProvider(rpc))
    if w3.eth.chain_id != 11155111:
        raise SystemExit(f"Wrong chain: {w3.eth.chain_id}")

    usdt_abi = json.loads(USDT_ABI_PATH.read_text())["abi"]
    usdt = w3.eth.contract(address=usdt_addr, abi=usdt_abi)

    print(f"Sweep run: {args.run_dir.name}")
    print(f"RPC:       {_redact_rpc(rpc)}")
    print(f"Deployer:  {deployer}")
    print(f"Deployer balance BEFORE: {w3.eth.get_balance(deployer)/1e18:.4f} ETH")
    print(f"Deployer USDT BEFORE:    {usdt.functions.balanceOf(deployer).call()/1e6:,.2f}")
    print(f"Wallets to sweep: {len(wallets) - 1}  ({'DRY-RUN' if args.dry_run else 'REAL'})")
    print()

    total_eth_reclaimed = 0
    total_usdt_reclaimed = 0
    total_eth_stranded = 0
    n_eth_swept = 0
    n_usdt_swept = 0

    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or w3.eth.gas_price
    priority = w3.to_wei(MIN_GAS_PRICE_GWEI, "gwei")
    max_fee = base_fee * 2 + priority
    eth_gas_cost_wei = ETH_TRANSFER_GAS * max_fee
    usdt_gas_cost_wei = ERC20_TRANSFER_GAS * max_fee

    for addr, key in wallets.items():
        if addr.lower() == deployer.lower():
            continue
        eth_balance = w3.eth.get_balance(addr)
        try:
            usdt_balance = usdt.functions.balanceOf(addr).call()
        except Exception:
            usdt_balance = 0

        actions = []

        # USDT first (needs ETH for gas — so sweep USDT while wallet still has ETH)
        if usdt_balance > 0 and eth_balance > usdt_gas_cost_wei:
            actions.append(f"USDT {usdt_balance/1e6:,.2f}")
            if not args.dry_run:
                try:
                    tx_hash, _ = _send_usdt(w3, usdt, addr, key, deployer,
                                            usdt_balance, w3.eth.chain_id)
                    total_usdt_reclaimed += usdt_balance
                    n_usdt_swept += 1
                    time.sleep(INTER_WALLET_SLEEP_S)
                    eth_balance = w3.eth.get_balance(addr)   # refresh after USDT tx
                except Exception as e:
                    actions.append(f"USDT FAILED: {e}")

        # ETH — leave just enough for the transfer tx itself
        if eth_balance > eth_gas_cost_wei * 2:
            transfer_amount = eth_balance - eth_gas_cost_wei
            actions.append(f"ETH {transfer_amount/1e18:.6f}")
            if not args.dry_run:
                try:
                    tx_hash, _ = _send_eth(w3, addr, key, deployer,
                                           transfer_amount, w3.eth.chain_id)
                    total_eth_reclaimed += transfer_amount
                    n_eth_swept += 1
                    time.sleep(INTER_WALLET_SLEEP_S)
                except Exception as e:
                    actions.append(f"ETH FAILED: {e}")
        elif eth_balance > 0:
            total_eth_stranded += eth_balance

        if actions:
            print(f"  {addr}: {', '.join(actions)}")
        elif eth_balance > 0 or usdt_balance > 0:
            print(f"  {addr}: {eth_balance/1e18:.6f} ETH + "
                  f"{usdt_balance/1e6:,.2f} USDT (below sweep threshold)")

    print()
    print(f"=== Sweep summary ===")
    print(f"  ETH reclaimed:  {total_eth_reclaimed/1e18:.6f} from {n_eth_swept} wallets")
    print(f"  USDT reclaimed: {total_usdt_reclaimed/1e6:,.2f} from {n_usdt_swept} wallets")
    print(f"  ETH stranded (below gas threshold): {total_eth_stranded/1e18:.6f}")

    if not args.dry_run:
        print(f"Deployer balance AFTER: {w3.eth.get_balance(deployer)/1e18:.4f} ETH")
        print(f"Deployer USDT AFTER:    {usdt.functions.balanceOf(deployer).call()/1e6:,.2f}")
        print(f"\nOnce satisfied, delete {keys_path} to remove private keys from disk.")


if __name__ == "__main__":
    main()
