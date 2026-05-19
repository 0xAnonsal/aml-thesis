"""Run-directory loader + transaction-graph extractor.

Reads a run directory produced by aml.attackers.run_campaign or
aml.detectors.run_benign (both share the same artifact schema) and
produces a labelled networkx.MultiDiGraph that the detector trains on.

The artifact schema (recap):
    meta.json          run-level metadata (kind, args, block boundaries)
    addresses.json     all addresses involved + role labels
    chain_trace.jsonl  one record per on-chain tx, with decoded events
    campaign.json      attacker-run only: CampaignResult transcript

Public surface:
    load_run(run_dir) -> RunData
    synthesize_attacker_labels(addresses) -> dict[addr, label]
    to_networkx(run) -> nx.MultiDiGraph
    summarize_graph(g) -> dict   (counts by label / by edge kind)

Edge model: one edge per VALUE-BEARING operation. View calls and
approvals are dropped (no graph signal, lots of noise). Each tx in the
trace can produce zero or more edges depending on the events it
emitted:
  - Native ETH transfer (value > 0, no token events) → 1 edge ETH
  - ERC-20 Transfer event → 1 edge per event, asset = USDT
  - Mixer Deposit → 1 edge depositor → mixer_contract, asset = ETH
  - Mixer Withdraw → 1 edge mixer_contract → recipient, asset = ETH
  - Uniswap-style Swap → 2 edges (in to pool + out from pool)

Node attributes: `label` (canonical role), `kind` (high-level bucket).
Edge attributes: `tx_hash`, `block`, `asset`, `value`, `kind`, `gas_used`.

The address→label canonicalisation handles both artifact dialects:
- benign runs ship a ready `labels` dict (single source of truth)
- attacker runs ship structured fields (source_wallet, clean_exits_*,
  burners_generated_*, etc.); synthesize_attacker_labels collapses
  them into the same flat dict
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx


# Canonical label set. Keep small + stable — these become node attrs the
# detector trains against and end up in evaluation reports.
LABEL_ATTACKER_SOURCE = "attacker_source"
LABEL_ATTACKER_BURNER = "attacker_burner"
LABEL_ATTACKER_OTHER = "attacker_other"
LABEL_CLEAN_EXIT_FUNDED = "clean_exit_funded"
LABEL_CLEAN_EXIT_UNUSED = "clean_exit_unused"
LABEL_BENIGN_USER = "benign_user"
LABEL_INFRASTRUCTURE = "infrastructure"
LABEL_CONTRACT = "contract"
LABEL_UNKNOWN = "unknown"

# Higher-level "kind" bucket. Useful when the detector only cares about
# attacker-vs-benign-vs-other rather than the fine-grained role.
_LABEL_KIND = {
    LABEL_ATTACKER_SOURCE: "attacker",
    LABEL_ATTACKER_BURNER: "attacker",
    LABEL_ATTACKER_OTHER: "attacker",
    LABEL_CLEAN_EXIT_FUNDED: "attacker_exit",
    LABEL_CLEAN_EXIT_UNUSED: "attacker_exit",
    LABEL_BENIGN_USER: "benign",
    LABEL_INFRASTRUCTURE: "infrastructure",
    LABEL_CONTRACT: "contract",
    LABEL_UNKNOWN: "unknown",
}


@dataclass
class RunData:
    """Parsed contents of a run directory."""
    run_dir: Path
    kind: str                       # "attacker" | "benign"
    meta: dict
    addresses: dict
    chain_trace: list[dict]
    labels: dict[str, str]          # canonical address → label
    campaign: dict | None = None    # attacker only


# --- loading -----------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def load_run(run_dir: Path | str) -> RunData:
    """Parse meta.json + addresses.json + chain_trace.jsonl (+ optional campaign.json).

    Detects the run kind from meta.json (`kind` for benign, `scenario`
    for attacker). Builds the canonical `labels` dict from whichever
    label format the addresses.json uses.
    """
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run_dir {run_dir} is not a directory")

    meta_path = run_dir / "meta.json"
    addresses_path = run_dir / "addresses.json"
    trace_path = run_dir / "chain_trace.jsonl"
    campaign_path = run_dir / "campaign.json"
    for p in (meta_path, addresses_path, trace_path):
        if not p.exists():
            raise FileNotFoundError(f"missing required artifact: {p}")

    meta = json.loads(meta_path.read_text())
    addresses = json.loads(addresses_path.read_text())
    chain_trace = _read_jsonl(trace_path)
    campaign = json.loads(campaign_path.read_text()) if campaign_path.exists() else None

    # Benign runs ship a `kind: "benign"` meta field + a ready-made
    # labels dict. Attacker runs ship a `scenario` field instead.
    if meta.get("kind") == "benign":
        run_kind = "benign"
        labels = dict(addresses.get("labels", {}))
    else:
        run_kind = "attacker"
        labels = synthesize_attacker_labels(addresses)

    return RunData(
        run_dir=run_dir, kind=run_kind, meta=meta, addresses=addresses,
        chain_trace=chain_trace, labels=labels, campaign=campaign,
    )


def synthesize_attacker_labels(addresses: dict) -> dict[str, str]:
    """Collapse an attacker addresses.json into a flat addr→label dict.

    Priority order (most specific wins):
      contracts > infrastructure > attacker_source > clean_exit_funded
      > clean_exit_unused > attacker_burner > attacker_other
    """
    labels: dict[str, str] = {}

    # Contracts first (they shouldn't be relabeled).
    for _, addr in (addresses.get("contracts") or {}).items():
        if addr:
            labels[addr] = LABEL_CONTRACT

    # Operator / faucet wallet.
    op = addresses.get("operator_wallet")
    if op and op not in labels:
        labels[op] = LABEL_INFRASTRUCTURE

    # Funded clean exits (the agent actually routed to these).
    funded = set(addresses.get("clean_exits_funded") or [])
    for addr in funded:
        labels[addr] = LABEL_CLEAN_EXIT_FUNDED

    # Labeled-but-unused clean exits (distractors).
    all_exits = set(addresses.get("clean_exit_wallets") or [])
    for addr in all_exits - funded:
        labels.setdefault(addr, LABEL_CLEAN_EXIT_UNUSED)

    # The laundering source. Override anything except contracts.
    src = addresses.get("source_wallet")
    if src and labels.get(src) != LABEL_CONTRACT:
        labels[src] = LABEL_ATTACKER_SOURCE

    # Burners generated during the campaign (every fresh wallet).
    burners = set(addresses.get("burners_generated_during_campaign") or [])
    for addr in burners:
        labels.setdefault(addr, LABEL_ATTACKER_BURNER)

    # Catch-all: any other attacker_wallets entries that didn't fit a
    # more specific role (rare — covers e.g. bootstrap-time wallets
    # that aren't source/operator).
    for addr in addresses.get("attacker_wallets") or []:
        labels.setdefault(addr, LABEL_ATTACKER_OTHER)

    return labels


# --- graph construction -------------------------------------------------


# Lower-cased contract addresses for fast event-dispatch routing inside
# to_networkx. Populated per-run from RunData.addresses.contracts.
def _contracts_by_label(addresses: dict) -> dict[str, str]:
    """Return {"usdt": addr or None, "pool": addr or None, "tornado": addr or None}.

    Lower-cased — events store addresses as checksum, we normalise on
    comparison.
    """
    c = addresses.get("contracts") or {}
    return {
        "usdt": (c.get("usdt") or "").lower() or None,
        "pool": (c.get("pool") or "").lower() or None,
        "tornado": (c.get("tornado") or "").lower() or None,
    }


def _label_for(addr: str, labels: dict[str, str]) -> str:
    """Look up an address's label, normalised on case. Returns LABEL_UNKNOWN if absent."""
    if addr is None:
        return LABEL_UNKNOWN
    # Try exact, then lower-cased, since web3 returns checksum form but
    # labels.json may have been serialised already.
    if addr in labels:
        return labels[addr]
    al = addr.lower()
    for k, v in labels.items():
        if k.lower() == al:
            return v
    return LABEL_UNKNOWN


def _add_node(g: nx.MultiDiGraph, addr: str, labels: dict[str, str]) -> None:
    if addr is None or addr in g:
        return
    lab = _label_for(addr, labels)
    g.add_node(addr, label=lab, kind=_LABEL_KIND.get(lab, "unknown"))


def to_networkx(run: RunData) -> nx.MultiDiGraph:
    """Build the labelled transaction graph from a parsed run.

    Nodes: every address that participates in any value-bearing operation.
    Each node carries `label` (canonical role) and `kind` (bucket).

    Edges: one per value-bearing event. ETH transfers, USDT Transfers,
    mixer Deposit/Withdraw, Uniswap Swap. View calls + approvals are
    silently dropped. Multi-edges between the same pair are preserved
    (each tx is its own edge).
    """
    g = nx.MultiDiGraph()
    g.graph["run_kind"] = run.kind
    g.graph["run_dir"] = str(run.run_dir)

    # Pre-seed every labelled address as an isolated node so distractors
    # (e.g. unused clean exits) are visible even when no edges land there.
    for addr in run.labels:
        _add_node(g, addr, run.labels)

    contracts = _contracts_by_label(run.addresses)

    for tx in run.chain_trace:
        tx_hash = tx.get("tx_hash")
        block = tx.get("block")
        gas_used = tx.get("gas_used", 0)
        status = tx.get("status", 1)
        if status != 1:
            continue   # reverted txs don't move value

        tx_from = tx.get("from")
        tx_to = tx.get("to")
        events = tx.get("events") or []
        value_wei = int(tx.get("value_wei", 0))
        value_eth = value_wei / 10**18

        produced_event_edge = False
        for ev in events:
            kind, edges = _edges_from_event(ev, tx_from, tx_to, contracts)
            for src, dst, attrs in edges:
                _add_node(g, src, run.labels)
                _add_node(g, dst, run.labels)
                g.add_edge(src, dst, tx_hash=tx_hash, block=block,
                           gas_used=gas_used, kind=kind, **attrs)
                produced_event_edge = True

        # Native ETH transfer: only if no token-level events fired AND
        # the tx carried value AND there's a recipient (contract-creation
        # has tx.to == None — skip those, they're deploy txs).
        if not produced_event_edge and value_wei > 0 and tx_to is not None:
            _add_node(g, tx_from, run.labels)
            _add_node(g, tx_to, run.labels)
            g.add_edge(tx_from, tx_to, tx_hash=tx_hash, block=block,
                       gas_used=gas_used, kind="transfer_eth",
                       asset="ETH", value=value_eth)

    return g


def _edges_from_event(
    ev: dict, tx_from: str, tx_to: str, contracts: dict[str, str | None],
) -> tuple[str, list[tuple[str, str, dict]]]:
    """Decode one event into (kind, [(src, dst, attrs), ...]) tuples.

    Returns (kind_label, []) when the event doesn't translate into any
    value-bearing edge (e.g. ERC-20 Approval, view-only events).
    """
    contract = ev.get("contract")
    name = ev.get("event")
    args = ev.get("args") or {}

    if contract == "usdt":
        if name == "Transfer":
            src = args.get("from")
            dst = args.get("to")
            raw = args.get("value", 0)
            value = (int(raw) if isinstance(raw, (str, int)) else raw) / 10**6
            if src and dst:
                return "transfer_usdt", [(src, dst, {
                    "asset": "USDT", "value": float(value),
                })]
        # Approval, Mint events etc — no edge
        return name or "usdt_event", []

    if contract == "pool":
        # Uniswap-style swap: ETH↔USDT. We model it as two edges:
        # user → pool (asset in) and pool → user (asset out). The
        # event ABI varies; common args: sender, amount0In/amount1In/
        # amount0Out/amount1Out OR ethIn/usdtIn/ethOut/usdtOut.
        if name and "Swap" in name:
            user = args.get("sender") or args.get("user") or tx_from
            pool_addr = contracts.get("pool")
            if not (user and pool_addr):
                return name, []
            edges = []
            eth_in = _as_float(args.get("ethIn") or args.get("amount0In"), 18)
            usdt_in = _as_float(args.get("usdtIn") or args.get("amount1In"), 6)
            eth_out = _as_float(args.get("ethOut") or args.get("amount0Out"), 18)
            usdt_out = _as_float(args.get("usdtOut") or args.get("amount1Out"), 6)
            if eth_in > 0:
                edges.append((user, pool_addr, {"asset": "ETH", "value": eth_in}))
            if usdt_in > 0:
                edges.append((user, pool_addr, {"asset": "USDT", "value": usdt_in}))
            if eth_out > 0:
                edges.append((pool_addr, user, {"asset": "ETH", "value": eth_out}))
            if usdt_out > 0:
                edges.append((pool_addr, user, {"asset": "USDT", "value": usdt_out}))
            return "swap", edges
        return name or "pool_event", []

    if contract == "tornado":
        tornado_addr = contracts.get("tornado")
        if name == "Deposit":
            depositor = tx_from   # the tx submitter sent 1 ETH with the call
            if not (depositor and tornado_addr):
                return "mixer_deposit", []
            return "mixer_deposit", [(depositor, tornado_addr, {
                "asset": "ETH", "value": 1.0,
            })]
        if name == "Withdrawal" or name == "Withdraw":
            recipient = args.get("to") or args.get("recipient")
            if not (recipient and tornado_addr):
                return "mixer_withdraw", []
            return "mixer_withdraw", [(tornado_addr, recipient, {
                "asset": "ETH", "value": 1.0,
            })]
        return name or "tornado_event", []

    return contract or "unknown", []


def _as_float(v: Any, decimals: int) -> float:
    """Coerce a raw on-chain integer-string-or-int into a human float."""
    if v is None:
        return 0.0
    try:
        raw = int(v) if not isinstance(v, int) else v
    except (TypeError, ValueError):
        return 0.0
    return raw / (10**decimals)


# --- summary helpers ----------------------------------------------------


def summarize_graph(g: nx.MultiDiGraph) -> dict:
    """Quick counts useful in logs and tests: nodes by label, edges by kind."""
    nodes_by_label: dict[str, int] = {}
    nodes_by_kind: dict[str, int] = {}
    for _, data in g.nodes(data=True):
        lab = data.get("label", LABEL_UNKNOWN)
        nodes_by_label[lab] = nodes_by_label.get(lab, 0) + 1
        kind = data.get("kind", "unknown")
        nodes_by_kind[kind] = nodes_by_kind.get(kind, 0) + 1

    edges_by_kind: dict[str, int] = {}
    edges_by_asset: dict[str, int] = {}
    for _, _, data in g.edges(data=True):
        kind = data.get("kind", "unknown")
        edges_by_kind[kind] = edges_by_kind.get(kind, 0) + 1
        asset = data.get("asset", "unknown")
        edges_by_asset[asset] = edges_by_asset.get(asset, 0) + 1

    return {
        "num_nodes": g.number_of_nodes(),
        "num_edges": g.number_of_edges(),
        "nodes_by_label": nodes_by_label,
        "nodes_by_kind": nodes_by_kind,
        "edges_by_kind": edges_by_kind,
        "edges_by_asset": edges_by_asset,
    }
