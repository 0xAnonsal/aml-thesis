"""Campaign runner CLI — spin up a chain, drive an attacker campaign, dump
labeled artifacts for downstream detector training.

Usage:
    python -m aml.attackers.run_campaign \\
        --scenario defi-exploit --seed 42 --out runs/

    # see all scenarios
    python -m aml.attackers.run_campaign --list-scenarios

Per-run output directory (named `<utc-timestamp>_<scenario>_seed<N>/`):
    meta.json         args, timestamps, anvil chain id, block boundaries
    campaign.json     CampaignResult — delegations, sub-agent reports, cost
    chain_trace.jsonl one record per on-chain tx (from, to, value, gas,
                      decoded ERC-20 / mixer / swap events)
    addresses.json    attacker-controlled wallets vs source/clean/contracts
    summary.txt       human-readable one-pager

The chain_trace.jsonl + addresses.json are the LABELED ground truth that
detector training pipelines consume. Each tx is implicitly labeled
"attacker-touched" iff either endpoint is in addresses.json.attacker_wallets.

Designed for batch invocation (varying --seed, --amount, --scenario) to
build a dataset of N campaigns. Costs ~$0.20-0.50 per defi-exploit run on
Haiku, ~3 min wall clock (mostly Anvil tx execution + ZK proof gen).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web3 import Web3

from aml.attackers import Coordinator, LLMClient, ToolDispatcher
from aml.attackers.scenarios import SCENARIOS, Scenario
from aml.chains import AnvilNode
from aml.chains.mimc import deploy_mimc


REPO_ROOT = Path(__file__).resolve().parents[3]
USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"
POOL_ARTIFACT = REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"
VERIFIER_ARTIFACT = REPO_ROOT / "out" / "Verifier.sol" / "Groth16Verifier.json"

# Spot price at bootstrap: 1 ETH = 2000 USDT (500 ETH / 1M USDT pool).
POOL_BOOTSTRAP_ETH_WEI = 500 * 10**18
POOL_BOOTSTRAP_USDT_BASE = 1_000_000 * 10**6
MERKLE_DEPTH = 10


# --- chain setup helpers (duplicated from tests/test_coordinator.py;
# refactor into aml.chains.eth_stack once a third caller appears) --------


def _raw_tx(signed):
    return getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")


def _send(w3, fn, sender, key, gas=4_000_000, value=0):
    tx = fn.build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
        "value": value,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    return w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(_raw_tx(signed)))


def _load_artifact(path: Path):
    if not path.exists():
        subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)
    with path.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


def _deploy_usdt(w3, deployer, deployer_key):
    abi, bytecode = _load_artifact(USDT_ARTIFACT)
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    receipt = _send(w3, factory.constructor(), deployer, deployer_key)
    return w3.eth.contract(address=receipt.contractAddress, abi=abi)


def _deploy_pool(w3, deployer, deployer_key, usdt):
    abi, bytecode = _load_artifact(POOL_ARTIFACT)
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    receipt = _send(w3, factory.constructor(usdt.address), deployer, deployer_key)
    pool = w3.eth.contract(address=receipt.contractAddress, abi=abi)
    _send(w3, usdt.functions.mint(deployer, POOL_BOOTSTRAP_USDT_BASE),
          deployer, deployer_key, gas=200_000)
    _send(w3, usdt.functions.approve(pool.address, POOL_BOOTSTRAP_USDT_BASE),
          deployer, deployer_key, gas=200_000)
    _send(w3, pool.functions.bootstrap(POOL_BOOTSTRAP_USDT_BASE),
          deployer, deployer_key, value=POOL_BOOTSTRAP_ETH_WEI)
    return pool


def _deploy_tornado(w3, deployer, deployer_key):
    mimc = deploy_mimc(w3, deployer, deployer_key)
    verifier_abi, verifier_bytecode = _load_artifact(VERIFIER_ARTIFACT)
    v_factory = w3.eth.contract(abi=verifier_abi, bytecode=verifier_bytecode)
    verifier_addr = _send(
        w3, v_factory.constructor(), deployer, deployer_key,
    ).contractAddress
    tornado_abi, tornado_bytecode = _load_artifact(TORNADO_ARTIFACT)
    t_factory = w3.eth.contract(abi=tornado_abi, bytecode=tornado_bytecode)
    tornado_addr = _send(
        w3, t_factory.constructor(verifier_addr, mimc.address, MERKLE_DEPTH),
        deployer, deployer_key, gas=10_000_000,
    ).contractAddress
    return w3.eth.contract(address=tornado_addr, abi=tornado_abi)


# --- chain trace extraction ---------------------------------------------


def _jsonable(v: Any) -> Any:
    """Coerce web3 / bytes / huge-int values into JSON-safe types."""
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()
    if hasattr(v, "hex") and not isinstance(v, (str, int)):
        try:
            return v.hex()
        except Exception:   # noqa: BLE001
            pass
    if isinstance(v, int) and abs(v) > 2**53:
        return str(v)   # avoid JS-side int overflow in JSON consumers
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _decode_event(log, known_contracts: dict[str, Any]) -> dict | None:
    """Try to decode a log against each known contract; return event dict or None.

    Walks the contract ABI directly (more stable across web3.py versions
    than iterating `contract.events`, which has different semantics in v6
    vs v7). For each event ABI entry, tries `contract.events.<Name>()
    .process_log(log)`; on the first match returns the decoded fields.
    """
    log_addr = log.address.lower()
    for label, contract in known_contracts.items():
        if contract is None or contract.address.lower() != log_addr:
            continue
        for abi_item in contract.abi:
            if abi_item.get("type") != "event":
                continue
            event_name = abi_item.get("name")
            if not event_name:
                continue
            try:
                event_obj = getattr(contract.events, event_name)()
                ev = event_obj.process_log(log)
            except Exception:   # noqa: BLE001 — wrong event ABI or anon event
                continue
            return {
                "contract": label,
                "event": ev["event"],
                "args": _jsonable(dict(ev["args"])),
            }
    return None


def extract_chain_trace(
    w3, end_block: int, known_contracts: dict[str, Any],
) -> list[dict]:
    """Walk every tx in blocks [0, end_block]; emit one record per tx.

    Each record: tx_hash, block, from, to, value (wei + eth), gas_used,
    status, and a `events` list of decoded logs matching the known contracts.
    """
    trace: list[dict] = []
    for block_num in range(end_block + 1):
        block = w3.eth.get_block(block_num, full_transactions=True)
        for tx in block.transactions:
            try:
                receipt = w3.eth.get_transaction_receipt(tx.hash)
            except Exception:   # noqa: BLE001 — should never happen on local Anvil
                continue
            events = []
            for log in receipt.logs:
                ev = _decode_event(log, known_contracts)
                if ev is not None:
                    events.append(ev)
            trace.append({
                "tx_hash": tx.hash.hex(),
                "block": block_num,
                "from": tx["from"],
                "to": tx.to,
                "value_wei": str(tx.value),   # str: avoid 53-bit JSON int limit
                "value_eth": tx.value / 10**18,
                "gas_used": receipt.gasUsed,
                "status": receipt.status,
                "events": events,
            })
    return trace


# --- main flow ----------------------------------------------------------


def run_campaign(args, scenario: Scenario) -> tuple[Any, Path]:
    """Set up the chain, run the campaign, dump artifacts. Returns (result, out_dir)."""
    amount = args.amount if args.amount is not None else scenario.default_amount
    seed = args.seed if args.seed is not None else random.randint(1, 10**9)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    run_name = f"{timestamp}_{scenario.name}_seed{seed}"
    out_dir = Path(args.out) / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[runner] starting {run_name} → {out_dir}", file=sys.stderr)
    start_wall = time.time()

    num_clean_exits = (
        args.num_clean_exits if args.num_clean_exits is not None
        else scenario.default_num_clean_exits
    )

    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        # N labeled clean exit wallets — Anvil starts with 10 funded
        # accounts; we take 2..2+N for the exit set. These are the
        # off-ramp destinations the agent fans out to; the detector's
        # job later is to identify them as belonging to the campaign.
        if num_clean_exits < 1 or 2 + num_clean_exits > len(node.accounts):
            raise ValueError(
                f"num_clean_exits={num_clean_exits} out of range "
                f"[1, {len(node.accounts) - 2}]"
            )
        clean_exits = list(node.accounts[2:2 + num_clean_exits])

        usdt = _deploy_usdt(w3, deployer, deployer_key)
        pool = _deploy_pool(w3, deployer, deployer_key, usdt) if scenario.needs_pool else None
        tornado = _deploy_tornado(w3, deployer, deployer_key) if scenario.needs_tornado else None

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt,
            wallets={deployer: deployer_key, alice: alice_key},
            pool_contract=pool, tornado_contract=tornado,
        )

        deploy_end_block = w3.eth.block_number
        bootstrap_attacker_addrs = sorted(dispatcher.wallets.keys())
        print(
            f"[runner] chain ready (deploy ended at block {deploy_end_block}), "
            f"launching campaign...",
            file=sys.stderr,
        )

        coordinator = Coordinator(
            LLMClient(), dispatcher,
            model=args.model, sub_agent_model=args.model,
            max_iterations=args.max_iterations,
            sub_agent_max_iterations=args.sub_agent_max_iterations,
        )

        prompt = scenario.format_prompt(
            alice=alice, clean_exits=clean_exits, amount=amount,
        )
        result = coordinator.run(
            prompt,
            max_tokens=args.max_tokens,
            sub_agent_max_tokens=args.max_tokens,
        )

        campaign_end_block = w3.eth.block_number
        wall_clock = time.time() - start_wall
        print(
            f"[runner] campaign done in {wall_clock:.1f}s, "
            f"stop={result.stopped_reason}, cost=${result.cost_usd:.4f}, "
            f"end block={campaign_end_block}",
            file=sys.stderr,
        )

        # Every wallet in the dispatcher's registry at end-of-run is
        # attacker-controlled (each was either bootstrapped or generated
        # by a tool the attacker called).
        all_attacker_addrs = sorted(dispatcher.wallets.keys())
        new_burner_addrs = sorted(
            set(all_attacker_addrs) - set(bootstrap_attacker_addrs)
        )

        # Post-run: record which clean exits actually received value, plus
        # the per-exit deltas. The detector trains on (chain_trace +
        # addresses); knowing which exits the agent USED vs which it
        # ignored is part of the labeled ground truth.
        exits_eth_after = {a: w3.eth.get_balance(a) for a in clean_exits}
        exits_usdt_after = (
            {a: usdt.functions.balanceOf(a).call() for a in clean_exits}
            if usdt is not None else {}
        )
        clean_exit_records = [
            {
                "address": addr,
                "eth_received": exits_eth_after[addr] / 10**18,
                "usdt_received": exits_usdt_after.get(addr, 0) / 10**6,
            }
            for addr in clean_exits
        ]

        addresses = {
            "attacker_wallets": all_attacker_addrs,
            "bootstrap_attackers": bootstrap_attacker_addrs,
            "burners_generated_during_campaign": new_burner_addrs,
            "source_wallet": alice,
            "clean_exit_wallets": clean_exits,   # all labeled exits, in order
            "clean_exits_funded": [
                r["address"] for r in clean_exit_records
                if r["eth_received"] > 0 or r["usdt_received"] > 0
            ],
            "clean_exit_per_address": clean_exit_records,
            "operator_wallet": deployer,
            "contracts": {
                "usdt": usdt.address,
                "pool": pool.address if pool is not None else None,
                "tornado": tornado.address if tornado is not None else None,
            },
        }

        print("[runner] extracting chain trace...", file=sys.stderr)
        trace = extract_chain_trace(
            w3, campaign_end_block,
            known_contracts={"usdt": usdt, "pool": pool, "tornado": tornado},
        )
        print(f"[runner] {len(trace)} txs traced", file=sys.stderr)

        # --- write artifacts ---
        meta = {
            "run_name": run_name,
            "scenario": scenario.name,
            "asset": scenario.asset,
            "amount": amount,
            "seed": seed,
            "model": args.model,
            "timestamp_utc": timestamp,
            "wall_clock_seconds": wall_clock,
            "anvil_chain_id": w3.eth.chain_id,
            "deploy_end_block": deploy_end_block,
            "campaign_end_block": campaign_end_block,
            "args": vars(args),
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
        (out_dir / "addresses.json").write_text(json.dumps(addresses, indent=2))

        campaign_dict = {
            "successful": result.successful,
            "stopped_reason": result.stopped_reason,
            "iterations": result.iterations,
            "cost_usd": result.cost_usd,
            "total_tool_calls": result.total_tool_calls,
            "final_text": result.final_text,
            "delegations": result.delegations,
            "sub_agent_runs": [
                {
                    "name": getattr(r, "name", None),
                    "status": r.status,
                    "summary": r.summary,
                    "key_facts": _jsonable(r.key_facts),
                    "tool_calls": _jsonable(r.tool_calls),
                    "iterations": r.iterations,
                    "cost_usd": r.cost_usd,
                }
                for r in result.sub_agent_runs
            ],
        }
        (out_dir / "campaign.json").write_text(
            json.dumps(campaign_dict, indent=2, default=str)
        )

        with (out_dir / "chain_trace.jsonl").open("w") as f:
            for record in trace:
                f.write(json.dumps(record, default=str) + "\n")

        funded_exit_count = len(addresses["clean_exits_funded"])
        total_to_exits_eth = sum(r["eth_received"] for r in clean_exit_records)
        total_to_exits_usdt = sum(r["usdt_received"] for r in clean_exit_records)

        summary_lines = [
            f"Run:         {run_name}",
            f"Scenario:    {scenario.name} — {scenario.description}",
            f"Stolen:      {amount} {scenario.asset.upper()}",
            f"Source:      {alice}",
            f"Clean exits: {len(clean_exits)} labeled "
            f"({funded_exit_count} actually received funds)",
            "",
            f"Result:      {'SUCCESS' if result.successful else 'INCOMPLETE'} "
            f"({result.stopped_reason})",
            f"Coordinator iterations: {result.iterations}",
            f"Sub-agent runs:         {len(result.sub_agent_runs)}",
            f"Chain tool calls:       {result.total_tool_calls}",
            f"Total chain txs:        {len(trace)}",
            f"Burners generated:      {len(new_burner_addrs)}",
            f"To clean exits:         {total_to_exits_eth:.4f} ETH + "
            f"{total_to_exits_usdt:.2f} USDT",
            f"Cost:        ${result.cost_usd:.4f}",
            f"Wall clock:  {wall_clock:.1f}s",
            "",
            "Delegations:",
        ]
        for d in result.delegations:
            summary_lines.append(f"  - {d['role']:12s} → {d['status']}")
        summary_lines += ["", "Per clean exit:"]
        for r in clean_exit_records:
            funded = (r["eth_received"] > 0 or r["usdt_received"] > 0)
            marker = "✓" if funded else "·"
            summary_lines.append(
                f"  {marker} {r['address']}  "
                f"{r['eth_received']:.4f} ETH  {r['usdt_received']:.2f} USDT"
            )
        summary_lines += ["", f"Artifacts: {out_dir}"]
        summary_text = "\n".join(summary_lines)
        (out_dir / "summary.txt").write_text(summary_text + "\n")

        print("\n" + "-" * 60, file=sys.stderr)
        print(summary_text, file=sys.stderr)

        return result, out_dir


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="aml.attackers.run_campaign",
        description=(
            "Run an attacker laundering campaign on a fresh Anvil chain and "
            "dump labeled artifacts (chain trace + addresses + campaign "
            "transcript) for downstream detector training."
        ),
    )
    ap.add_argument(
        "--scenario", choices=list(SCENARIOS.keys()),
        help="Which laundering typology to run (see --list-scenarios).",
    )
    ap.add_argument(
        "--amount", type=float, default=None,
        help="Amount of the asset to launder. Default: scenario's default_amount.",
    )
    ap.add_argument(
        "--seed", type=int, default=None,
        help="RNG seed for reproducibility. Default: random.",
    )
    ap.add_argument(
        "--model", default="haiku",
        help=(
            "Anthropic model alias for both Coordinator and sub-agents "
            "(default: haiku — cheapest, fastest)."
        ),
    )
    ap.add_argument(
        "--out", type=str, default="runs",
        help="Output directory; each run gets a timestamped subdirectory (default: runs/).",
    )
    ap.add_argument(
        "--max-iterations", type=int, default=12,
        help="Coordinator delegate-loop cap (default: 12).",
    )
    ap.add_argument(
        "--sub-agent-max-iterations", type=int, default=20,
        help="Per sub-agent tool-loop cap (default: 20).",
    )
    ap.add_argument(
        "--max-tokens", type=int, default=2048,
        help="Max tokens per LLM completion (default: 2048).",
    )
    ap.add_argument(
        "--num-clean-exits", type=int, default=None,
        help=(
            "Number of labeled clean exit wallets the agent can fan out "
            "to (default: scenario.default_num_clean_exits). Real "
            "launderers diversify off-ramps across many accounts."
        ),
    )
    ap.add_argument(
        "--list-scenarios", action="store_true",
        help="Print available scenarios with descriptions and exit.",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.list_scenarios:
        for name, s in SCENARIOS.items():
            print(f"{name}")
            print(f"  asset:   {s.asset}, default amount: {s.default_amount}")
            print(f"  needs:   pool={s.needs_pool}, tornado={s.needs_tornado}")
            print(f"  {s.description}")
            print()
        return 0

    if args.scenario is None:
        print("error: --scenario is required (or pass --list-scenarios)", file=sys.stderr)
        return 2

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "error: ANTHROPIC_API_KEY not set. Source your .env or export it first.",
            file=sys.stderr,
        )
        return 2

    scenario = SCENARIOS[args.scenario]
    result, _ = run_campaign(args, scenario)
    return 0 if result.successful else 1


if __name__ == "__main__":
    sys.exit(main())
