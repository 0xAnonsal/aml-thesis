"""Deploy MockOraclePool on Sepolia with Chainlink ETH/USD feed.

Replaces the constant-product MockUniswapV2Pool with an oracle-pegged
mock that uses Chainlink's live ETH/USD price feed on Sepolia. Same
external ABI (getReserves, getAmountOut, swapETHForUSDT, swapUSDTForETH,
bootstrap, bootstrapped) so tools.py works unmodified.

Chainlink ETH/USD Sepolia feed: 0x694AA1769357215DE4FAC081bf1f309aDC325306
(8 decimals, updated ~hourly by Chainlink oracle network)

Bootstrap ETH: small reserve (~0.5 ETH) for USDT→ETH swap payouts.
USDT is elastic — minted on demand via MockUSDT.mint() (permissionless).
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
POOL_ARTIFACT = REPO / "out" / "MockOraclePool.sol" / "MockOraclePool.json"

CHAINLINK_ETH_USD_SEPOLIA = "0x694AA1769357215DE4FAC081bf1f309aDC325306"


def _send(w3, fn, sender, key, value=0, gas=3_000_000):
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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--reserve", type=float, default=0.5,
                   help="ETH to seed as reserve for USDT->ETH swap payouts (default 0.5)")
    p.add_argument("--oracle", default=CHAINLINK_ETH_USD_SEPOLIA,
                   help="Chainlink AggregatorV3 address (default Sepolia ETH/USD)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    rpc = os.environ["SEPOLIA_RPC_URL"]
    key = os.environ["SEPOLIA_DEPLOYER_PRIVATE_KEY"]
    w3 = Web3(Web3.HTTPProvider(rpc))
    deployer = Account.from_key(key).address
    print(f"deployer:  {deployer}")
    print(f"balance:   {float(w3.from_wei(w3.eth.get_balance(deployer), 'ether')):.4f} ETH")

    depl = json.load(open(DEPLOYMENT))
    old_pool = depl["contracts"].get("MockUniswapV2Pool")
    usdt_addr = Web3.to_checksum_address(depl["contracts"]["MockUSDT"])
    oracle_addr = Web3.to_checksum_address(args.oracle)
    print(f"USDT:      {usdt_addr}")
    print(f"oracle:    {oracle_addr}  (Chainlink ETH/USD Sepolia)")
    print(f"old pool:  {old_pool}  (will archive as MockUniswapV2Pool_OLD)")

    if args.dry_run:
        print(f"--dry-run: would deploy MockOraclePool + bootstrap {args.reserve} ETH")
        return 0

    pool_json = json.load(open(POOL_ARTIFACT))
    pool_bytecode = pool_json["bytecode"]["object"]
    pool_abi = pool_json["abi"]

    # 1. Deploy
    print("\n=== Deploying MockOraclePool ===")
    Pool = w3.eth.contract(abi=pool_abi, bytecode=pool_bytecode)
    receipt = _send(w3, Pool.constructor(usdt_addr, oracle_addr),
                    deployer, key, gas=2_000_000)
    new_pool_addr = receipt.contractAddress
    print(f"  new pool: {new_pool_addr}")
    pool = w3.eth.contract(address=new_pool_addr, abi=pool_abi)

    # 2. Sanity-check the oracle before bootstrap
    price_raw = pool.functions.currentOraclePrice().call()
    price_usd = price_raw / 1e8
    print(f"\n=== Oracle sanity ===")
    print(f"  Chainlink returned: {price_raw} (raw, 8 decimals)")
    print(f"  = ${price_usd:,.2f}/ETH")

    # 3. Bootstrap ETH reserve
    print(f"\n=== Bootstrap {args.reserve} ETH reserve ===")
    reserve_wei = int(args.reserve * 10**18)
    _send(w3, pool.functions.bootstrap(),
          deployer, key, value=reserve_wei, gas=200_000)
    r_eth, r_usdt_virtual = pool.functions.getReserves().call()
    print(f"  reserveETH: {r_eth/1e18} ETH")
    print(f"  virtual USDT (at spot): {r_usdt_virtual/1e6:,.2f}")

    # 4. Update deployment JSON
    print(f"\n=== Update {DEPLOYMENT} ===")
    depl.setdefault("deprecated_contracts_archive", {})
    if old_pool:
        depl["deprecated_contracts_archive"]["MockUniswapV2Pool_PRE_ORACLE"] = old_pool
        print(f"  archived pre-oracle pool: {old_pool}")
    depl["contracts"]["MockUniswapV2Pool"] = new_pool_addr
    depl["pool_type"] = "MockOraclePool"
    depl["oracle_feed"] = oracle_addr
    depl["oracle_pool_deploy_block"] = int(w3.eth.block_number)
    depl["oracle_pool_bootstrap_eth"] = args.reserve
    depl["oracle_pool_topup_ts"] = int(time.time())
    with open(DEPLOYMENT, "w") as f:
        json.dump(depl, f, indent=2)
    print(f"  new active pool: {new_pool_addr}")

    # 5. Demo: quote a swap
    print("\n=== Swap quotes (no slippage — always oracle price) ===")
    for eth_in in [0.1, 1.0, 5.0, 20.0]:
        quote = pool.functions.getAmountOut(
            int(eth_in * 10**18), r_eth, r_usdt_virtual
        ).call()
        print(f"  {eth_in:.1f} ETH → {quote/1e6:,.2f} USDT (at spot ${price_usd:,.2f})")

    print(f"\nDeployer balance AFTER: "
          f"{float(w3.from_wei(w3.eth.get_balance(deployer), 'ether')):.4f} ETH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
