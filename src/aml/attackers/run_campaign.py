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
from aml.attackers.funder_sizing import allocate_funder_amounts
from aml.attackers.scenarios import SCENARIOS, Scenario
from aml.chains import AnvilNode
from aml.chains.eth_stack import deploy_pool, deploy_tornado, deploy_usdt
from aml.chains.trace import extract_chain_trace, jsonable
from aml.env import PriceOracle, build_market_context, resolve_campaign_ts


_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRICE_CACHE = _REPO_ROOT / "data" / "prices"


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

    oracle = PriceOracle(cache_dir=_PRICE_CACHE)
    campaign_ts = resolve_campaign_ts(oracle, getattr(args, "campaign_ts", None))
    print(
        f"[runner] oracle: campaign_ts={campaign_ts.isoformat()} "
        f"eth=${oracle.price('eth', campaign_ts):,.2f} "
        f"usdt=${oracle.price('usdt', campaign_ts):.4f}",
        file=sys.stderr,
    )

    # Funder-pool sizing: tier-based lookup (aml.attackers.funder_sizing)
    # picks count and per-funder amounts from --amount. Legacy override:
    # --funder-eth + --num-funders (matches pre-2026-08-14 tests).
    if args.funder_eth is not None:
        funder_amounts = [args.funder_eth] * args.num_funders
    else:
        funder_amounts = allocate_funder_amounts(amount)
    funder_pool_total_eth = sum(funder_amounts)
    if funder_amounts:
        print(
            f"[runner] funder pool: {len(funder_amounts)} wallets, "
            f"{funder_pool_total_eth:.4f} ETH total "
            f"(${funder_pool_total_eth * oracle.price('eth', campaign_ts):,.2f}), "
            f"per-funder [{min(funder_amounts):.4f} .. "
            f"{max(funder_amounts):.4f}]",
            file=sys.stderr,
        )
        print(
            f"[runner] funder amounts: "
            f"{['%.4f' % x for x in funder_amounts]}",
            file=sys.stderr,
        )

    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]

        # Anvil pre-funds every account with 10_000 ETH by default. Alice
        # is meant to hold EXACTLY `amount` ETH — the "stolen loot" she
        # will launder. Real hackers only have what they stole; any gas
        # they pay for onward transfers comes OUT of the loot, not from
        # magic side-funds. Without this drain, the Coordinator could
        # route more than `amount` ETH from Alice's Anvil-inherited 10K
        # into laundering and inflate the recovery metric past 100%.
        #
        # Alice's own tx gas will come from `amount` (the tools respect
        # a reserve_eth=0.01 default so she never bricks herself; the
        # last-mile drain can pass reserve_eth=0 to sweep the residual).
        alice_target_wei = int(amount * 10**18)
        alice_balance_wei = w3.eth.get_balance(alice)
        if alice_balance_wei > alice_target_wei:
            gas_price = w3.eth.gas_price
            drain_gas = 21_000
            drain_value = alice_balance_wei - alice_target_wei - (drain_gas * int(gas_price * 1.2))
            if drain_value > 0:
                tx = {
                    "from": alice, "to": deployer, "value": drain_value,
                    "nonce": w3.eth.get_transaction_count(alice),
                    "gas": drain_gas, "gasPrice": gas_price,
                    "chainId": w3.eth.chain_id,
                }
                from eth_account import Account
                signed = Account.sign_transaction(tx, private_key=alice_key)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                w3.eth.wait_for_transaction_receipt(tx_hash)
        print(
            f"[runner] alice normalized: "
            f"{w3.eth.get_balance(alice)/1e18:.4f} ETH "
            f"(target exact: {amount:.4f})",
            file=sys.stderr,
        )
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

        # Pass laundering_target_usd so ToolDispatcher can enforce the
        # burner-cap (moderate-professional profile: max 30-250 burners
        # scaled by amount, prevents runaway generation loops observed
        # in seeds 306/401/402).
        usd_stolen_preview = oracle.usd_value(amount, scenario.asset, campaign_ts)
        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt,
            wallets={deployer: deployer_key, alice: alice_key},
            pool_contract=pool, tornado_contract=tornado,
            laundering_target_usd=usd_stolen_preview,
        )
        # Multi-funder pool: k intermediate funders (each seeded once from
        # deployer) that then randomly fund every new burner/exit. Breaks
        # the single-source co-funding heuristic — clustering a campaign's
        # wallets now requires 2-hop analysis instead of 1-hop.
        dispatcher.bootstrap_funder_pool(amounts_eth=funder_amounts)

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

        prompt = (
            scenario.format_prompt(alice=alice, amount=amount)
            + "\n\n" + build_market_context(oracle, campaign_ts)
            + "\n\nTwo constraints when sizing legs from the USD thresholds "
            "above:\n"
            "(a) $999 is a HARD CAP per leg (sub-CTR compliance), NOT an "
            "efficiency target. The Layering system prompt requires "
            "TOPOLOGICAL VARIETY across burners (single-counterparty, "
            "hub-and-spoke, and chain-shaped mixes, with randomised amounts "
            "and edge-kind heterogeneity). MODERATE over-provisioning of "
            "burner count beyond the arithmetic minimum aids GNN evasion "
            "(target ~1.5-2× the minimum, not more) — a run that structures "
            "$10K into the theoretical minimum of ~11 burners is trivially "
            "learnable, but a run with 200+ burners for the same value is a "
            "known failure mode that inflates gas without adding evasion.\n"
            "(b) At end-of-campaign, aggressively consolidate remaining "
            "balances (dust in intermediate burners, gas-floor residues) "
            "before wrapping up. TARGET: total USD loss <5% including gas "
            "and pool slippage. Use reserve_eth=0 on final drains.\n\n"
            "The mock Uniswap pool trades at fixed 1 ETH = 2000 USDT for "
            "tractability but the FATF regime is USD, not pool-rate."
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

        # Post-campaign safety net: any wallet left holding USDT without
        # enough ETH for a transfer gets rescued via the funder pool.
        # Re-checks after rescue and surfaces any that remain — those are
        # a hard failure signal (e.g. exhausted funder pool). Result goes
        # into campaign.json as evidence the anti-strand infra worked.
        print("[runner] scanning for stranded wallets...", file=sys.stderr)
        strand_report = dispatcher.rescue_stranded_wallets()
        print(
            f"[runner] anti-strand: {strand_report['stranded_before']} found, "
            f"{strand_report['rescued']} rescued, "
            f"{strand_report['stranded_after']} still stranded",
            file=sys.stderr,
        )
        # Sweep the funder pool back to the deployer so every campaign
        # starts and ends with funders at zero. This gives the TRUE gas
        # cost as (initial pool allocated) - (amount recovered by sweep):
        # anything unrecovered went to real chain gas or seed transfers
        # the funder made into burners. Same call in Sepolia runner so
        # both chains behave identically.
        sweep_report = dispatcher.sweep_funder_pool(destination=deployer)
        gas_burned_by_funders_eth = (
            funder_pool_total_eth - sweep_report["total_returned_eth"]
        )
        print(
            f"[runner] funder sweep: {sweep_report['swept']}/"
            f"{sweep_report['num_funders']} returned "
            f"{sweep_report['total_returned_eth']:.4f} ETH to deployer, "
            f"{sweep_report['skipped']} skipped, "
            f"{len(sweep_report['failed'])} failed. "
            f"True gas burned by funder pool: "
            f"{gas_burned_by_funders_eth:.4f} ETH.",
            file=sys.stderr,
        )
        if strand_report["stranded_after"] > 0:
            print(
                f"[runner] WARNING — {strand_report['stranded_after']} wallet(s) "
                f"could not be rescued: {strand_report['stranded_addresses']}",
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
        # USD-denominated metrics via oracle. Sums per-exit ETH and USDT
        # separately (each converted at campaign_ts spot) so a run that
        # delivered everything as USDT vs. one that consolidated to ETH
        # can be compared apples-to-apples in laundered USD value.
        eth_price = oracle.price("eth", campaign_ts)
        usdt_price = oracle.price("usdt", campaign_ts)
        usd_stolen = oracle.usd_value(amount, scenario.asset, campaign_ts)
        usd_operating_capital = funder_pool_total_eth * eth_price
        usd_total_attacker_capital = usd_stolen + usd_operating_capital
        # True operating-capital cost after sweep: everything unrecovered
        # was consumed by chain gas + seeds the funder made into burners.
        # This is the "gas overhead" the campaign really paid on top of
        # the pool-slippage / consolidation losses tracked separately.
        usd_gas_burned_by_funders = gas_burned_by_funders_eth * eth_price
        usd_to_exits = sum(
            r["eth_received"] * eth_price + r["usdt_received"] * usdt_price
            for r in clean_exit_records
        )
        # Dual metric: recovery_pct_of_stolen can exceed 100% if the
        # Coordinator sweeps operating capital into exits (accounting
        # artefact, not laundering efficiency). recovery_pct_of_capital
        # is bounded ≤100% and answers "what fraction of everything the
        # attacker started with ended up at clean exits" — the honest
        # comparability metric across runs with different funder pools.
        recovery_pct_of_stolen = (100.0 * usd_to_exits / usd_stolen) if usd_stolen else 0.0
        recovery_pct_of_capital = (
            100.0 * usd_to_exits / usd_total_attacker_capital
            if usd_total_attacker_capital else 0.0
        )

        # HONEST recovery + ETH reconciliation — same shape as
        # run_sepolia_campaign.py. See there for full docstring; on
        # Anvil the pool distortion is much smaller (fresh pool per
        # run) but the metric is reported for cross-run consistency.
        eth_swapped_into_pool = 0.0
        for r in result.sub_agent_runs:
            for tc in r.tool_calls:
                if tc.get("name") != "swap_eth_for_usdt":
                    continue
                out = tc.get("output") or {}
                if isinstance(out, dict) and not tc.get("is_error"):
                    eth_swapped_into_pool += float(out.get("eth_paid") or 0)
        eth_at_exits = sum(r["eth_received"] for r in clean_exit_records)
        usdt_at_exits = sum(r["usdt_received"] for r in clean_exit_records)
        usdt_as_eth_market = (usdt_at_exits * usdt_price / eth_price
                              if eth_price else 0.0)
        usdt_as_eth_capped = min(usdt_as_eth_market, eth_swapped_into_pool)
        honest_recovery_eth = eth_at_exits + usdt_as_eth_capped
        honest_recovery_usd = honest_recovery_eth * eth_price
        honest_recovery_pct = (100.0 * honest_recovery_usd / usd_stolen
                               if usd_stolen else 0.0)
        honest_recovery_pct_capped = min(honest_recovery_pct, 100.0)

        # Full on-chain ETH reconciliation.
        def _bal(a: str) -> float:
            try: return w3.eth.get_balance(a) / 1e18
            except Exception: return 0.0
        reconc_alice_in = float(amount)   # on Anvil alice is drained to amount
        reconc_alice_now = _bal(alice)
        reconc_exits_eth_now = sum(_bal(a) for a in clean_exit_addrs)
        campaign_wallets = set(dispatcher.wallets.keys())
        campaign_wallets.discard(deployer)
        campaign_wallets.discard(alice)
        for a in clean_exit_addrs:
            campaign_wallets.discard(a)
        reconc_burners_eth_now = sum(_bal(a) for a in campaign_wallets)
        reconc_residual = (
            reconc_alice_in - reconc_alice_now - reconc_exits_eth_now
            - reconc_burners_eth_now - honest_recovery_eth
        )

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
            "anti_strand": strand_report,
            "funder_pool": {
                "num_funders": len(funder_amounts),
                "amounts_eth": funder_amounts,
                "total_eth": funder_pool_total_eth,
                "swept_back_eth": sweep_report["total_returned_eth"],
                "refill_events": dispatcher._funder_refill_events,
                "gas_burned_eth": gas_burned_by_funders_eth,
            },
            "oracle": {
                "campaign_ts_iso": campaign_ts.isoformat(),
                "eth_usd": eth_price,
                "usdt_usd": usdt_price,
                "trx_usd": oracle.price("trx", campaign_ts),
                "usd_stolen": usd_stolen,
                "usd_operating_capital": usd_operating_capital,
                "usd_total_attacker_capital": usd_total_attacker_capital,
                "usd_gas_burned_by_funders": usd_gas_burned_by_funders,
                "usd_to_clean_exits": usd_to_exits,
                "recovery_pct_of_stolen": recovery_pct_of_stolen,
                "recovery_pct_of_capital": recovery_pct_of_capital,
                # Honest recovery (mock-pool-adjusted, market-priced, capped)
                "honest_recovery": {
                    "eth_swapped_into_pool": eth_swapped_into_pool,
                    "eth_at_exits_direct": eth_at_exits,
                    "usdt_at_exits_raw": usdt_at_exits,
                    "usdt_as_eth_market_rate": usdt_as_eth_market,
                    "usdt_as_eth_capped_by_swap_input": usdt_as_eth_capped,
                    "honest_recovery_eth": honest_recovery_eth,
                    "honest_recovery_usd": honest_recovery_usd,
                    "honest_recovery_pct": honest_recovery_pct,
                },
                # Full on-chain reconciliation
                "eth_reconciliation": {
                    "alice_funded_in": reconc_alice_in,
                    "alice_balance_now": reconc_alice_now,
                    "exits_eth_now": reconc_exits_eth_now,
                    "burners_eth_now_recoverable": reconc_burners_eth_now,
                    "honest_recovery_eth": honest_recovery_eth,
                    "residual_eth_locked_or_burned": reconc_residual,
                },
            },
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
            f"Stolen (USD equiv):     ${usd_stolen:,.2f}  "
            f"(@ {campaign_ts.strftime('%Y-%m-%d')}: ETH=${eth_price:,.2f})",
            f"Operating capital:      ${usd_operating_capital:,.2f}  "
            f"(funder pool {funder_pool_total_eth:.4f} ETH)",
            f"Total attacker capital: ${usd_total_attacker_capital:,.2f}",
            f"To exits (USD equiv):   ${usd_to_exits:,.2f}",
            f"  Recovery of stolen:   {recovery_pct_of_stolen:.1f}%  "
            f"(>100% = swept operating capital)",
            f"  Recovery of capital:  {recovery_pct_of_capital:.1f}%  "
            f"(bounded ≤100%, comparability metric)",
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
        "--funder-eth", type=float, default=None,
        help=(
            "Legacy override: fixed ETH per funder (all identical, count "
            "from --num-funders). Kept for backward compat with pre-"
            "2026-08-14 runs. Default None → tier-based sizing from "
            "aml.attackers.funder_sizing (recommended)."
        ),
    )
    ap.add_argument(
        "--campaign-ts", type=str, default=None,
        help=(
            "Freeze the price oracle to a specific date (YYYY-MM-DD or "
            "ISO 8601). Anvil runs default to now(UTC) clamped to the "
            "last cached day, which is fine for one-offs but non-"
            "reproducible across days — pass this to pin campaigns in "
            "the same market regime when comparing seeds. Sepolia runs "
            "always ignore this and use live now(UTC)."
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
