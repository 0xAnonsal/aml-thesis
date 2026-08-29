"""Compare Sepolia campaign runs — dump a summary table.

Reads every results/sepolia_campaign/<run_dir>/ that has a
coordinator_checkpoint.json and produces a cross-run table with
delivered_pct, cost, duration, tool_call error rate, and mixer stats.

Useful for:
  * TFM §8.9 empirical trends (seed 500 → 601 → 602 → 700 progression)
  * Ad-hoc regression detection between two consecutive runs
  * Picking the best-performing seed as the "hero" for the TFM

Usage:
  python scripts/campaign_comparison.py
  python scripts/campaign_comparison.py --seeds 601 602 603
  python scripts/campaign_comparison.py --csv results/comparison.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "results" / "sepolia_campaign"


def _parse_seed(run_dir_name: str) -> int | None:
    m = re.search(r"seed(\d+)", run_dir_name)
    return int(m.group(1)) if m else None


def _stats_from_run(run_dir: Path) -> dict | None:
    ckpt = run_dir / "coordinator_checkpoint.json"
    if not ckpt.exists():
        return None
    try:
        d = json.load(ckpt.open())
    except (OSError, json.JSONDecodeError):
        return None

    seed = _parse_seed(run_dir.name)
    tool_stats = Counter()
    err_stats = Counter()
    for run in d.get("sub_agent_runs_raw", []) or []:
        for tc in run.get("tool_calls", []) or []:
            tool_stats[tc["name"]] += 1
            if tc.get("is_error"):
                err_stats[tc["name"]] += 1

    # Mixer stats
    mixer_notes = run_dir / "mixer_notes.jsonl"
    n_notes_unique = 0
    n_notes_confirmed = 0
    if mixer_notes.exists():
        seen = set()
        for line in mixer_notes.open():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            note = entry.get("note")
            if note and note not in seen:
                seen.add(note)
                n_notes_unique += 1
            if entry.get("status") == "confirmed":
                n_notes_confirmed += 1

    total_tools = sum(tool_stats.values())
    total_errs = sum(err_stats.values())
    err_rate = 100.0 * total_errs / total_tools if total_tools else 0

    delegations = d.get("delegations", []) or []
    delg_status = Counter(dg.get("status", "?") for dg in delegations)

    return {
        "run_dir": run_dir.name,
        "seed": seed,
        "iterations": d.get("iteration"),
        "cost_usd": d.get("cost_usd", 0),
        "stop_reason": d.get("stopped_reason", "?"),
        "delegations": len(delegations),
        "delg_success": delg_status.get("success", 0),
        "delg_incomplete": delg_status.get("incomplete", 0),
        "delg_failed": delg_status.get("failed", 0),
        "tool_calls": total_tools,
        "tool_errors": total_errs,
        "err_rate_pct": round(err_rate, 2),
        "mixer_deposits_unique": n_notes_unique,
        "mixer_deposits_confirmed": n_notes_confirmed,
        "mixer_deposit_calls": tool_stats.get("mixer_deposit", 0),
        "mixer_deposit_errors": err_stats.get("mixer_deposit", 0),
        "mixer_withdraw_calls": tool_stats.get("mixer_withdraw", 0),
        "mixer_withdraw_errors": err_stats.get("mixer_withdraw", 0),
        "clean_exits_registered": tool_stats.get("register_clean_exit", 0),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", nargs="*", type=int,
                   help="Filter to specific seeds (e.g. --seeds 601 602)")
    p.add_argument("--csv", type=Path,
                   help="Also write comparison to a CSV file")
    p.add_argument("--limit", type=int, default=20,
                   help="Max runs to show (default 20 latest)")
    args = p.parse_args()

    if not RUNS.exists():
        print(f"No results dir: {RUNS}", file=sys.stderr)
        return 1

    rows: list[dict] = []
    for run_dir in sorted(RUNS.iterdir()):
        if not run_dir.is_dir():
            continue
        stats = _stats_from_run(run_dir)
        if stats is None:
            continue
        if args.seeds and stats["seed"] not in args.seeds:
            continue
        rows.append(stats)

    rows.sort(key=lambda r: (r["seed"] or 0, r["run_dir"]))
    if args.limit and len(rows) > args.limit:
        rows = rows[-args.limit:]

    if not rows:
        print("No matching runs.")
        return 0

    # Compact table (fits in a terminal)
    cols = [
        ("seed", 4),
        ("iters", 5),
        ("cost", 7),
        ("stop", 15),
        ("delg", 4),
        ("s/i/f", 7),
        ("tools", 6),
        ("err%", 5),
        ("dep u/c", 8),
        ("dep OK/err", 11),
        ("wdr OK/err", 11),
        ("exits", 6),
    ]
    header = "  ".join(f"{c:<{w}}" for c, w in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        cells = [
            f"{r['seed'] or '?':<4}",
            f"{r['iterations'] or '?':<5}",
            f"${r['cost_usd']:.2f}",
            f"{r['stop_reason'][:15]:<15}",
            f"{r['delegations']:<4}",
            f"{r['delg_success']}/{r['delg_incomplete']}/{r['delg_failed']:<3}",
            f"{r['tool_calls']:<6}",
            f"{r['err_rate_pct']:<5}",
            f"{r['mixer_deposits_unique']}/{r['mixer_deposits_confirmed']:<6}",
            f"{r['mixer_deposit_calls'] - r['mixer_deposit_errors']}/{r['mixer_deposit_errors']:<9}",
            f"{r['mixer_withdraw_calls'] - r['mixer_withdraw_errors']}/{r['mixer_withdraw_errors']:<9}",
            f"{r['clean_exits_registered']:<6}",
        ]
        print("  ".join(cells))

    print()
    print(f"Total runs shown: {len(rows)}")

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"CSV: {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
