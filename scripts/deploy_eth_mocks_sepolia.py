"""Deploy mock AML contracts to Sepolia testnet — TFM external validation.

Adapts scripts/deploy_eth_mocks.py from Anvil (ephemeral) to Sepolia (persistent).
Saves deployment addresses to deployments/sepolia.json for reuse by the campaign
runner (scripts/run_sepolia_campaign.py, see Task #7).

Deploys, in order:
    1. MockUSDT          (ERC-20, 6 decimals)
    2. MockUniswapV2Pool (ETH/USDT constant-product AMM) + bootstrap liquidity
    3. MiMCSponge        (auto-generated hasher from circomlibjs)
    4. Verifier          (Groth16 verifier for the withdraw circuit)
    5. MockTornado       (ZK mixer at depth 10, denomination 0.01 ETH on Sepolia)
    6. MockBridge        (USDT lock-and-release for cross-chain simulation)

Sepolia-specific differences vs the Anvil script:
    - Reads RPC URL + deployer key from .env.sepolia (NEVER committed)
    - Uses EIP-1559 gas (Sepolia supports it — 90%+ cheaper than legacy)
    - Sleeps between txs (Sepolia blocks are ~12s; nonce management matters)
    - Smaller denominations (Sepolia ETH is faucet-limited)
    - Saves addresses to deployments/sepolia.json for the campaign to load
    - Skips the ZK laundering demo — that's the campaign's job, not deploy

Prereqs:
    - Foundry installed (~/.foundry/bin/forge)
    - aml package installed in editable mode (pip install -e .)
    - bash scripts/install_zk_tools.sh
    - bash scripts/setup_zk.sh withdraw      # writes contracts/Verifier.sol
    - .env.sepolia populated (see .env.sepolia.example)
    - Deployer wallet funded with >= 0.2 ETH Sepolia

Usage:
    python scripts/deploy_eth_mocks_sepolia.py [--dry-run] [--only USDT,Pool,...]
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

from aml.chains.mimc import deploy_mimc

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENTS_DIR = REPO_ROOT / "deployments"
SEPOLIA_JSON = DEPLOYMENTS_DIR / "sepolia.json"

USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"
POOL_ARTIFACT = REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"
BRIDGE_ARTIFACT = REPO_ROOT / "out" / "MockBridge.sol" / "MockBridge.json"
VERIFIER_ARTIFACT = REPO_ROOT / "out" / "Verifier.sol" / "Groth16Verifier.json"

# Sepolia budget knobs — faucet ETH is limited to ~1-2/day.
# NOTE: MockTornado.sol has DENOMINATION hardcoded as `1 ether` on line 49
# (contract constant, not passable via constructor). So each mixer deposit
# costs 1 ETH regardless of TORNADO_DENOMINATION_WEI below — that constant
# is metadata only, used for the deployments/sepolia.json output.
# If you want <1 ETH deposits for the campaign, either:
#   (a) edit MockTornado.sol line 49 and recompile with `forge build`, or
#   (b) accept 6-8 deposits per campaign with 8 ETH budget.
BOOTSTRAP_USDT = 100_000 * 10**6            # 100k USDT (mock, permissionless)
BOOTSTRAP_ETH_WEI = 5 * 10**17              # 0.5 ETH into pool (permanent liquidity)
TORNADO_DENOMINATION_WEI = 10**18           # 1 ETH — MUST match MockTornado.sol L49
MERKLE_DEPTH = 10                           # 2^10 = 1024 deposit capacity

# Sepolia block time is ~12s. Sleep between txs to avoid nonce races
# when the RPC provider hasn't propagated the previous receipt yet.
INTER_TX_SLEEP_S = 2


def _raw_tx(signed) -> bytes:
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes attribute")
    return raw


def _send(w3, fn, sender: str, key: str, gas: int = 2_000_000, value: int = 0):
    """Send tx with EIP-1559 gas. Returns receipt."""
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or w3.eth.gas_price
    priority = w3.to_wei(2, "gwei")           # 2 gwei tip is generous on Sepolia
    max_fee = base_fee * 2 + priority
    tx = fn.build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": gas,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority,
        "value": value,
        "chainId": w3.eth.chain_id,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"tx {tx_hash.hex()} reverted; see Sepolia Etherscan")
    time.sleep(INTER_TX_SLEEP_S)
    return receipt


def load_artifact(path: Path) -> tuple[list, str]:
    with path.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


def _redact_rpc(rpc: str) -> str:
    """Redact API key from RPC URL for safe printing.

    Handles both query-param style (https://host?key=X) and Alchemy/Infura
    path style (https://host/v2/KEY, https://host/v3/PROJECT_ID). The full
    URL contains a secret that must never be printed to stdout/log files.
    """
    from urllib.parse import urlparse
    parsed = urlparse(rpc)
    host = f"{parsed.scheme}://{parsed.netloc}"
    # Alchemy/Infura embed the key in the last path segment.
    # Keep the version prefix but redact the segment after it.
    path_parts = [p for p in parsed.path.split("/") if p]
    if path_parts:
        # /v2/<key> -> /v2/<redacted>, /v3/<project> -> /v3/<redacted>
        path_parts[-1] = "<redacted>"
        host += "/" + "/".join(path_parts)
    return host


def load_env() -> tuple[str, str]:
    """Load Sepolia RPC + deployer key from .env.sepolia. Exits if either missing."""
    env_path = REPO_ROOT / ".env.sepolia"
    if not env_path.exists():
        raise SystemExit(
            f"Missing {env_path}. Copy .env.sepolia.example and fill in your keys.\n"
            "NEVER commit .env.sepolia — it contains a private key."
        )
    load_dotenv(env_path)
    rpc = os.environ.get("SEPOLIA_RPC_URL")
    key = os.environ.get("SEPOLIA_DEPLOYER_PRIVATE_KEY")
    if not rpc:
        raise SystemExit("SEPOLIA_RPC_URL not set in .env.sepolia")
    if not key:
        raise SystemExit("SEPOLIA_DEPLOYER_PRIVATE_KEY not set in .env.sepolia")
    if not key.startswith("0x"):
        key = "0x" + key
    return rpc, key


def check_balance(w3: Web3, deployer: str, minimum_eth: float = 0.15) -> None:
    balance_wei = w3.eth.get_balance(deployer)
    balance_eth = balance_wei / 10**18
    print(f"Deployer:           {deployer}")
    print(f"Balance:            {balance_eth:.4f} ETH")
    if balance_eth < minimum_eth:
        raise SystemExit(
            f"Insufficient balance ({balance_eth:.4f} ETH). "
            f"Need at least {minimum_eth} ETH for full deploy + bootstrap. "
            "Use https://sepoliafaucet.com or https://alchemy.com/faucets/ethereum-sepolia"
        )


def save_deployment(addresses: dict) -> None:
    DEPLOYMENTS_DIR.mkdir(exist_ok=True)
    payload = {
        "chain_id": 11155111,
        "chain_name": "sepolia",
        "deployed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "contracts": addresses,
        "constants": {
            "merkle_depth": MERKLE_DEPTH,
            "tornado_denomination_wei": TORNADO_DENOMINATION_WEI,
            "bootstrap_usdt_micro": BOOTSTRAP_USDT,
            "bootstrap_eth_wei": BOOTSTRAP_ETH_WEI,
        },
    }
    SEPOLIA_JSON.write_text(json.dumps(payload, indent=2))
    print(f"\nDeployment addresses saved to {SEPOLIA_JSON.relative_to(REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="Only verify RPC + balance + artifacts, don't deploy.")
    args = parser.parse_args()

    for artifact in (USDT_ARTIFACT, POOL_ARTIFACT, TORNADO_ARTIFACT,
                     BRIDGE_ARTIFACT, VERIFIER_ARTIFACT):
        if not artifact.exists():
            raise SystemExit(
                f"Missing artifact {artifact}. Run `forge build` first.\n"
                f"If Verifier.sol missing, run `bash scripts/setup_zk.sh withdraw`."
            )

    rpc, key = load_env()
    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        raise SystemExit(f"Cannot connect to Sepolia RPC: {_redact_rpc(rpc)}")
    if w3.eth.chain_id != 11155111:
        raise SystemExit(
            f"Wrong chain: expected Sepolia (11155111), got {w3.eth.chain_id}. "
            "Check SEPOLIA_RPC_URL points to Sepolia, not mainnet or another testnet."
        )

    deployer = w3.eth.account.from_key(key).address
    print(f"Sepolia RPC:        {_redact_rpc(rpc)}  (chain_id={w3.eth.chain_id})")
    check_balance(w3, deployer)

    if args.dry_run:
        print("\n[--dry-run] Environment OK. Skipping deploy.")
        return

    print("\nLoading artifacts...")
    usdt_abi, usdt_bytecode = load_artifact(USDT_ARTIFACT)
    pool_abi, pool_bytecode = load_artifact(POOL_ARTIFACT)
    tornado_abi, tornado_bytecode = load_artifact(TORNADO_ARTIFACT)
    bridge_abi, bridge_bytecode = load_artifact(BRIDGE_ARTIFACT)
    verifier_abi, verifier_bytecode = load_artifact(VERIFIER_ARTIFACT)

    addresses = {}

    print("\n=== Deploying MockUSDT ===")
    r = _send(w3, w3.eth.contract(abi=usdt_abi, bytecode=usdt_bytecode).constructor(),
              deployer, key)
    usdt = w3.eth.contract(address=r.contractAddress, abi=usdt_abi)
    addresses["MockUSDT"] = usdt.address
    print(f"  MockUSDT:           {usdt.address}")
    print(f"  gas used:           {r.gasUsed:,}")

    print("\n=== Deploying MockUniswapV2Pool ===")
    r = _send(w3, w3.eth.contract(abi=pool_abi, bytecode=pool_bytecode)
              .constructor(usdt.address), deployer, key)
    pool = w3.eth.contract(address=r.contractAddress, abi=pool_abi)
    addresses["MockUniswapV2Pool"] = pool.address
    print(f"  MockUniswapV2Pool:  {pool.address}")
    print(f"  Bootstrapping pool (0.5 ETH + 100k USDT)...")
    _send(w3, usdt.functions.mint(deployer, BOOTSTRAP_USDT), deployer, key, gas=200_000)
    _send(w3, usdt.functions.approve(pool.address, BOOTSTRAP_USDT), deployer, key, gas=200_000)
    _send(w3, pool.functions.bootstrap(BOOTSTRAP_USDT), deployer, key, value=BOOTSTRAP_ETH_WEI)
    eth_r, usdt_r = pool.functions.getReserves().call()
    print(f"  pool reserves:      {eth_r / 10**18:.4f} ETH / {usdt_r / 10**6:,.2f} USDT")
    print(f"  spot price:         1 ETH = {(usdt_r / 10**6) / (eth_r / 10**18):,.2f} USDT")

    print("\n=== Deploying MiMCSponge (from circomlibjs) ===")
    mimc = deploy_mimc(w3, deployer, key)
    addresses["MiMCSponge"] = mimc.address
    print(f"  MiMCSponge:         {mimc.address}")

    print("\n=== Deploying Groth16 Verifier ===")
    r = _send(w3, w3.eth.contract(abi=verifier_abi, bytecode=verifier_bytecode).constructor(),
              deployer, key)
    verifier = w3.eth.contract(address=r.contractAddress, abi=verifier_abi)
    addresses["Verifier"] = verifier.address
    print(f"  Verifier (Groth16): {verifier.address}")

    print("\n=== Deploying MockTornado ===")
    r = _send(w3, w3.eth.contract(abi=tornado_abi, bytecode=tornado_bytecode)
              .constructor(verifier.address, mimc.address, MERKLE_DEPTH),
              deployer, key, gas=10_000_000)
    tornado = w3.eth.contract(address=r.contractAddress, abi=tornado_abi)
    addresses["MockTornado"] = tornado.address
    print(f"  MockTornado (ZK):   {tornado.address}")
    print(f"  depth:              {MERKLE_DEPTH} (capacity 2^{MERKLE_DEPTH} = {1 << MERKLE_DEPTH})")
    print(f"  denomination:       {tornado.functions.DENOMINATION().call() / 10**18} ETH")

    print("\n=== Deploying MockBridge ===")
    r = _send(w3, w3.eth.contract(abi=bridge_abi, bytecode=bridge_bytecode)
              .constructor(usdt.address), deployer, key)
    bridge = w3.eth.contract(address=r.contractAddress, abi=bridge_abi)
    addresses["MockBridge"] = bridge.address
    print(f"  MockBridge:         {bridge.address}")

    save_deployment(addresses)

    print("\n=== Deployment summary ===")
    for name, addr in addresses.items():
        print(f"  {name:<20} https://sepolia.etherscan.io/address/{addr}")

    final_balance = w3.eth.get_balance(deployer) / 10**18
    print(f"\nDeployer remaining balance: {final_balance:.4f} ETH")
    print(f"Next: python scripts/verify_sepolia_deployment.py")


if __name__ == "__main__":
    main()
