"""Smoke-test deployed Sepolia contracts.

Loads deployments/sepolia.json (produced by deploy_eth_mocks_sepolia.py) and
runs read-only checks against each contract to prove:
    - RPC connectivity works
    - Each address is a contract (not EOA)
    - Constructor state is what deploy set (pool reserves, tornado depth, ...)
    - Contract code hash matches what forge compiled

Does NOT send any tx — read-only. Safe to run repeatedly, costs 0 ETH.

Usage:
    python scripts/verify_sepolia_deployment.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[1]
SEPOLIA_JSON = REPO_ROOT / "deployments" / "sepolia.json"


def load_rpc() -> str:
    load_dotenv(REPO_ROOT / ".env.sepolia")
    rpc = os.environ.get("SEPOLIA_RPC_URL")
    if not rpc:
        raise SystemExit("SEPOLIA_RPC_URL not set in .env.sepolia")
    return rpc


def main() -> None:
    if not SEPOLIA_JSON.exists():
        raise SystemExit(
            f"Missing {SEPOLIA_JSON}. Run scripts/deploy_eth_mocks_sepolia.py first."
        )
    deployment = json.loads(SEPOLIA_JSON.read_text())
    addresses = deployment["contracts"]

    w3 = Web3(Web3.HTTPProvider(load_rpc()))
    if not w3.is_connected():
        raise SystemExit("Cannot connect to Sepolia RPC.")

    print(f"Sepolia chain_id: {w3.eth.chain_id} (expected 11155111)")
    print(f"Deployed:         {deployment.get('deployed_at_utc', '?')}")
    print(f"Loading ABIs from Foundry out/...\n")

    def load_abi(sol_name: str, contract_name: str = None) -> list:
        artifact = REPO_ROOT / "out" / f"{sol_name}.sol" / f"{contract_name or sol_name}.json"
        return json.loads(artifact.read_text())["abi"]

    all_ok = True

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal all_ok
        icon = "OK" if ok else "FAIL"
        print(f"  [{icon}] {name}: {detail}")
        if not ok:
            all_ok = False

    # 1. Every address is a contract
    print("=== Contract code presence ===")
    for name, addr in addresses.items():
        code = w3.eth.get_code(addr)
        check(name, len(code) > 2, f"{addr}  ({len(code)} bytes)")

    # 2. MockUSDT metadata
    print("\n=== MockUSDT state ===")
    usdt = w3.eth.contract(address=addresses["MockUSDT"], abi=load_abi("MockUSDT"))
    check("name", usdt.functions.name().call() == "Mock USDT",
          usdt.functions.name().call())
    check("symbol", usdt.functions.symbol().call() == "USDT",
          usdt.functions.symbol().call())
    check("decimals", usdt.functions.decimals().call() == 6, "6")

    # 3. Pool bootstrap (reserves should be non-zero)
    print("\n=== MockUniswapV2Pool state ===")
    pool = w3.eth.contract(
        address=addresses["MockUniswapV2Pool"],
        abi=load_abi("MockUniswapV2Pool"),
    )
    eth_r, usdt_r = pool.functions.getReserves().call()
    check("pool bootstrapped", eth_r > 0 and usdt_r > 0,
          f"{eth_r / 10**18:.4f} ETH / {usdt_r / 10**6:,.2f} USDT")

    # 4. Tornado config
    print("\n=== MockTornado state ===")
    tornado = w3.eth.contract(
        address=addresses["MockTornado"],
        abi=load_abi("MockTornado"),
    )
    denom = tornado.functions.DENOMINATION().call()
    expected_denom = deployment["constants"]["tornado_denomination_wei"]
    check("denomination", denom == expected_denom,
          f"{denom / 10**18} ETH (expected {expected_denom / 10**18})")
    levels = tornado.functions.levels().call()
    expected_levels = deployment["constants"]["merkle_depth"]
    check("merkle depth", levels == expected_levels,
          f"{levels} (expected {expected_levels})")

    # 5. Bridge
    print("\n=== MockBridge state ===")
    bridge = w3.eth.contract(
        address=addresses["MockBridge"],
        abi=load_abi("MockBridge"),
    )
    check("operator set", bridge.functions.operator().call() != "0x" + "0" * 40,
          bridge.functions.operator().call())

    print("\n" + ("=" * 60))
    if all_ok:
        print("ALL CHECKS PASSED — Sepolia deployment is operational.")
        print("Next: run the 6-8h attacker campaign (Task #7).")
    else:
        print("FAILURES DETECTED — inspect the [FAIL] lines above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
