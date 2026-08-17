"""Adapter: EthereumHeist dataset (Wu et al. 2023) → aml-thesis pipeline.

Loads the 24 real Ethereum heists (Bancor, Bitmart, UpbitHack/Lazarus,
PolyNetwork, PlusTokenPonzi, etc.) into a single labeled MultiDiGraph
compatible with the detector code in `aml.detectors.*`.

Source: git clone https://github.com/lindan113/EthereumHeist +
`EthereumHeist_open.zip` from the paper's Dropbox (extracted into
~/aml-data/EthereumHeist_data/).

Each hack subfolder contains:
  - all-address.csv    (address, name_tag, label)
  - all-tx.csv         (hash, from, to, value, timeStamp, blockNumber,
                        tokenSymbol, contractAddress, isError, gasPrice, gasUsed)
  - accounts-hacker.csv (subset of all-address filtered to confirmed hackers)

Label semantics (per paper):
  - "heist"    → confirmed laundering account (attacker or their transit)
  - ""         → service provider / benign address (empty label field)
  - "unknown"  → same as empty in practice
  name_tag "ml_transit_0" = FATF placement stage
  name_tag "ml_transit_X" (X>0) = FATF layering stage (X = hop number)

Each hack becomes a "campaign" (analogue of aml.detectors.dataset RunData),
which enables Leave-One-Campaign-Out cross-validation and per-hack analysis.

Output: `data/ethereum_heist_combined.pkl` — pickled dict with:
  {
    "graph": nx.MultiDiGraph,      # union of all hacks
    "node_labels": dict[address→int]  # 1=heist, 0=benign
    "hack_membership": dict[address→set[str]]  # which hacks each addr in
    "per_hack_stats": dict[hack_name → {n_addr, n_edges, n_heist}]
  }

UpbitHack is 95% of the raw data (528 MB); rest of the 23 hacks combined
are ~35 MB. This is documented — no manual downsampling here so the raw
graph is reproducible; downstream code can subsample UpbitHack if training
is too slow.

Usage:
    python scripts/load_ethereum_heist.py
    python scripts/load_ethereum_heist.py --hacks UpbitHack PolyNetworkExploiter
"""
from __future__ import annotations

import argparse
import pickle
import time
from pathlib import Path

import networkx as nx
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
HEIST_ROOT = Path.home() / "aml-data" / "EthereumHeist_data"
OUT_PKL = REPO_ROOT / "data" / "ethereum_heist_combined.pkl"


def list_hacks() -> list[str]:
    """Every subfolder that has both all-address.csv and all-tx.csv."""
    hacks: list[str] = []
    for d in sorted(HEIST_ROOT.iterdir()):
        if not d.is_dir():
            continue
        if (d / "all-address.csv").exists() and (d / "all-tx.csv").exists():
            hacks.append(d.name)
    return hacks


def _norm_addr(a: str | None) -> str | None:
    """Lowercase + strip. Empty / NaN → None."""
    if a is None or not isinstance(a, str):
        return None
    a = a.strip().lower()
    if not a or a == "nan":
        return None
    return a


def load_hack(hack_name: str) -> tuple[list[dict], list[dict]]:
    """Return (address_records, tx_records) for one hack folder.

    address_records: [{address, name_tag, label}, ...]
    tx_records: [{hash, from, to, value, timeStamp, blockNumber,
                  tokenSymbol, contractAddress, isError, gas_price, gas_used}, ...]
    """
    hack_dir = HEIST_ROOT / hack_name
    addr_df = pd.read_csv(
        hack_dir / "all-address.csv", dtype=str, keep_default_na=False,
    )
    tx_df = pd.read_csv(
        hack_dir / "all-tx.csv", dtype=str, keep_default_na=False,
    )

    address_records: list[dict] = []
    for _, row in addr_df.iterrows():
        addr = _norm_addr(row.get("address"))
        if addr is None:
            continue
        address_records.append({
            "address": addr,
            "name_tag": (row.get("name_tag") or "").strip(),
            # empty label → "unknown" (per Wu et al. paper: service providers)
            "label": (row.get("label") or "unknown").strip().lower() or "unknown",
        })

    tx_records: list[dict] = []
    for _, row in tx_df.iterrows():
        from_a = _norm_addr(row.get("from"))
        to_a = _norm_addr(row.get("to"))
        if from_a is None or to_a is None:
            continue
        # Value comes as scientific string ("1e+16") — parse safely, skip
        # rows with garbled value fields instead of crashing.
        try:
            val = float(row.get("value") or 0.0)
        except (TypeError, ValueError):
            val = 0.0
        try:
            ts = int(float(row.get("timeStamp") or 0))
        except (TypeError, ValueError):
            ts = 0
        try:
            block = int(row.get("blockNumber") or 0)
        except (TypeError, ValueError):
            block = 0
        tx_records.append({
            "hash": row.get("hash", ""),
            "from": from_a,
            "to": to_a,
            "value": val,
            "timeStamp": ts,
            "blockNumber": block,
            "token": (row.get("tokenSymbol") or "").strip() or "ETH",
            "contract": _norm_addr(row.get("contractAddress")) or "",
            "is_error": (row.get("isError") or "0").strip() in ("1", "1.0", "true"),
        })

    return address_records, tx_records


def build_combined(hack_names: list[str]) -> dict:
    """Union all hacks into a single labeled graph."""
    graph = nx.MultiDiGraph()
    hack_membership: dict[str, set[str]] = {}
    address_labels_per_hack: dict[str, list[str]] = {}
    per_hack_stats: dict[str, dict] = {}

    for i, hack in enumerate(hack_names, 1):
        t0 = time.time()
        addrs, txs = load_hack(hack)

        n_heist = 0
        for rec in addrs:
            a = rec["address"]
            lab = rec["label"]
            address_labels_per_hack.setdefault(a, []).append(lab)
            hack_membership.setdefault(a, set()).add(hack)
            if not graph.has_node(a):
                graph.add_node(a, name_tag=rec["name_tag"])
            if lab == "heist":
                n_heist += 1

        # Add edges; skip failed txs (isError=1) since they don't represent
        # value actually moving between accounts.
        n_edges_added = 0
        for tx in txs:
            if tx["is_error"]:
                continue
            # Ensure both endpoints are nodes even if not in all-address.csv
            # (raw tx data may reference addresses outside the labeled set —
            # e.g. counterparty exchanges or contracts).
            for a in (tx["from"], tx["to"]):
                if not graph.has_node(a):
                    graph.add_node(a, name_tag="")
                    hack_membership.setdefault(a, set()).add(hack)
            graph.add_edge(
                tx["from"], tx["to"],
                value=tx["value"], timeStamp=tx["timeStamp"],
                blockNumber=tx["blockNumber"], token=tx["token"],
                source_hack=hack,
            )
            n_edges_added += 1

        per_hack_stats[hack] = {
            "n_labeled_addresses": len(addrs),
            "n_heist_labeled": n_heist,
            "n_transactions": len(txs),
            "n_edges_added": n_edges_added,
        }
        print(
            f"  [{i:2d}/{len(hack_names)}] {hack:32s}  "
            f"addrs={len(addrs):>6d}  heist={n_heist:>5d}  "
            f"edges={n_edges_added:>7d}  ({time.time() - t0:.1f}s)"
        )

    # Resolve canonical label per address: heist wins over unknown.
    # (An address labeled heist in ANY hack is treated as heist everywhere.)
    node_labels: dict[str, int] = {}
    for addr, labels in address_labels_per_hack.items():
        node_labels[addr] = 1 if "heist" in labels else 0

    return {
        "graph": graph,
        "node_labels": node_labels,
        "hack_membership": hack_membership,
        "per_hack_stats": per_hack_stats,
        "hacks": hack_names,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hacks", nargs="*", default=None,
        help="Specific hack folder names to load (default: all).",
    )
    parser.add_argument("--out", type=Path, default=OUT_PKL)
    args = parser.parse_args()

    available = list_hacks()
    print(f"Discovered {len(available)} hack folders under {HEIST_ROOT}")

    selected = args.hacks or available
    unknown = [h for h in selected if h not in available]
    if unknown:
        raise SystemExit(
            f"Unknown hack folder(s): {unknown}. Available: {available}"
        )
    print(f"Loading {len(selected)} hacks:\n")

    t_start = time.time()
    combined = build_combined(selected)
    elapsed = time.time() - t_start

    g = combined["graph"]
    labels = combined["node_labels"]
    n_labeled_heist = sum(1 for v in labels.values() if v == 1)
    print()
    print("=" * 78)
    print(f"COMBINED GRAPH built in {elapsed:.1f}s:")
    print(f"  Total nodes:              {g.number_of_nodes():,}")
    print(f"  Total edges:              {g.number_of_edges():,}")
    print(f"  Labeled nodes (heist):    {n_labeled_heist:,}")
    print(
        f"  Labeled nodes (benign):   "
        f"{len(labels) - n_labeled_heist:,}"
    )
    print(
        f"  Unlabeled nodes (pulled from tx endpoints): "
        f"{g.number_of_nodes() - len(labels):,}"
    )
    print("=" * 78)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("wb") as f:
        pickle.dump(combined, f)
    size_mb = args.out.stat().st_size / 1024**2
    print(f"\nSaved to {args.out} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
