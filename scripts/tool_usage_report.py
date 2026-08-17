"""Reusable tool-usage analysis for any attacker campaign run.

Parses `campaign.json` from a run directory and reports:
  - Global tool count + error breakdown
  - Per-sub-agent-run breakdown (with FATF role from delegations)
  - List of unused tools + why they might not have been called
  - Cost + iteration stats

Usage:
    python scripts/tool_usage_report.py <run_dir>
    python scripts/tool_usage_report.py results/anvil/2026-08-16T20-20-49_defi-exploit_seed403
    python scripts/tool_usage_report.py results/sepolia_campaign/2026-08-11T18-02-38_defi-exploit_seed100_sepolia
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


# All 19 tools exposed to the attacker LLM
ALL_TOOLS: dict[str, str] = {
    "get_balance": "consulta balance ETH/USDT",
    "get_gas_budget": "estima cuántas tx aguanta la wallet",
    "get_swap_quote": "cotización swap sin ejecutar",
    "inspect_chain": "audit read-only con contadores por tipo evento",
    "transfer_usdt": "transferencia ERC-20 estándar",
    "transfer_eth": "transferencia ETH nativa con reserve gas",
    "mint_usdt": "mint permissionless USDT mock",
    "generate_burner_wallet": "crea nueva EOA + fondea 0.005 ETH gas",
    "register_clean_exit": "genera+registra address etiquetada [Platform_N]",
    "smurf_split": "batched: N transferencias USDT en 1 llamada",
    "smurf_eth_split": "batched: N transferencias ETH en 1 llamada",
    "peel_chain": "peel chain clásica en 1 llamada",
    "swap_eth_for_usdt": "swap Uniswap ETH→USDT",
    "swap_usdt_for_eth": "swap Uniswap USDT→ETH",
    "advance_blocks": "Anvil skip N bloques (Sepolia no-op)",
    "mixer_deposit": "deposit 1 ETH Tornado, devuelve note",
    "mixer_withdraw": "genera prueba Groth16 + retira 1 ETH",
    "mixer_batch_deposit": "batched: N deposits Tornado en 1 llamada",
    "mixer_batch_withdraw": "batched: N withdraws Tornado en 1 llamada",
}


def analyse(run_dir: Path) -> dict:
    campaign = json.loads((run_dir / "campaign.json").read_text())

    tool_counts: Counter[str] = Counter()
    tool_errors: Counter[str] = Counter()
    error_samples: dict[str, list[str]] = {}
    per_run: list[dict] = []

    delegations = campaign.get("delegations", [])
    sub_runs = campaign.get("sub_agent_runs", [])

    for idx, sub in enumerate(sub_runs):
        role = delegations[idx]["role"] if idx < len(delegations) else "unknown"
        status = delegations[idx]["status"] if idx < len(delegations) else "unknown"
        this_run_tools: Counter[str] = Counter()
        for tc in sub.get("tool_calls", []):
            name = tc["name"]
            tool_counts[name] += 1
            this_run_tools[name] += 1
            if tc.get("is_error"):
                tool_errors[name] += 1
                err_msg = tc.get("error") or ""
                if len(error_samples.setdefault(name, [])) < 3:
                    error_samples[name].append(str(err_msg)[:200])
        per_run.append({
            "run_idx": idx + 1,
            "role": role,
            "status": status,
            "tool_calls": sum(this_run_tools.values()),
            "iterations": sub.get("iterations", 0),
            "cost_usd": sub.get("cost_usd", 0.0),
            "tools": dict(this_run_tools),
        })

    unused = [t for t in ALL_TOOLS if t not in tool_counts]

    return {
        "run_dir": str(run_dir),
        "total_calls": sum(tool_counts.values()),
        "coordinator_iterations": campaign.get("iterations", 0),
        "total_cost_usd": campaign.get("cost_usd", 0.0),
        "successful": campaign.get("successful", False),
        "tool_counts": dict(tool_counts),
        "tool_errors": dict(tool_errors),
        "error_samples": error_samples,
        "per_run": per_run,
        "unused_tools": unused,
    }


def print_report(rep: dict) -> None:
    total = rep["total_calls"]
    print(f"=== TOOL USAGE — {Path(rep['run_dir']).name} ===")
    print(f"    Success: {rep['successful']}  "
          f"Coord iterations: {rep['coordinator_iterations']}  "
          f"Total cost: ${rep['total_cost_usd']:.4f}")
    print(f"    Total tool calls: {total}")
    print()
    print(f"{'Tool':<28} {'Count':>6} {'%':>7} {'Err':>6}")
    print("-" * 55)
    for name, count in sorted(
        rep["tool_counts"].items(), key=lambda x: -x[1]
    ):
        pct = 100.0 * count / total if total else 0
        err = rep["tool_errors"].get(name, 0)
        print(f"{name:<28} {count:>6} {pct:>6.1f}% {err:>6}")
    print()
    print("--- BREAKDOWN por sub-agent run ---")
    for r in rep["per_run"]:
        print(f"\n[Run {r['run_idx']}] role={r['role']:<12} "
              f"status={r['status']:<10} "
              f"{r['tool_calls']} tool calls | "
              f"iter={r['iterations']} | cost=${r['cost_usd']:.4f}")
        for name, c in sorted(r["tools"].items(), key=lambda x: -x[1]):
            print(f"    {c:>3}  {name}")

    print()
    print("--- TOOLS NO USADAS EN ESTA CAMPAÑA ---")
    for name in rep["unused_tools"]:
        print(f"  · {name:<28}  ({ALL_TOOLS[name]})")

    if rep["error_samples"]:
        print()
        print("--- MUESTRAS DE ERRORES ---")
        for name, samples in rep["error_samples"].items():
            print(f"\n  {name} ({rep['tool_errors'][name]} errores):")
            for s in samples:
                print(f"    · {s}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path,
                        help="Path to campaign run directory")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON instead of human-readable table")
    args = parser.parse_args()

    if not (args.run_dir / "campaign.json").exists():
        raise SystemExit(f"missing {args.run_dir}/campaign.json")

    rep = analyse(args.run_dir)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print_report(rep)


if __name__ == "__main__":
    main()
