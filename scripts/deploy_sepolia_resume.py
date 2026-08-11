"""Resume Sepolia deploy after MiMC timeout.

The original scripts/deploy_eth_mocks_sepolia.py deploys USDT + Pool with
EIP-1559 gas (this works), then calls the shared aml.chains.mimc.deploy_mimc
helper which uses LEGACY gas (this got stuck at 0.975 gwei on 2026-08-11).

This recovery script:
  1. Reads the addresses of the already-deployed USDT + Pool from CLI args.
  2. Optionally cancels a stuck pending tx at a specified nonce by sending
     a 0-value self-transfer at that nonce with EIP-1559 gas.
  3. Deploys MiMC + Verifier + Tornado + Bridge with EIP-1559 gas and a
     longer per-tx timeout (300s), all at min priority fee 5 gwei.
  4. Writes deployments/sepolia.json with all 6 addresses.

Usage:
    python scripts/deploy_sepolia_resume.py \\
        --usdt 0x665A2176d7beF3bccE52F37F1e64c99D993Aa7Ba \\
        --pool 0x229cC888FE17c81CD474Ea8eb6F5387f1739a4c4 \\
        --cancel-nonce 7                   # optional
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
DEPLOYMENTS_DIR = REPO_ROOT / "deployments"
SEPOLIA_JSON = DEPLOYMENTS_DIR / "sepolia.json"

MIMC_ABI_JS = REPO_ROOT / "scripts" / "zk_helpers.js"

VERIFIER_ARTIFACT = REPO_ROOT / "out" / "Verifier.sol" / "Groth16Verifier.json"
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"
BRIDGE_ARTIFACT = REPO_ROOT / "out" / "MockBridge.sol" / "MockBridge.json"

MERKLE_DEPTH = 10
TORNADO_DENOMINATION_WEI = 10**18
BOOTSTRAP_USDT = 100_000 * 10**6
BOOTSTRAP_ETH_WEI = 5 * 10**17

MIN_PRIORITY_GWEI = 5           # higher than default 2 gwei to unstick things


def _redact_rpc(rpc: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(rpc)
    parts = [x for x in p.path.split("/") if x]
    if parts:
        parts[-1] = "<redacted>"
    return f"{p.scheme}://{p.netloc}/" + "/".join(parts)


def _raw_tx(signed) -> bytes:
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


def _send(w3, fn_or_tx_dict, sender, key, gas=2_000_000, value=0, nonce=None,
          priority_gwei=MIN_PRIORITY_GWEI, timeout=300):
    """Send tx with EIP-1559 gas and configurable priority + timeout."""
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or w3.eth.gas_price
    priority = w3.to_wei(priority_gwei, "gwei")
    max_fee = base_fee * 2 + priority

    if hasattr(fn_or_tx_dict, "build_transaction"):
        tx = fn_or_tx_dict.build_transaction({
            "from": sender,
            "nonce": nonce if nonce is not None else w3.eth.get_transaction_count(sender),
            "gas": gas,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority,
            "value": value,
            "chainId": w3.eth.chain_id,
        })
    else:
        tx = dict(fn_or_tx_dict)
        tx.update({
            "from": sender,
            "nonce": nonce if nonce is not None else w3.eth.get_transaction_count(sender),
            "gas": gas,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority,
            "value": value,
            "chainId": w3.eth.chain_id,
        })

    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
    print(f"  tx sent: {tx_hash.hex()}  (nonce={tx['nonce']}, priority={priority_gwei} gwei)")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
    if receipt.status != 1:
        raise RuntimeError(f"tx {tx_hash.hex()} REVERTED — check Etherscan")
    return receipt


def cancel_stuck_tx(w3, sender, key, nonce):
    """Cancel a stuck pending tx by broadcasting a 0-value self-transfer at
    the same nonce with a much higher priority fee. If the stuck tx is a
    legacy tx paying ~1 gwei, our EIP-1559 replacement at 5 gwei priority
    will win the mempool race."""
    print(f"\nCancelling stuck tx at nonce {nonce} (0 ETH self-transfer with 5 gwei priority)...")
    tx_dict = {"to": sender, "value": 0, "data": b""}
    receipt = _send(
        w3, tx_dict, sender, key, gas=21_000, nonce=nonce,
        priority_gwei=MIN_PRIORITY_GWEI, timeout=180,
    )
    print(f"  cancel confirmed at block {receipt.blockNumber} (gas used {receipt.gasUsed})")


def deploy_mimc_eip1559(w3, deployer, key):
    """Deploy MiMCSponge with EIP-1559 gas (unlike the shared helper)."""
    import subprocess
    print("\n=== Deploying MiMCSponge (EIP-1559, priority 5 gwei) ===")
    abi = json.loads(subprocess.run(
        ["node", str(MIMC_ABI_JS), "mimc-abi"],
        check=True, capture_output=True, text=True,
    ).stdout)
    bytecode = subprocess.run(
        ["node", str(MIMC_ABI_JS), "mimc-bytecode"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    receipt = _send(
        w3, factory.constructor(), deployer, key,
        gas=5_000_000, priority_gwei=MIN_PRIORITY_GWEI, timeout=300,
    )
    mimc = w3.eth.contract(address=receipt.contractAddress, abi=abi)
    print(f"  MiMCSponge: {mimc.address} (gas used {receipt.gasUsed:,})")
    return mimc


def load_artifact(path):
    with path.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--usdt", required=True,
                        help="Already-deployed MockUSDT address")
    parser.add_argument("--pool", required=True,
                        help="Already-deployed MockUniswapV2Pool address")
    parser.add_argument("--cancel-nonce", type=int, default=None,
                        help="Optional nonce to cancel a stuck pending tx first")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env.sepolia")
    rpc = os.environ["SEPOLIA_RPC_URL"]
    key = os.environ["SEPOLIA_DEPLOYER_PRIVATE_KEY"]
    if not key.startswith("0x"):
        key = "0x" + key

    w3 = Web3(Web3.HTTPProvider(rpc))
    if w3.eth.chain_id != 11155111:
        raise SystemExit(f"Wrong chain: {w3.eth.chain_id}")
    deployer = w3.eth.account.from_key(key).address

    print(f"RPC:      {_redact_rpc(rpc)}")
    print(f"Deployer: {deployer}")
    print(f"Balance:  {w3.eth.get_balance(deployer) / 10**18:.4f} ETH")
    print(f"Nonce:    {w3.eth.get_transaction_count(deployer)}")

    # 1. Cancel stuck tx if requested
    if args.cancel_nonce is not None:
        cancel_stuck_tx(w3, deployer, key, args.cancel_nonce)

    # 2. MiMC with EIP-1559
    mimc = deploy_mimc_eip1559(w3, deployer, key)

    # 3. Verifier
    print("\n=== Deploying Groth16 Verifier ===")
    verifier_abi, verifier_bytecode = load_artifact(VERIFIER_ARTIFACT)
    r = _send(w3, w3.eth.contract(abi=verifier_abi, bytecode=verifier_bytecode)
              .constructor(), deployer, key)
    verifier = w3.eth.contract(address=r.contractAddress, abi=verifier_abi)
    print(f"  Verifier: {verifier.address}  (gas used {r.gasUsed:,})")

    # 4. MockTornado
    print("\n=== Deploying MockTornado ===")
    tornado_abi, tornado_bytecode = load_artifact(TORNADO_ARTIFACT)
    r = _send(w3, w3.eth.contract(abi=tornado_abi, bytecode=tornado_bytecode)
              .constructor(verifier.address, mimc.address, MERKLE_DEPTH),
              deployer, key, gas=10_000_000)
    tornado = w3.eth.contract(address=r.contractAddress, abi=tornado_abi)
    print(f"  MockTornado: {tornado.address}  (gas used {r.gasUsed:,})")
    print(f"  denomination: {tornado.functions.DENOMINATION().call() / 10**18} ETH")

    # 5. MockBridge
    print("\n=== Deploying MockBridge ===")
    bridge_abi, bridge_bytecode = load_artifact(BRIDGE_ARTIFACT)
    r = _send(w3, w3.eth.contract(abi=bridge_abi, bytecode=bridge_bytecode)
              .constructor(args.usdt), deployer, key)
    bridge = w3.eth.contract(address=r.contractAddress, abi=bridge_abi)
    print(f"  MockBridge: {bridge.address}  (gas used {r.gasUsed:,})")

    # 6. Save sepolia.json with all 6 addresses
    DEPLOYMENTS_DIR.mkdir(exist_ok=True)
    payload = {
        "chain_id": 11155111,
        "chain_name": "sepolia",
        "deployed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "contracts": {
            "MockUSDT": args.usdt,
            "MockUniswapV2Pool": args.pool,
            "MiMCSponge": mimc.address,
            "Verifier": verifier.address,
            "MockTornado": tornado.address,
            "MockBridge": bridge.address,
        },
        "constants": {
            "merkle_depth": MERKLE_DEPTH,
            "tornado_denomination_wei": TORNADO_DENOMINATION_WEI,
            "bootstrap_usdt_micro": BOOTSTRAP_USDT,
            "bootstrap_eth_wei": BOOTSTRAP_ETH_WEI,
        },
        "notes": "Deploy completado en 2 fases: USDT+Pool en primer run "
                 "(scripts/deploy_eth_mocks_sepolia.py), MiMC+Verifier+Tornado+"
                 "Bridge en fase resume (deploy_sepolia_resume.py) tras cancelar "
                 "tx MiMC pendiente por gas legacy underpriced.",
    }
    SEPOLIA_JSON.write_text(json.dumps(payload, indent=2))
    print(f"\nAll 6 addresses saved to {SEPOLIA_JSON.relative_to(REPO_ROOT)}")

    print("\n=== Deployment summary ===")
    for name, addr in payload["contracts"].items():
        print(f"  {name:<20} https://sepolia.etherscan.io/address/{addr}")

    print(f"\nRemaining balance: {w3.eth.get_balance(deployer) / 10**18:.4f} ETH")
    print("Next: python scripts/verify_sepolia_deployment.py")


if __name__ == "__main__":
    main()
