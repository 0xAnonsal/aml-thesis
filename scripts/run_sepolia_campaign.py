"""Sepolia campaign runner — Task #7 mini-campaign against deployed contracts.

Adapts src/aml/attackers/run_campaign.py from AnvilNode (ephemeral local)
to Sepolia (persistent testnet). Reuses everything downstream:
- Coordinator + sub-agents from src/aml/attackers/coordinator.py
- ToolDispatcher + 17 tools from src/aml/attackers/tools.py
- Scenarios from src/aml/attackers/scenarios.py
- Chain trace extractor from src/aml/chains/trace.py

Key differences vs Anvil runner:
1. Loads env from .env.sepolia (RPC + deployer key).
2. Loads pre-deployed contract addresses from deployments/sepolia.json
   (no re-deploy — reuses the contracts deployed 2026-08-11).
3. Funds a fresh alice wallet from deployer with configurable ETH amount.
4. Installs a `min_gas_price` Web3 middleware that raises the returned
   value from `eth_gasPrice` to at least MIN_GAS_PRICE_GWEI. This is
   critical because tools.py uses legacy `gasPrice = w3.eth.gas_price`
   in 40+ sites, and Sepolia RPC providers (Alchemy, Infura) frequently
   return values <=1 gwei that are insufficient for reliable inclusion
   (observed during the 2026-08-11 MiMC deploy incident).
5. Extracts on-chain trace via block range instead of "since deploy".

Usage:
    python scripts/run_sepolia_campaign.py --scenario defi-exploit \\
        --amount 3.0 --model sonnet --out results/sepolia_campaign/

For a smaller/safer test:
    python scripts/run_sepolia_campaign.py --scenario defi-exploit \\
        --amount 1.0 --model haiku --out results/sepolia_campaign/

Estimated wall-clock for `defi-exploit 3 ETH sonnet`: ~30-45 min.
Estimated cost: ~$0.30-0.60 LLM (Sonnet) or ~$0.05-0.10 (Haiku).
Estimated ETH spend from deployer: ~1.5 ETH (funds alice + gas).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

from aml.attackers import Coordinator, LLMClient, ToolDispatcher
from aml.attackers.scenarios import SCENARIOS
from aml.attackers.tools import _DEFAULT_GAS_RESERVE_ETH
from aml.chains.trace import extract_chain_trace, jsonable

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENTS_JSON = REPO_ROOT / "deployments" / "sepolia.json"

# Sepolia gas — override w3.eth.gas_price to at least this floor. Prevents
# the legacy `w3.eth.gas_price` calls in tools.py from returning the
# Alchemy default (~1 gwei) which is insufficient for reliable inclusion.
MIN_GAS_PRICE_GWEI = 3

# Contract identifier -> Foundry artifact path (for ABI loading).
ARTIFACT_PATHS = {
    "MockUSDT": REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json",
    "MockUniswapV2Pool": REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json",
    "MockTornado": REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json",
    "MockBridge": REPO_ROOT / "out" / "MockBridge.sol" / "MockBridge.json",
}


def install_gas_floor_middleware(w3: Web3, min_gwei: int) -> None:
    """Raise eth_gasPrice responses to at least `min_gwei`.

    Sepolia RPC providers routinely return gas price suggestions barely
    above base fee, producing txs that stall in mempool. Since tools.py
    uses legacy `gasPrice = w3.eth.gas_price` in ~40 sites, patching the
    middleware layer is the least invasive fix. Web3.py v7 uses class-
    based middleware (Web3Middleware); this installs a subclass that
    overrides response_processor for the eth_gasPrice method.
    """
    from web3.middleware import Web3Middleware
    min_wei = w3.to_wei(min_gwei, "gwei")

    class GasFloorMiddleware(Web3Middleware):
        def response_processor(self, method, response):
            if method == "eth_gasPrice" and isinstance(response, dict) and "result" in response:
                try:
                    current = int(response["result"], 16)
                    if current < min_wei:
                        response["result"] = hex(min_wei)
                except (ValueError, TypeError):
                    pass
            return response

    w3.middleware_onion.add(GasFloorMiddleware, name="gas_floor")


def _redact_rpc(rpc: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(rpc)
    parts = [x for x in p.path.split("/") if x]
    if parts:
        parts[-1] = "<redacted>"
    return f"{p.scheme}://{p.netloc}/" + "/".join(parts)


def load_sepolia_env() -> tuple[str, str]:
    # Load .env first (ANTHROPIC_API_KEY + shared secrets), then .env.sepolia
    # (chain-specific overrides). Chain-specific keys take precedence.
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(REPO_ROOT / ".env.sepolia", override=True)
    rpc = os.environ.get("SEPOLIA_RPC_URL")
    key = os.environ.get("SEPOLIA_DEPLOYER_PRIVATE_KEY")
    if not rpc:
        raise SystemExit("SEPOLIA_RPC_URL not set")
    if not key:
        raise SystemExit("SEPOLIA_DEPLOYER_PRIVATE_KEY not set")
    if not key.startswith("0x"):
        key = "0x" + key
    return rpc, key


def load_deployed_contracts(w3: Web3):
    """Load pre-deployed Sepolia contracts as web3 handles."""
    if not DEPLOYMENTS_JSON.exists():
        raise SystemExit(
            f"Missing {DEPLOYMENTS_JSON}. Run scripts/deploy_eth_mocks_sepolia.py first."
        )
    deployment = json.loads(DEPLOYMENTS_JSON.read_text())
    addrs = deployment["contracts"]

    def load(name):
        with ARTIFACT_PATHS[name].open() as f:
            abi = json.load(f)["abi"]
        return w3.eth.contract(address=addrs[name], abi=abi)

    return {
        "usdt": load("MockUSDT"),
        "pool": load("MockUniswapV2Pool"),
        "tornado": load("MockTornado"),
        "bridge": load("MockBridge"),
    }


def _raw_tx(signed):
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


def fund_alice_from_deployer(w3: Web3, deployer: str, deployer_key: str,
                             eth_amount: float) -> tuple[str, str]:
    """Generate a fresh alice wallet + fund from deployer with `eth_amount` ETH."""
    acct = w3.eth.account.create()
    alice, alice_key = acct.address, acct.key.hex()

    value_wei = int(eth_amount * 10**18)
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or w3.eth.gas_price
    priority = w3.to_wei(MIN_GAS_PRICE_GWEI, "gwei")
    max_fee = base_fee * 2 + priority

    tx = {
        "from": deployer,
        "to": alice,
        "value": value_wei,
        "nonce": w3.eth.get_transaction_count(deployer),
        "gas": 21_000,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority,
        "chainId": w3.eth.chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, private_key=deployer_key)
    tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
    print(f"[funding] alice={alice} funded with {eth_amount} ETH "
          f"(tx {tx_hash.hex()})", file=sys.stderr)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"alice funding tx reverted: {tx_hash.hex()}")

    return alice, alice_key


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", required=True, choices=list(SCENARIOS.keys()))
    parser.add_argument("--amount", type=float, default=None,
                        help="Amount to launder (default from scenario)")
    parser.add_argument("--model", default="haiku",
                        help="LLM model: haiku|sonnet|opus (default haiku)")
    parser.add_argument("--out", default="results/sepolia_campaign",
                        help="Output directory (default results/sepolia_campaign/)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Seed for reproducibility (default random)")
    parser.add_argument("--alice-funding-eth", type=float, default=None,
                        help="ETH to fund alice with (default = amount + 0.5 gas buffer)")
    parser.add_argument("--max-iterations", type=int, default=60)
    parser.add_argument("--sub-agent-max-iterations", type=int, default=40)
    parser.add_argument("--max-tokens", type=int, default=8192)
    args = parser.parse_args()

    scenario = SCENARIOS[args.scenario]
    amount = args.amount if args.amount is not None else scenario.default_amount
    seed = args.seed if args.seed is not None else random.randint(1, 10**9)
    random.seed(seed)

    alice_funding = (
        args.alice_funding_eth if args.alice_funding_eth is not None
        else amount + 0.5   # amount to launder + 0.5 ETH for gas buffer
    )

    rpc, deployer_key = load_sepolia_env()
    w3 = Web3(Web3.HTTPProvider(rpc))
    install_gas_floor_middleware(w3, MIN_GAS_PRICE_GWEI)

    if w3.eth.chain_id != 11155111:
        raise SystemExit(f"Wrong chain: expected Sepolia (11155111), got {w3.eth.chain_id}")

    deployer = w3.eth.account.from_key(deployer_key).address
    deployer_balance = w3.eth.get_balance(deployer) / 10**18

    if deployer_balance < alice_funding + 0.05:
        raise SystemExit(
            f"Deployer balance {deployer_balance:.4f} ETH insufficient — "
            f"need >= {alice_funding + 0.05:.2f} ETH (alice funding + 0.05 gas)."
        )

    contracts = load_deployed_contracts(w3)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    run_name = f"{timestamp}_{scenario.name}_seed{seed}_sepolia"
    out_dir = Path(args.out) / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=" * 70, file=sys.stderr)
    print(f"[runner] SEPOLIA CAMPAIGN — {run_name}", file=sys.stderr)
    print(f"  RPC:          {_redact_rpc(rpc)}", file=sys.stderr)
    print(f"  chain_id:     {w3.eth.chain_id}", file=sys.stderr)
    print(f"  deployer:     {deployer}", file=sys.stderr)
    print(f"  balance:      {deployer_balance:.4f} ETH", file=sys.stderr)
    print(f"  scenario:     {scenario.name}", file=sys.stderr)
    print(f"  amount:       {amount} {scenario.asset.upper()}", file=sys.stderr)
    print(f"  alice funds:  {alice_funding} ETH", file=sys.stderr)
    print(f"  model:        {args.model}", file=sys.stderr)
    print(f"  seed:         {seed}", file=sys.stderr)
    print(f"  out_dir:      {out_dir}", file=sys.stderr)
    print(f"  gas floor:    {MIN_GAS_PRICE_GWEI} gwei", file=sys.stderr)
    print(f"  contracts:", file=sys.stderr)
    for k, v in contracts.items():
        print(f"    {k:8s} {v.address}", file=sys.stderr)
    print(f"=" * 70, file=sys.stderr)

    start_wall = time.time()
    start_block = w3.eth.block_number
    print(f"[runner] campaign starting at block {start_block}", file=sys.stderr)

    # Fund alice
    alice, alice_key = fund_alice_from_deployer(w3, deployer, deployer_key, alice_funding)
    print(f"[runner] alice funded, starting Coordinator...", file=sys.stderr)

    # Build dispatcher with pre-deployed contracts
    dispatcher = ToolDispatcher(
        w3=w3,
        usdt_contract=contracts["usdt"],
        wallets={deployer: deployer_key, alice: alice_key},
        pool_contract=contracts["pool"],
        tornado_contract=contracts["tornado"] if scenario.needs_tornado else None,
    )
    bootstrap_attacker_addrs = sorted(dispatcher.wallets.keys())

    # Run Coordinator
    coordinator = Coordinator(
        LLMClient(), dispatcher,
        model=args.model, sub_agent_model=args.model,
        max_iterations=args.max_iterations,
        sub_agent_max_iterations=args.sub_agent_max_iterations,
    )
    prompt = scenario.format_prompt(alice=alice, amount=amount)
    result = coordinator.run(
        prompt,
        max_tokens=args.max_tokens,
        sub_agent_max_tokens=args.max_tokens,
    )

    end_block = w3.eth.block_number
    wall_clock = time.time() - start_wall
    print(f"[runner] campaign done in {wall_clock:.1f}s "
          f"({wall_clock/60:.1f} min), stop={result.stopped_reason}, "
          f"cost=${result.cost_usd:.4f}, end block={end_block}",
          file=sys.stderr)

    # Post-run analysis
    all_attacker_addrs = sorted(dispatcher.wallets.keys())
    clean_exit_entries = list(dispatcher.registered_clean_exits)
    clean_exit_addrs = [e["address"] for e in clean_exit_entries]
    clean_exit_addr_set = set(clean_exit_addrs)
    new_burner_addrs = sorted(
        set(all_attacker_addrs) - set(bootstrap_attacker_addrs) - clean_exit_addr_set
    )

    gas_seed_wei = int(_DEFAULT_GAS_RESERVE_ETH * 10**18)
    clean_exit_records = []
    for entry in clean_exit_entries:
        addr = entry["address"]
        eth_final_wei = w3.eth.get_balance(addr)
        eth_received = max(0.0, (eth_final_wei - gas_seed_wei) / 10**18)
        usdt_final = contracts["usdt"].functions.balanceOf(addr).call()
        rec = {
            "address": addr,
            "exchange_platform": entry["exchange_platform"],
            "eth_received": eth_received,
            "usdt_received": usdt_final / 10**6,
        }
        if "note" in entry:
            rec["note"] = entry["note"]
        clean_exit_records.append(rec)

    addresses = {
        "attacker_wallets": all_attacker_addrs,
        "bootstrap_attackers": bootstrap_attacker_addrs,
        "burners_generated_during_campaign": new_burner_addrs,
        "source_wallet": alice,
        "clean_exit_wallets": clean_exit_addrs,
        "clean_exits_funded": [
            r["address"] for r in clean_exit_records
            if r["eth_received"] > 0 or r["usdt_received"] > 0
        ],
        "clean_exit_per_address": clean_exit_records,
        "operator_wallet": deployer,
        "contracts": {
            "usdt": contracts["usdt"].address,
            "pool": contracts["pool"].address,
            "tornado": contracts["tornado"].address,
            "bridge": contracts["bridge"].address,
        },
    }

    # Extract trace via block range (Sepolia: extract from start_block to end_block)
    print(f"[runner] extracting chain trace from block {start_block} to {end_block}...",
          file=sys.stderr)
    trace = extract_chain_trace(
        w3, end_block,
        known_contracts={
            "usdt": contracts["usdt"],
            "pool": contracts["pool"],
            "tornado": contracts["tornado"] if scenario.needs_tornado else None,
        },
        start_block=start_block,
    )
    print(f"[runner] {len(trace)} txs traced", file=sys.stderr)

    meta = {
        "run_name": run_name,
        "scenario": scenario.name,
        "asset": scenario.asset,
        "amount": amount,
        "seed": seed,
        "model": args.model,
        "timestamp_utc": timestamp,
        "wall_clock_seconds": wall_clock,
        "chain_id": w3.eth.chain_id,
        "chain_name": "sepolia",
        "start_block": start_block,
        "end_block": end_block,
        "alice_funding_eth": alice_funding,
        "gas_floor_gwei": MIN_GAS_PRICE_GWEI,
        "args": vars(args),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    (out_dir / "addresses.json").write_text(json.dumps(addresses, indent=2))

    # SECRETS — save private keys of every wallet the dispatcher generated,
    # so scripts/sweep_sepolia.py can later reclaim any residual ETH/USDT
    # stranded on those wallets. This file MUST NOT be committed —
    # gitignored via .gitignore (`wallets_keys.json`).
    wallets_keys = {
        "run_name": run_name,
        "chain_id": w3.eth.chain_id,
        "deployer": deployer,
        "wallets": dict(dispatcher.wallets),
        "warning": (
            "This file contains private keys. Even though these are Sepolia "
            "testnet wallets with no real-money value, NEVER commit this file "
            "to Git and NEVER paste its contents into chat, logs, or "
            "screenshots. Use scripts/sweep_sepolia.py to reclaim residual "
            "funds back to the deployer, then delete this file."
        ),
    }
    (out_dir / "wallets_keys.json").write_text(
        json.dumps(wallets_keys, indent=2, default=str)
    )
    print(f"[runner] wallets_keys.json saved ({len(dispatcher.wallets)} keys) "
          f"— gitignored; use scripts/sweep_sepolia.py to reclaim funds",
          file=sys.stderr)

    campaign_dict = {
        "successful": result.successful,
        "stopped_reason": result.stopped_reason,
        "iterations": result.iterations,
        "cost_usd": result.cost_usd,
        "total_tool_calls": result.total_tool_calls,
        "final_text": result.final_text,
        "delegations": result.delegations,
    }
    (out_dir / "campaign.json").write_text(
        json.dumps(campaign_dict, indent=2, default=str)
    )

    with (out_dir / "chain_trace.jsonl").open("w") as f:
        for tx in trace:
            f.write(json.dumps(jsonable(tx)) + "\n")

    # Human-readable summary
    summary_lines = [
        f"Sepolia campaign — {run_name}",
        f"Chain: Sepolia (11155111)",
        f"Scenario: {scenario.name} ({scenario.description[:80]}...)",
        f"Amount: {amount} {scenario.asset.upper()}",
        f"Wall clock: {wall_clock/60:.1f} min",
        f"Blocks: {start_block} -> {end_block} ({end_block - start_block} blocks)",
        f"LLM cost: ${result.cost_usd:.4f} ({args.model})",
        f"Tx traced: {len(trace)}",
        f"Attacker wallets: {len(all_attacker_addrs)}",
        f"Burners generated: {len(new_burner_addrs)}",
        f"Clean exits registered: {len(clean_exit_addrs)}",
        f"Clean exits funded: {sum(1 for r in clean_exit_records if r['eth_received'] > 0 or r['usdt_received'] > 0)}",
        f"Coordinator stopped: {result.stopped_reason}",
        f"",
        f"Etherscan links:",
        f"  Alice: https://sepolia.etherscan.io/address/{alice}",
        f"  Deployer: https://sepolia.etherscan.io/address/{deployer}",
    ]
    (out_dir / "summary.txt").write_text("\n".join(summary_lines))

    print(f"\n[runner] artifacts written to {out_dir}", file=sys.stderr)
    print("\n".join(summary_lines))


if __name__ == "__main__":
    main()
