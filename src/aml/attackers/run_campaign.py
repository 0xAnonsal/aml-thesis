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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from web3 import Web3

from aml.attackers import Coordinator, LLMClient, ToolDispatcher
from aml.attackers.scenarios import SCENARIOS, Scenario
from aml.chains import AnvilNode
from aml.chains.eth_stack import deploy_pool, deploy_tornado, deploy_usdt
from aml.chains.trace import extract_chain_trace, jsonable


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

    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        # Clean exits are NOT pre-allocated. The Integration sub-agent
        # creates them dynamically via register_clean_exit, and we read
        # the resulting list from dispatcher.registered_clean_exits at
        # end-of-campaign. This means we can't pre-snapshot balances
        # (the exits don't exist yet) — but the wallets are also created
        # fresh during the campaign, so the natural starting balance is
        # the auto-seeded gas dust (~0.05 ETH from the faucet) plus
        # whatever USDT the agent sends them. eth_received is reported
        # as (final - gas_seed) for honesty.

        usdt = deploy_usdt(w3, deployer, deployer_key)
        pool = deploy_pool(w3, deployer, deployer_key, usdt) if scenario.needs_pool else None
        tornado = deploy_tornado(w3, deployer, deployer_key) if scenario.needs_tornado else None

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt,
            wallets={deployer: deployer_key, alice: alice_key},
            pool_contract=pool, tornado_contract=tornado,
        )
        # Multi-funder pool: k intermediate funders (each seeded once from
        # deployer) that then randomly fund every new burner/exit. Breaks
        # the single-source co-funding heuristic — clustering a campaign's
        # wallets now requires 2-hop analysis instead of 1-hop.
        dispatcher.bootstrap_funder_pool(
            num_funders=args.num_funders,
            eth_per_funder=args.funder_eth,
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

        prompt = scenario.format_prompt(alice=alice, amount=amount)
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
        # by a tool the attacker called — either generate_burner_wallet
        # or register_clean_exit).
        all_attacker_addrs = sorted(dispatcher.wallets.keys())

        # Split the new wallets into burners vs. clean exits using the
        # dispatcher's registered_clean_exits log (populated by every
        # register_clean_exit call). Anything attacker-controlled that
        # is NOT a clean exit and NOT bootstrap is a burner.
        clean_exit_entries = list(dispatcher.registered_clean_exits)
        clean_exit_addrs = [e["address"] for e in clean_exit_entries]
        clean_exit_addr_set = set(clean_exit_addrs)
        new_burner_addrs = sorted(
            set(all_attacker_addrs)
            - set(bootstrap_attacker_addrs)
            - clean_exit_addr_set
        )

        # For each registered clean exit: report final ETH (minus the
        # 0.05 gas seed so eth_received reflects only what the campaign
        # actually delivered) and USDT received. Wallets were created
        # fresh during the campaign so the only ETH they hold beyond gas
        # dust is what the agent routed in.
        from aml.attackers.tools import _DEFAULT_GAS_RESERVE_ETH  # local to avoid cycle
        gas_seed_wei = int(_DEFAULT_GAS_RESERVE_ETH * 10**18)
        clean_exit_records = []
        for entry in clean_exit_entries:
            addr = entry["address"]
            eth_final_wei = w3.eth.get_balance(addr)
            eth_received = max(0.0, (eth_final_wei - gas_seed_wei) / 10**18)
            usdt_final = (
                usdt.functions.balanceOf(addr).call()
                if usdt is not None else 0
            )
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
                    "key_facts": jsonable(r.key_facts),
                    "tool_calls": jsonable(r.tool_calls),
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

        # Per-platform aggregation for the summary.
        per_platform: dict[str, dict[str, float]] = {}
        for r in clean_exit_records:
            p = r["exchange_platform"]
            slot = per_platform.setdefault(
                p, {"count": 0, "funded": 0, "usdt": 0.0}
            )
            slot["count"] += 1
            if r["usdt_received"] > 0 or r["eth_received"] > 0:
                slot["funded"] += 1
            slot["usdt"] += r["usdt_received"]

        summary_lines = [
            f"Run:         {run_name}",
            f"Scenario:    {scenario.name} — {scenario.description}",
            f"Stolen:      {amount} {scenario.asset.upper()}",
            f"Source:      {alice}",
            f"Clean exits: {len(clean_exit_records)} created by agent "
            f"({funded_exit_count} received funds) across "
            f"{len(per_platform)} platforms",
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

        summary_lines += ["", "Per platform:"]
        for p, slot in sorted(per_platform.items()):
            summary_lines.append(
                f"  {p:12s} {int(slot['funded'])}/{int(slot['count'])} funded  "
                f"{slot['usdt']:.2f} USDT total"
            )

        summary_lines += ["", "Per clean exit:"]
        for r in clean_exit_records:
            funded = (r["eth_received"] > 0 or r["usdt_received"] > 0)
            marker = "✓" if funded else "·"
            summary_lines.append(
                f"  {marker} {r['address']}  [{r['exchange_platform']}]  "
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
        "--max-iterations", type=int, default=15,
        help=(
            "Coordinator delegate-loop cap (default: 15). Bumped from "
            "12 in PR #49: with dynamic clean exits and three FATF "
            "scenarios at varying scale, the Coordinator legitimately "
            "needs more delegations (especially when verifying with "
            "inspect_chain and re-delegating Integration for unfunded "
            "exits)."
        ),
    )
    ap.add_argument(
        "--sub-agent-max-iterations", type=int, default=40,
        help=(
            "Per sub-agent tool-loop cap (default: 40). Bumped from "
            "20 in PR #49: stablecoin-scam (8000 USDT) and ransomware-"
            "cashout (5 ETH) need Integration to do "
            "register_clean_exit + transfer_usdt + get_balance for "
            "15-25 exits, which exceeds the old cap. 40 covers all "
            "current scenarios with margin; bump further per --flag "
            "for very large amounts."
        ),
    )
    ap.add_argument(
        "--max-tokens", type=int, default=4096,
        help=(
            "Max tokens per LLM completion (default: 4096). Bumped "
            "from 2048 in PR #49: the Coordinator's per-turn response "
            "with trifurcated Layering + dynamic exit planning + "
            "inspect_chain audit reads regularly exceeded the old cap, "
            "producing premature stop=max_tokens."
        ),
    )
    ap.add_argument(
        "--num-funders", type=int, default=5,
        help=(
            "Size of the intermediate funder pool (default: 5). Each "
            "funder is seeded once from the deployer with --funder-eth "
            "and then randomly picked to fund every new burner/exit. "
            "Set to 0 to disable and use the deployer directly (creates "
            "single-source co-funding signal — matches pre-2026-08-13 "
            "behaviour)."
        ),
    )
    ap.add_argument(
        "--funder-eth", type=float, default=1.0,
        help=(
            "ETH bootstrapped into each funder wallet (default: 1.0). "
            "Must be enough to cover the total gas dust seeded across "
            "all burners/exits/top-ups routed through that funder. On "
            "Anvil the deployer starts with 10k ETH so 1.0 is plenty."
        ),
    )
    ap.add_argument(
        "--list-scenarios", action="store_true",
        help="Print available scenarios with descriptions and exit.",
    )
    return ap


def _load_env_if_available() -> None:
    """Load .env into os.environ using the shared stdlib parser.

    Delegates to aml.utils.env.load_dotenv_if_present so this CLI
    parses .env the same way pytest's conftest.py does. No dependency
    on python-dotenv. Silent no-op when no .env is present.
    """
    from aml.utils.env import load_dotenv_if_present
    load_dotenv_if_present()


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

    # Pull ANTHROPIC_API_KEY (and anything else in .env) into env BEFORE
    # the key check, so a fresh shell + .env file just works.
    _load_env_if_available()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "error: ANTHROPIC_API_KEY not set in environment or .env. "
            "Add it to .env in the repo root or export it explicitly.",
            file=sys.stderr,
        )
        return 2

    scenario = SCENARIOS[args.scenario]
    result, _ = run_campaign(args, scenario)
    return 0 if result.successful else 1


if __name__ == "__main__":
    sys.exit(main())
