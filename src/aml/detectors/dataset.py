"""Dataset combiner + partial-visibility split.

Loads N attacker runs + M benign runs (produced by run_campaign and
run_benign) into a single labelled MultiDiGraph, then splits the
resulting graph into per-"exchange" subgraphs that model the
locked-scope novelty of the thesis: each exchange sees only its own
KYC'd users (plus, optionally, the shared on-chain contracts), and the
detector has to do actor-level clustering across exchanges to
reconstruct the full picture.

Also exposes a campaign-level train/val/test splitter — splitting at
the edge level would leak neighbourhood information across splits, so
the unit of split is the entire run.

Public API:
    CombinedDataset                          — combined runs + labels
    ExchangeView                             — one exchange's view
    combine_runs(dirs) -> CombinedDataset
    partial_visibility_split(dataset, ...)   -> list[ExchangeView]
    train_val_test_split(run_names, ...)     -> (train, val, test)

All operations are deterministic given a `seed` so experiments are
reproducible. Graph construction reuses aml.detectors.graph.to_networkx
on a per-run basis, so the edge model (one tx → zero or more typed
edges) and the canonical label set are consistent end-to-end.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from aml.detectors.graph import (
    LABEL_ATTACKER_BURNER,
    LABEL_ATTACKER_OTHER,
    LABEL_ATTACKER_SOURCE,
    LABEL_BENIGN_USER,
    LABEL_CLEAN_EXIT_FUNDED,
    LABEL_CLEAN_EXIT_UNUSED,
    LABEL_CONTRACT,
    LABEL_INFRASTRUCTURE,
    LABEL_UNKNOWN,
    RunData,
    load_run,
    to_networkx,
)


# Label priority for resolving cross-run conflicts. When the SAME
# address appears in multiple runs with different labels (rare in the
# current setup but possible), the higher-priority label wins. Order:
# objective facts (contracts, infrastructure) first; then the most
# specific attacker roles; benign and unknown last.
_LABEL_PRIORITY: list[str] = [
    LABEL_CONTRACT,
    LABEL_INFRASTRUCTURE,
    LABEL_ATTACKER_SOURCE,
    LABEL_ATTACKER_BURNER,
    LABEL_CLEAN_EXIT_FUNDED,
    LABEL_CLEAN_EXIT_UNUSED,
    LABEL_ATTACKER_OTHER,
    LABEL_BENIGN_USER,
    LABEL_UNKNOWN,
]
_LABEL_PRIORITY_INDEX: dict[str, int] = {
    lab: i for i, lab in enumerate(_LABEL_PRIORITY)
}

# Kind bucket lookup — mirror of graph._LABEL_KIND. Duplicated here to
# avoid importing a private symbol. Keep in sync with graph.py if new
# labels are added.
_LABEL_KIND: dict[str, str] = {
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


def _highest_priority_label(labels: list[str]) -> str:
    """Pick the highest-priority label from a list of candidates."""
    return min(
        labels,
        key=lambda lab: _LABEL_PRIORITY_INDEX.get(lab, len(_LABEL_PRIORITY)),
    )


# --- dataset containers --------------------------------------------------


@dataclass
class CombinedDataset:
    """N attacker runs + M benign runs unified into one labelled graph.

    `graph` is the MultiDiGraph union — same address in multiple runs is
    a single node carrying a `runs` attribute listing every run name
    where it appeared. Edges carry their per-run `source_run` attribute
    so downstream code can filter by campaign.

    `node_labels` is the canonical address→label map (one entry per
    unique node, conflict-resolved via _highest_priority_label).
    `runs` keeps the parsed RunData objects so per-run metadata is
    available downstream (e.g., split builders need attacker vs benign).
    """

    graph: nx.MultiDiGraph
    node_labels: dict[str, str]
    runs: list[RunData] = field(default_factory=list)

    @property
    def attacker_run_names(self) -> list[str]:
        return [
            r.meta.get("run_name", str(r.run_dir))
            for r in self.runs if r.kind == "attacker"
        ]

    @property
    def benign_run_names(self) -> list[str]:
        return [
            r.meta.get("run_name", str(r.run_dir))
            for r in self.runs if r.kind == "benign"
        ]

    @property
    def all_run_names(self) -> list[str]:
        return [r.meta.get("run_name", str(r.run_dir)) for r in self.runs]


@dataclass
class ExchangeView:
    """One simulated exchange's partial view of the combined dataset.

    `visible_addresses` is the set of addresses this exchange "owns"
    (its KYC'd users) plus, optionally, the shared on-chain contracts.
    `visible_subgraph` is the **induced** subgraph — only edges where
    BOTH endpoints are visible to this exchange are kept. That's the
    strict-visibility model: cross-exchange transactions appear in
    NEITHER exchange's view, and the collaborative detector has to
    reconstruct the connection by other means (label inference,
    metadata, time correlation).
    """

    name: str
    visible_addresses: set[str]
    visible_subgraph: nx.MultiDiGraph


# --- combining -----------------------------------------------------------


def combine_runs(run_dirs: list[Path | str]) -> CombinedDataset:
    """Load every run dir and unify into a single labelled MultiDiGraph.

    Three passes:
      1. Load each run + build its per-run graph.
      2. Resolve canonical label per address across all runs.
      3. Build the combined graph: nodes carry canonical label + the
         list of source runs they appeared in; edges carry their
         per-run source_run tag for later filtering.

    Returns an empty CombinedDataset for an empty input — useful for
    tests and lets call sites avoid special-casing.
    """
    runs: list[RunData] = []
    per_run_graphs: list[tuple[str, nx.MultiDiGraph]] = []
    address_labels: dict[str, list[str]] = {}
    address_runs: dict[str, set[str]] = {}

    # Pass 1: load each run, build its graph, collect address info.
    for d in run_dirs:
        run = load_run(d)
        runs.append(run)
        run_name = run.meta.get("run_name", str(run.run_dir))
        g = to_networkx(run)
        per_run_graphs.append((run_name, g))
        for node, data in g.nodes(data=True):
            address_labels.setdefault(node, []).append(
                data.get("label", LABEL_UNKNOWN),
            )
            address_runs.setdefault(node, set()).add(run_name)

    # Pass 2: resolve canonical labels.
    canonical_labels: dict[str, str] = {
        addr: _highest_priority_label(labs)
        for addr, labs in address_labels.items()
    }

    # Pass 3: build the combined graph.
    combined = nx.MultiDiGraph()
    combined.graph["num_runs"] = len(runs)
    for addr, label in canonical_labels.items():
        combined.add_node(
            addr,
            label=label,
            kind=_LABEL_KIND.get(label, "unknown"),
            runs=sorted(address_runs[addr]),
        )
    for run_name, g in per_run_graphs:
        for u, v, data in g.edges(data=True):
            combined.add_edge(u, v, **{**data, "source_run": run_name})

    return CombinedDataset(
        graph=combined,
        node_labels=canonical_labels,
        runs=runs,
    )


# --- partial-visibility split -------------------------------------------


def partial_visibility_split(
    dataset: CombinedDataset, *,
    num_exchanges: int = 3,
    seed: int = 0,
    contracts_shared: bool = True,
) -> list[ExchangeView]:
    """Split the combined dataset into N exchange views.

    Each non-contract address is randomly assigned to EXACTLY ONE of
    the num_exchanges exchanges. Each exchange's `visible_subgraph` is
    the induced subgraph over its visible address set — only edges
    with BOTH endpoints visible are included (the strict-visibility
    model; see ExchangeView docstring).

    Args:
        contracts_shared: if True (default), USDT / pool / mixer
            contract addresses are visible to ALL exchanges (realistic:
            on-chain contracts are public infrastructure). If False,
            contracts are also randomly assigned to one exchange each.
        seed: RNG seed for the assignment — same seed reproduces the
            same per-exchange address split.

    Returns the list of views in `exchange_A`, `exchange_B`, ... order.
    Requires num_exchanges in [1, 26] (single-letter naming convention).
    """
    if not (1 <= num_exchanges <= 26):
        raise ValueError(
            f"num_exchanges must be in [1, 26], got {num_exchanges}"
        )

    rng = random.Random(seed)
    exchange_names = [
        f"exchange_{chr(ord('A') + i)}" for i in range(num_exchanges)
    ]

    contracts: set[str] = {
        addr for addr, lab in dataset.node_labels.items()
        if lab == LABEL_CONTRACT
    }

    # Sort the address list before iterating so the seeded RNG produces
    # the same assignment across Python runs (dict iteration order is
    # otherwise insertion-order, which can differ if loaders ran in a
    # different order).
    assignments: dict[str, str] = {}
    for addr in sorted(dataset.node_labels):
        if addr in contracts and contracts_shared:
            continue   # contracts handled separately
        assignments[addr] = rng.choice(exchange_names)

    views: list[ExchangeView] = []
    for name in exchange_names:
        visible = {addr for addr, ex in assignments.items() if ex == name}
        if contracts_shared:
            visible |= contracts
        subgraph = dataset.graph.subgraph(visible).copy()
        views.append(ExchangeView(
            name=name,
            visible_addresses=visible,
            visible_subgraph=subgraph,
        ))
    return views


def partial_visibility_split_by_platform(
    dataset: CombinedDataset, *,
    platforms: list[str] | None = None,
    seed: int = 0,
    contracts_shared: bool = True,
) -> list[ExchangeView]:
    """Platform-aware partial-visibility split — one exchange per platform.

    Unlike `partial_visibility_split` (which assigns EVERY non-contract
    address at random), this version assigns attacker `clean_exit`
    wallets to the exchange partition that matches their registered
    `exchange_platform` label. All other addresses (attacker source,
    burners, benign users) are randomly assigned across the platforms.

    Rationale: in real Ethereum, an exchange (Binance, Coinbase, etc.)
    has KYC visibility ONLY on its own users — it sees deposits from
    and withdrawals to its own wallets, plus public on-chain contracts.
    Random assignment of every address (as `partial_visibility_split`
    does) breaks this semantic: a wallet the attacker registered as
    'Binance' should be visible to the Binance-federated detector, not
    to a random other partition.

    Args:
        platforms: list of exchange platform names to use as partitions.
            Defaults to the full 7-platform universe used by the attacker
            prompt: ["Binance", "Coinbase", "Kraken", "OKX", "Kucoin",
            "Bitfinex", "Gate"]. Each becomes one partition; wallets
            registered at that platform go to that partition. Any
            attacker exit with a `exchange_platform` NOT in this list
            is assigned randomly (models a platform outside the AML
            federation, e.g. a small unregulated venue).
        seed: RNG seed for assignment of unlabeled wallets.
        contracts_shared: as in partial_visibility_split.

    Returns list of ExchangeViews, one per platform in the order given.
    """
    DEFAULT_PLATFORMS = [
        "Binance", "Coinbase", "Kraken", "OKX",
        "Kucoin", "Bitfinex", "Gate",
    ]
    if platforms is None:
        platforms = DEFAULT_PLATFORMS
    if not platforms:
        raise ValueError("platforms list cannot be empty")

    rng = random.Random(seed)

    contracts: set[str] = {
        addr for addr, lab in dataset.node_labels.items()
        if lab == LABEL_CONTRACT
    }

    # Build address → platform map from BOTH:
    #   (a) attacker runs' clean_exit metadata (wallets the attacker
    #       registered under a specific exchange platform), and
    #   (b) benign runs' exchange_wallets metadata (hot wallets that
    #       the run_benign generator bootstrapped for each exchange
    #       platform — added in PR #55 to model CEX interactions).
    address_to_platform: dict[str, str] = {}
    for run in dataset.runs:
        if run.kind == "attacker":
            for exit_record in run.addresses.get("clean_exit_per_address") or []:
                addr = exit_record.get("address")
                platform = exit_record.get("exchange_platform")
                if addr and platform:
                    address_to_platform[addr] = platform
        elif run.kind == "benign":
            for platform, wallets in (run.addresses.get("exchange_wallets") or {}).items():
                for addr in wallets:
                    if addr:
                        address_to_platform[addr] = platform

    # Assign: clean_exit wallets by platform label (if in `platforms`);
    # everything else randomly.
    assignments: dict[str, str] = {}
    for addr in sorted(dataset.node_labels):
        if addr in contracts and contracts_shared:
            continue
        labeled_platform = address_to_platform.get(addr)
        if labeled_platform and labeled_platform in platforms:
            assignments[addr] = labeled_platform
        else:
            # Attacker source, burners, benign users, and clean_exits
            # at platforms outside the AML federation all get random
            # assignment. This models that the AML federation of
            # exchanges has no privileged view of these addresses'
            # KYC identity.
            assignments[addr] = rng.choice(platforms)

    views: list[ExchangeView] = []
    for platform in platforms:
        visible = {addr for addr, ex in assignments.items() if ex == platform}
        if contracts_shared:
            visible |= contracts
        subgraph = dataset.graph.subgraph(visible).copy()
        views.append(ExchangeView(
            name=platform,
            visible_addresses=visible,
            visible_subgraph=subgraph,
        ))
    return views


# --- train/val/test split (campaign-level) ------------------------------


def train_val_test_split(
    run_names: list[str], *,
    ratios: tuple[float, float, float] = (0.7, 0.15, 0.15),
    seed: int = 0,
) -> tuple[list[str], list[str], list[str]]:
    """Split a list of campaign names into (train, val, test).

    Split at the CAMPAIGN level — each run goes to exactly one split.
    An edge-level split would leak neighbourhood information from one
    split into another (the detector would see test-set neighbours
    during training). Campaign-level keeps each laundering trail
    entirely inside one split.

    Args:
        ratios: (train, val, test) fractions that must sum to ~1.0.
        seed: RNG seed for the shuffle — same seed reproduces the same
            split.

    Returns three disjoint lists whose concatenation is a permutation
    of `run_names`. With small N, some splits may be empty (e.g., N=3
    + (0.7, 0.15, 0.15) → val is empty because 3 × 0.15 < 1). That's
    the caller's responsibility to handle.
    """
    if abs(sum(ratios) - 1.0) > 1e-9:
        raise ValueError(f"ratios must sum to 1.0, got {sum(ratios)}")
    if any(r < 0 for r in ratios):
        raise ValueError(f"ratios must be non-negative, got {ratios}")

    names = sorted(run_names)   # deterministic baseline before shuffle
    rng = random.Random(seed)
    rng.shuffle(names)

    n = len(names)
    n_train = int(n * ratios[0])
    n_val = int(n * ratios[1])
    train = names[:n_train]
    val = names[n_train:n_train + n_val]
    test = names[n_train + n_val:]
    return train, val, test
