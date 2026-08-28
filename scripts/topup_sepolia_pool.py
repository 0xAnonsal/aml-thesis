"""Deploy a fresh MockUniswapV2Pool on Sepolia with deeper liquidity.

The existing MockUniswapV2Pool is bootstrapped once (bootstrap() has
`require(!bootstrapped)`) and cannot be topped up. After 20+ campaigns
its reserves drift down to ~1.5 ETH + 33k USDT (k = 45k) — a swap of
even 1 ETH now takes 10% slippage. For the planned 20 ETH campaign
this would make Integration unreliable.

This script:
  1. Deploys a fresh MockUniswapV2Pool
  2. Mints BOOTSTRAP_USDT to the deployer
  3. approve()s the new pool
  4. bootstrap(BOOTSTRAP_USDT)s it with BOOTSTRAP_ETH_WEI ETH
  5. Moves the previous MockUniswapV2Pool address to
     deprecated_contracts_archive.MockUniswapV2Pool_OLD in
     deployments/sepolia.json (same pattern used for _OLD mixer pools)
  6. Sets `MockUniswapV2Pool` to the fresh address

The ETH bootstrap is recoverable at end-of-thesis by swapping the
accumulated USDT back and reading the pool's ETH reserve into the
deployer.

Usage:
  python scripts/topup_sepolia_pool.py                # 5 ETH + 250k USDT
  python scripts/topup_sepolia_pool.py --eth 10 --usdt 500000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from eth_account import Account
from web3 import Web3

REPO = Path(__file__).resolve().parents[1]
DEPLOYMENT = REPO / "deployments" / "sepolia.json"
POOL_ARTIFACT = REPO / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"
USDT_ARTIFACT = REPO / "out" / "MockUSDT.sol" / "MockUSDT.json"


def _send(w3, fn_or_tx, sender, key, value=0, gas=3_000_000):
    """Build → sign → send → wait. Returns receipt."""
    if hasattr(fn_or_tx, "build_transaction"):
        tx = fn_or_tx.build_transaction({
            "from": sender,
            "nonce": w3.eth.get_transaction_count(sender, "pending"),
            "gas": gas,
            "gasPrice": int(w3.eth.gas_price * 2),
            "chainId": w3.eth.chain_id,
            "value": value,
        })
    else:
        tx = fn_or_tx
        tx.setdefault("nonce", w3.eth.get_transaction_count(sender, "pending"))
        tx.setdefault("gasPrice", int(w3.eth.gas_price * 2))
        tx.setdefault("chainId", w3.eth.chain_id)
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, poll_latency=1.0)
    if receipt.status != 1:
        raise RuntimeError(f"tx reverted: {tx_hash.hex()}")
    return receipt


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--eth", type=float, default=5.0,
                   help="ETH to bootstrap into the fresh pool (default 5)")
    p.add_argument("--usdt", type=float, default=250_000.0,
                   help="USDT to bootstrap (default 250 000)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    rpc = os.environ.get("SEPOLIA_RPC_URL")
    key = os.environ.get("SEPOLIA_DEPLOYER_PRIVATE_KEY")
    if not rpc or not key:
        print("ERR: SEPOLIA_RPC_URL and SEPOLIA_DEPLOYER_PRIVATE_KEY required",
              file=sys.stderr)
        return 1

    w3 = Web3(Web3.HTTPProvider(rpc))
    deployer = Account.from_key(key).address
    print(f"deployer: {deployer}")
    bal_eth = w3.eth.get_balance(deployer) / 1e18
    print(f"balance:  {bal_eth:.4f} ETH")

    if bal_eth < args.eth + 0.1:
        print(f"ERR: need at least {args.eth + 0.1:.2f} ETH; have {bal_eth:.4f}",
              file=sys.stderr)
        return 1

    depl = json.load(open(DEPLOYMENT))
    old_pool_addr = depl["contracts"].get("MockUniswapV2Pool")
    usdt_addr = Web3.to_checksum_address(depl["contracts"]["MockUSDT"])
    print(f"existing pool (to archive): {old_pool_addr}")
    print(f"USDT: {usdt_addr}")

    if args.dry_run:
        print("--dry-run: would deploy + bootstrap "
              f"{args.eth} ETH + {args.usdt} USDT")
        return 0

    pool_json = json.load(open(POOL_ARTIFACT))
    usdt_json = json.load(open(USDT_ARTIFACT))
    pool_bytecode = pool_json["bytecode"]["object"]
    pool_abi = pool_json["abi"]
    usdt_abi = usdt_json["abi"]

    # 1. Deploy fresh pool
    print("\n=== Deploying MockUniswapV2Pool ===")
    Pool = w3.eth.contract(abi=pool_abi, bytecode=pool_bytecode)
    receipt = _send(w3, Pool.constructor(usdt_addr), deployer, key, gas=3_000_000)
    new_pool_addr = receipt.contractAddress
    print(f"  new pool: {new_pool_addr}")
    new_pool = w3.eth.contract(address=new_pool_addr, abi=pool_abi)

    # 2. Mint USDT
    print("\n=== Mint + approve USDT ===")
    usdt = w3.eth.contract(address=usdt_addr, abi=usdt_abi)
    usdt_amount = int(args.usdt * 10**6)
    _send(w3, usdt.functions.mint(deployer, usdt_amount), deployer, key, gas=200_000)
    print(f"  minted {args.usdt} USDT to deployer")
    _send(w3, usdt.functions.approve(new_pool_addr, usdt_amount), deployer, key, gas=200_000)
    print("  approved new pool as USDT spender")

    # 3. Bootstrap
    print(f"\n=== Bootstrap pool ({args.eth} ETH + {args.usdt} USDT) ===")
    eth_wei = int(args.eth * 10**18)
    _send(w3, new_pool.functions.bootstrap(usdt_amount),
          deployer, key, value=eth_wei, gas=500_000)
    r_eth, r_usdt = new_pool.functions.getReserves().call()
    print(f"  reserves: {r_eth / 1e18} ETH / {r_usdt / 1e6} USDT")
    print(f"  k = {(r_eth / 1e18) * (r_usdt / 1e6):,.0f}")

    # 4. Update deployment JSON
    print(f"\n=== Update {DEPLOYMENT} ===")
    depl.setdefault("deprecated_contracts_archive", {})
    if old_pool_addr:
        depl["deprecated_contracts_archive"]["MockUniswapV2Pool_OLD"] = old_pool_addr
        print(f"  archived old pool: {old_pool_addr}")
    depl["contracts"]["MockUniswapV2Pool"] = new_pool_addr
    depl["pool_deploy_block"] = int(w3.eth.block_number)
    depl["pool_bootstrap_eth"] = args.eth
    depl["pool_bootstrap_usdt"] = args.usdt
    depl["pool_topup_ts"] = int(time.time())
    with open(DEPLOYMENT, "w") as f:
        json.dump(depl, f, indent=2)
    print(f"  new active pool: {new_pool_addr}")

    # 5. Slippage estimate for the planned 20 ETH campaign
    print("\n=== Slippage estimate for 20 ETH campaign ===")
    for eth_in in [1.0, 2.0, 5.0, 10.0]:
        # constant-product with 0.3% fee
        amt_in_fee = eth_in * 0.997
        expected = amt_in_fee * (r_usdt / 1e6) / ((r_eth / 1e18) + amt_in_fee)
        spot = (r_usdt / 1e6) / (r_eth / 1e18) * eth_in
        slippage_pct = 100.0 * (1 - expected / spot)
        print(f"  swap {eth_in:.1f} ETH → {expected:,.2f} USDT "
              f"(spot: {spot:,.2f}, slippage {slippage_pct:.1f}%)")

    print(f"\nDeployer balance AFTER: "
          f"{w3.eth.get_balance(deployer) / 1e18:.4f} ETH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
