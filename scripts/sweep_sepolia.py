"""Reclaim residual ETH + USDT from a Sepolia campaign back to the deployer.

Sepolia campaigns generate many wallets (burners, clean exits) that end
up holding small residual amounts of ETH and USDT. On mainnet these
would be permanently stranded once the private keys are lost; on Sepolia
the money has no dollar value, but reclaiming it preserves the deployer
budget for the next campaign run.

Reads:
  - <run-dir>/wallets_keys.json  (gitignored, contains private keys)
  - <run-dir>/addresses.json     (public addresses + contract handles)
  - deployments/sepolia.json     (MockUSDT + MockUniswapV2Pool addresses)
  - .env.sepolia                 (RPC URL + SEPOLIA_DEPLOYER_PRIVATE_KEY)

Three phases:
  1. Per-wallet loop: for each wallet in wallets_keys.json (other than
     the deployer), query ETH+USDT balance; sweep USDT first (needs
     wallet ETH for gas), then ETH; optionally rescue stranded wallets
     that hold USDT but lack ETH for the sweep tx.
  2. Post-loop reverse swap (opt-out via --no-reverse-swap): if the
     deployer has accumulated USDT >= MIN_USDT_CONVERT_BASE (100),
     approve the pool and execute swapUSDTForETH to convert the
     stablecoin residual back to ETH via MockUniswapV2Pool. Slippage
     tolerance defaults to 2% (--reverse-swap-slippage).
  3. Final balance report.

Uses EIP-1559 gas with 3 gwei priority (aligned with the run_sepolia_campaign
gas floor). Rate-limits between wallets to avoid overwhelming free-tier RPC.

Usage:
  python scripts/sweep_sepolia.py --run-dir results/sepolia_campaign/<run-name>
  python scripts/sweep_sepolia.py --run-dir <run-dir> --dry-run
  python scripts/sweep_sepolia.py --run-dir <run-dir> --no-reverse-swap
  python scripts/sweep_sepolia.py --run-dir <run-dir> --reverse-swap-slippage 0.03
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
ERC20_APPROVE_GAS = 60_000
POOL_SWAP_GAS = 200_000       # constant-product swap w/ ERC20 transfer
INTER_WALLET_SLEEP_S = 0.5    # avoid Alchemy rate-limit
MIN_USDT_CONVERT_BASE = 100 * 10**6   # skip reverse swap below 100 USDT

USDT_ABI_PATH = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"
POOL_ABI_PATH = REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"


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


def _approve_usdt(w3, usdt, from_addr, from_key, spender_addr, amount_base, chain_id):
    """Approve `spender_addr` to spend `amount_base` USDT of `from_addr`."""
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or w3.eth.gas_price
    priority = w3.to_wei(MIN_GAS_PRICE_GWEI, "gwei")
    max_fee = base_fee * 2 + priority
    tx = usdt.functions.approve(spender_addr, amount_base).build_transaction({
        "from": from_addr,
        "nonce": w3.eth.get_transaction_count(from_addr),
        "gas": ERC20_APPROVE_GAS,
        "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority,
        "chainId": chain_id,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=from_key)
    tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return tx_hash, receipt


def _swap_usdt_for_eth(w3, pool, from_addr, from_key, usdt_in_base, min_out_wei, chain_id):
    """Execute pool.swapUSDTForETH — caller must have approved `pool` first."""
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or w3.eth.gas_price
    priority = w3.to_wei(MIN_GAS_PRICE_GWEI, "gwei")
    max_fee = base_fee * 2 + priority
    tx = pool.functions.swapUSDTForETH(usdt_in_base, min_out_wei).build_transaction({
        "from": from_addr,
        "nonce": w3.eth.get_transaction_count(from_addr),
        "gas": POOL_SWAP_GAS,
        "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority,
        "chainId": chain_id,
    })
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
    parser.add_argument("--no-rescue", action="store_true",
                        help="Do NOT top-up stranded wallets from deployer "
                             "(default: rescue is on if SEPOLIA_DEPLOYER_"
                             "PRIVATE_KEY is set)")
    parser.add_argument("--no-reverse-swap", action="store_true",
                        help="Do NOT convert deployer's accumulated USDT "
                             "back to ETH via the pool at the end "
                             "(default: reverse swap on if deployer USDT "
                             ">= 100 and SEPOLIA_DEPLOYER_PRIVATE_KEY set)")
    parser.add_argument("--reverse-swap-slippage", type=float, default=0.02,
                        help="Slippage tolerance for the reverse swap "
                             "(default 0.02 = 2%%)")
    args = parser.parse_args()

    # Two possible sources of keys (both may exist):
    #   wallets_keys.jsonl — write-through log, one line per key at creation
    #                        time (added 2026-08-20; crash-safe).
    #   wallets_keys.json  — final snapshot dumped at end-of-run (legacy;
    #                        may be missing if the process crashed).
    # We use whichever exists, preferring the JSONL when both are present
    # (guaranteed complete).
    keys_jsonl = args.run_dir / "wallets_keys.jsonl"
    keys_json = args.run_dir / "wallets_keys.json"

    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(REPO_ROOT / ".env.sepolia", override=True)
    rpc = os.environ["SEPOLIA_RPC_URL"]

    if keys_jsonl.exists():
        wallets = {}
        deployer = None
        for line in keys_jsonl.open():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            addr = row["address"]
            wallets[addr] = row["private_key"]
            if deployer is None:
                deployer = addr   # first entry is the deployer by convention
        print(f"[sweep] loaded {len(wallets)} keys from wallets_keys.jsonl "
              f"(crash-safe write-through log)")
    elif keys_json.exists():
        wallets_data = json.loads(keys_json.read_text())
        deployer = wallets_data["deployer"]
        wallets = wallets_data["wallets"]
    else:
        raise SystemExit(
            f"Neither {keys_jsonl.name} nor {keys_json.name} exists in "
            f"{args.run_dir}. Nothing to sweep."
        )

    if not DEPLOYMENTS_JSON.exists():
        raise SystemExit(f"Missing {DEPLOYMENTS_JSON}")
    deployment = json.loads(DEPLOYMENTS_JSON.read_text())
    usdt_addr = deployment["contracts"]["MockUSDT"]

    w3 = Web3(Web3.HTTPProvider(rpc))
    if w3.eth.chain_id != 11155111:
        raise SystemExit(f"Wrong chain: {w3.eth.chain_id}")

    usdt_abi = json.loads(USDT_ABI_PATH.read_text())["abi"]
    usdt = w3.eth.contract(address=usdt_addr, abi=usdt_abi)

    pool_addr = deployment["contracts"]["MockUniswapV2Pool"]
    pool_abi = json.loads(POOL_ABI_PATH.read_text())["abi"]
    pool = w3.eth.contract(address=pool_addr, abi=pool_abi)

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
    total_usdt_rescued = 0
    n_eth_swept = 0
    n_usdt_swept = 0
    n_rescued = 0

    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or w3.eth.gas_price
    priority = w3.to_wei(MIN_GAS_PRICE_GWEI, "gwei")
    max_fee = base_fee * 2 + priority
    eth_gas_cost_wei = ETH_TRANSFER_GAS * max_fee
    usdt_gas_cost_wei = ERC20_TRANSFER_GAS * max_fee

    # Optional: load deployer key so we can top-up stranded wallets
    # (USDT balance > 0 but ETH < gas cost). Without this, USDT sits
    # locked in exits that consumed all their initial 0.05 ETH seed
    # relaying transfers during the campaign. With it, we send just
    # enough ETH from deployer to unlock the sweep — costs ~0.0002 ETH
    # per rescue, recovers 10-1000× that in USDT.
    deployer_key = os.environ.get("SEPOLIA_DEPLOYER_PRIVATE_KEY")
    rescue_enabled = deployer_key is not None and not args.no_rescue
    if not rescue_enabled and not args.no_rescue:
        print("[sweep] SEPOLIA_DEPLOYER_PRIVATE_KEY not set — stranded "
              "USDT will not be rescued", flush=True)

    for addr, key in wallets.items():
        if addr.lower() == deployer.lower():
            continue
        eth_balance = w3.eth.get_balance(addr)
        try:
            usdt_balance = usdt.functions.balanceOf(addr).call()
        except Exception:
            usdt_balance = 0

        actions = []

        # Rescue: if wallet has USDT but not enough ETH, top up from
        # deployer so the USDT sweep can proceed.
        needs_rescue = (
            usdt_balance > 0
            and eth_balance <= usdt_gas_cost_wei
            and rescue_enabled
        )
        if needs_rescue:
            topup = usdt_gas_cost_wei * 3 - eth_balance   # 3× margin
            actions.append(f"RESCUE +{topup/1e18:.6f} ETH")
            if not args.dry_run:
                try:
                    _send_eth(w3, deployer, deployer_key, addr,
                              topup, w3.eth.chain_id)
                    eth_balance = w3.eth.get_balance(addr)   # refresh
                    n_rescued += 1
                    total_usdt_rescued += usdt_balance
                    time.sleep(INTER_WALLET_SLEEP_S)
                except Exception as e:
                    actions.append(f"RESCUE FAILED: {e}")

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

        # ETH — leave 3× gas cost as buffer for base_fee fluctuations
        # (previously 1× caused "insufficient funds" failures when
        # base_fee bumped between our calc and tx submission).
        if eth_balance > eth_gas_cost_wei * 4:
            transfer_amount = eth_balance - eth_gas_cost_wei * 3
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
    print(f"  Stranded wallets rescued: {n_rescued} (unlocked {total_usdt_rescued/1e6:,.2f} USDT)")
    print(f"  ETH stranded (below gas threshold): {total_eth_stranded/1e18:.6f}")

    # Reverse swap: convert deployer's accumulated USDT back to ETH
    # via the pool. Runs only if enabled, deployer key available, and
    # balance above the min threshold (100 USDT ~ 0.053 ETH at spot,
    # below which the swap gas cost dominates the recovery).
    reverse_swap_enabled = (
        deployer_key is not None
        and not args.no_reverse_swap
        and not args.dry_run
    )
    if reverse_swap_enabled:
        deployer_usdt_now = usdt.functions.balanceOf(deployer).call()
        if deployer_usdt_now >= MIN_USDT_CONVERT_BASE:
            reserve_eth, reserve_usdt = pool.functions.getReserves().call()
            expected_eth_wei = pool.functions.getAmountOut(
                deployer_usdt_now, reserve_usdt, reserve_eth
            ).call()
            min_out_wei = int(expected_eth_wei * (1.0 - args.reverse_swap_slippage))

            print()
            print(f"=== Reverse swap deployer USDT -> ETH ===")
            print(f"  Deployer USDT:  {deployer_usdt_now/1e6:,.2f}")
            print(f"  Pool reserves:  {reserve_eth/1e18:.4f} ETH / {reserve_usdt/1e6:,.2f} USDT")
            print(f"  Expected ETH:   {expected_eth_wei/1e18:.6f}")
            print(f"  Min ETH out:    {min_out_wei/1e18:.6f} "
                  f"(slippage tol {args.reverse_swap_slippage*100:.1f}%)")

            try:
                _approve_usdt(w3, usdt, deployer, deployer_key,
                              pool_addr, deployer_usdt_now, w3.eth.chain_id)
                time.sleep(INTER_WALLET_SLEEP_S)
                tx_hash, receipt = _swap_usdt_for_eth(
                    w3, pool, deployer, deployer_key,
                    deployer_usdt_now, min_out_wei, w3.eth.chain_id,
                )
                actual_eth_wei = receipt.get("logs")  # from event, if we parsed
                print(f"  Swap tx:        {tx_hash.hex()}  (status={receipt.status})")
                print(f"  Recovered:      ~{expected_eth_wei/1e18:.6f} ETH")
            except Exception as e:
                print(f"  REVERSE SWAP FAILED: {e}")
        else:
            print(f"\nDeployer USDT ({deployer_usdt_now/1e6:.2f}) below "
                  f"reverse-swap threshold ({MIN_USDT_CONVERT_BASE/1e6:.0f}). "
                  f"Skipping conversion.")

    if not args.dry_run:
        print()
        print(f"Deployer balance AFTER: {w3.eth.get_balance(deployer)/1e18:.4f} ETH")
        print(f"Deployer USDT AFTER:    {usdt.functions.balanceOf(deployer).call()/1e6:,.2f}")
        keys_src = keys_jsonl if keys_jsonl.exists() else keys_json
        print(f"\nOnce satisfied, delete {keys_src} to remove private keys from disk.")


if __name__ == "__main__":
    main()
