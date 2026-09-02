"""Drain ETH from the ACTIVE MockUniswapV2Pool by minting USDT + swapping.

Rationale: bootstrap() is one-shot per pool. Once a pool is deployed
and bootstrapped, the only way to extract ETH is via swap. MockUSDT
has a permissionless mint(), so we can mint arbitrary USDT and swap
it into the pool to drain most of its ETH reserve.

Uses constant-product math (x·y = k, 0.3% fee) to compute the USDT
amount needed to hit a target residual ETH in the pool. A residual
of 0.1 ETH means we recover 99% of the current reserve.

Usage:
  python scripts/drain_sepolia_pool.py                # target 0.1 ETH residual
  python scripts/drain_sepolia_pool.py --residual 0.05  # more aggressive
  python scripts/drain_sepolia_pool.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from eth_account import Account
from web3 import Web3

REPO = Path(__file__).resolve().parents[1]
DEPLOYMENT = REPO / "deployments" / "sepolia.json"
POOL_ARTIFACT = REPO / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"
USDT_ARTIFACT = REPO / "out" / "MockUSDT.sol" / "MockUSDT.json"


def _send(w3, fn, sender, key, value=0, gas=500_000):
    if hasattr(fn, "build_transaction"):
        tx = fn.build_transaction({
            "from": sender,
            "nonce": w3.eth.get_transaction_count(sender, "pending"),
            "gas": gas,
            "gasPrice": int(w3.eth.gas_price * 2),
            "chainId": w3.eth.chain_id,
            "value": value,
        })
    else:
        tx = fn
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120, poll_latency=1.0)
    if receipt.status != 1:
        raise RuntimeError(f"tx reverted: {tx_hash.hex()}")
    return receipt


def _usdt_in_for_eth_out(reserve_eth_wei: int, reserve_usdt_base: int,
                        eth_out_wei: int) -> int:
    """Constant-product Uniswap V2 math: how much USDT do we need to
    input to withdraw eth_out_wei from the pool, with 0.3% fee."""
    # y * x = k. After swap: (y - eth_out) * (x + usdt_in * fee) = k
    # usdt_in * fee = k / (y - eth_out) - x = (y * x - (y - eth_out) * x) / (y - eth_out)
    #               = (eth_out * x) / (y - eth_out)
    # usdt_in = ceil( eth_out * x / (y - eth_out) / fee )
    if eth_out_wei >= reserve_eth_wei:
        raise ValueError("cannot withdraw ≥ full ETH reserve")
    numerator = eth_out_wei * reserve_usdt_base * 1000
    denominator = (reserve_eth_wei - eth_out_wei) * 997
    return numerator // denominator + 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--residual", type=float, default=0.1,
                   help="Target ETH residual in pool after drain (default 0.1)")
    p.add_argument("--slippage-tol", type=float, default=0.5,
                   help="Extra slippage tolerance factor (default 0.5 = mint 50%% more USDT than the naive math to survive concurrent txs)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    rpc = os.environ["SEPOLIA_RPC_URL"]
    key = os.environ["SEPOLIA_DEPLOYER_PRIVATE_KEY"]
    w3 = Web3(Web3.HTTPProvider(rpc))
    deployer = Account.from_key(key).address
    print(f"deployer:    {deployer}")
    print(f"balance:     {float(w3.from_wei(w3.eth.get_balance(deployer), 'ether')):.4f} ETH")

    depl = json.load(open(DEPLOYMENT))
    pool_addr = Web3.to_checksum_address(depl["contracts"]["MockUniswapV2Pool"])
    usdt_addr = Web3.to_checksum_address(depl["contracts"]["MockUSDT"])
    pool_abi = json.load(open(POOL_ARTIFACT))["abi"]
    usdt_abi = json.load(open(USDT_ARTIFACT))["abi"]
    pool = w3.eth.contract(address=pool_addr, abi=pool_abi)
    usdt = w3.eth.contract(address=usdt_addr, abi=usdt_abi)

    r_eth, r_usdt = pool.functions.getReserves().call()
    print(f"pool addr:   {pool_addr}")
    print(f"reserves:    {r_eth/1e18:.6f} ETH / {r_usdt/1e6:.2f} USDT")
    print(f"spot rate:   {(r_usdt/1e6)/(r_eth/1e18):.2f} USDT/ETH")

    residual_wei = int(args.residual * 10**18)
    if residual_wei >= r_eth:
        print(f"pool already at residual (has {r_eth/1e18} ETH)")
        return 0

    eth_out_wei = r_eth - residual_wei
    usdt_needed_base = _usdt_in_for_eth_out(r_eth, r_usdt, eth_out_wei)
    usdt_needed_base = int(usdt_needed_base * (1 + args.slippage_tol))

    print(f"drain plan:  swap {usdt_needed_base/1e6:,.2f} USDT → "
          f"recover ~{eth_out_wei/1e18:.6f} ETH")
    print(f"leaves:      ~{residual_wei/1e18:.4f} ETH in pool")

    if args.dry_run:
        print("--dry-run: skipping tx")
        return 0

    # 1. Mint USDT to deployer
    print("\n=== Mint USDT ===")
    _send(w3, usdt.functions.mint(deployer, usdt_needed_base),
          deployer, key, gas=200_000)
    dep_usdt = usdt.functions.balanceOf(deployer).call()
    print(f"  deployer USDT balance now: {dep_usdt/1e6:,.2f}")

    # 2. Approve pool
    print("\n=== Approve pool as USDT spender ===")
    _send(w3, usdt.functions.approve(pool_addr, usdt_needed_base),
          deployer, key, gas=200_000)

    # 3. Swap USDT → ETH (min_out = 0 for max drain)
    print("\n=== Swap USDT → ETH ===")
    eth_before = w3.eth.get_balance(deployer)
    _send(w3, pool.functions.swapUSDTForETH(usdt_needed_base, 0),
          deployer, key, gas=500_000)
    eth_after = w3.eth.get_balance(deployer)
    eth_recovered = (eth_after - eth_before) / 1e18
    print(f"  ETH recovered: {eth_recovered:.6f}")

    r_eth2, r_usdt2 = pool.functions.getReserves().call()
    print(f"\npool reserves AFTER: {r_eth2/1e18:.6f} ETH / {r_usdt2/1e6:.2f} USDT")
    print(f"deployer AFTER: {float(w3.from_wei(w3.eth.get_balance(deployer), 'ether')):.4f} ETH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
