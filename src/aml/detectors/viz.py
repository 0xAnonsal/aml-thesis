"""Transaction-graph visualisation — produces thesis-ready figures.

Renders the labelled MultiDiGraph from aml.detectors.graph as a PNG (or
any matplotlib-supported format). Nodes are coloured by `label`, sized
by degree; edges are styled by `kind` (asset and category) and width-
scaled by `value`. Spring layout by default (clusters cohere visually),
overridable.

Usage:
    from aml.detectors.graph import load_run, to_networkx
    from aml.detectors.viz import draw_run_graph

    run = load_run(\"runs/2026-05-19_defi-exploit_seed42/\")
    draw_run_graph(run, \"fig/defi-exploit-seed42.png\")

Or directly on a graph object:

    g = to_networkx(run)
    fig, ax = draw_graph(g, title=\"DeFi exploit campaign\")
    fig.savefig(\"out.png\", dpi=160, bbox_inches=\"tight\")
    plt.close(fig)

Design choices that affect what the figure communicates:
- Distinct colour per label so attacker / benign / exit / infrastructure
  / contract are immediately distinguishable
- Solid edges for token transfers; dashed for mixer; dotted for swaps —
  the eye picks up the structure of laundering (mixer cycles, fan-out
  at exits) without reading the legend
- Edge width scales with the log of the value so small + large transfers
  are both visible
- Edge alpha fades as the graph gets dense (>200 edges) so overlap
  doesn't become an unreadable blob
- Isolated nodes (e.g. unused clean exits) are drawn at the periphery —
  they're a feature, not a bug, since the detector should see distractors
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
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
    to_networkx,
)


# Distinct colours per label. Picked for thesis-print readability — high
# contrast, perceptually distinguishable even in grayscale. Edit here to
# update every figure consistently.
LABEL_COLOURS: dict[str, str] = {
    LABEL_ATTACKER_SOURCE:    "#c0392b",   # red — the stolen-funds wallet
    LABEL_ATTACKER_BURNER:    "#e67e22",   # orange — disposable hops
    LABEL_ATTACKER_OTHER:     "#f39c12",   # lighter orange
    LABEL_CLEAN_EXIT_FUNDED:  "#27ae60",   # green — off-ramps the agent used
    LABEL_CLEAN_EXIT_UNUSED:  "#95a5a6",   # grey — labelled-but-ignored distractor
    LABEL_BENIGN_USER:        "#2980b9",   # blue — ordinary users
    LABEL_INFRASTRUCTURE:     "#8e44ad",   # purple — operator/faucet
    LABEL_CONTRACT:           "#34495e",   # dark slate — deployed contracts
    LABEL_UNKNOWN:            "#bdc3c7",   # light grey — addresses without a role
}

# Edge styling by kind. (linestyle, base_color, base_alpha). Tuned after
# the first round of thesis figures: mixer edges (the project's headline
# ZK contribution) were getting buried in dense centre clusters. They
# now use saturated red/crimson, are drawn LAST so they sit on top, and
# get an extra width boost (see _EDGE_KIND_WIDTH_BOOST below).
EDGE_STYLES: dict[str, tuple[str, str, float]] = {
    "transfer_eth":    ("solid", "#2c3e50", 0.45),
    "transfer_usdt":   ("solid", "#16a085", 0.45),
    "swap":            ("dotted", "#d35400", 0.60),
    "mixer_deposit":   ("dashed", "#e74c3c", 1.00),   # bright red, fully opaque
    "mixer_withdraw":  ("dashed", "#922b21", 1.00),   # deep crimson
}

# Render order: transfer/swap first (they form the background), mixer
# last (they paint on top of everything so the laundering structure is
# unmistakable). Anything not listed here renders at the very end.
_EDGE_DRAW_ORDER: list[str] = [
    "transfer_eth",
    "transfer_usdt",
    "swap",
    "mixer_deposit",
    "mixer_withdraw",
]

# Width multipliers per edge kind. Mixer edges get a 2.5× boost so they
# read as the visual focus even when they share the centre of a busy
# graph with denser transfer activity.
_EDGE_KIND_WIDTH_BOOST: dict[str, float] = {
    "mixer_deposit":  2.5,
    "mixer_withdraw": 2.5,
}


def _node_size(g: nx.MultiDiGraph, node) -> float:
    """Linear-ish size from degree, with a floor so isolated nodes are visible."""
    deg = g.in_degree(node) + g.out_degree(node)
    return 120.0 + 40.0 * deg


def _edge_width(value: float | None) -> float:
    """Log-scaled edge width. Falls back to a constant for missing values."""
    if value is None or value <= 0:
        return 0.7
    return 0.5 + 0.4 * math.log1p(value)


def _adaptive_alpha(num_edges: int, base: float) -> float:
    """Fade edges as the graph gets dense so overlap is still readable."""
    if num_edges <= 100:
        return base
    if num_edges <= 400:
        return base * 0.75
    return base * 0.55


def draw_graph(
    g: nx.MultiDiGraph, *,
    title: str | None = None,
    layout: str = "spring",
    seed: int = 7,
    figsize: tuple[float, float] = (12.0, 9.0),
    legend: bool = True,
) -> tuple[plt.Figure, plt.Axes]:
    """Render a labelled MultiDiGraph as a matplotlib figure.

    Returns (fig, ax). Caller is responsible for fig.savefig(...) and
    plt.close(fig) — kept separate so callers can compose figures
    (subplots, side-by-side attacker vs benign, etc).

    `layout` is one of {"spring", "kamada_kawai", "circular", "shell"}.
    Spring is the default — it pulls clusters together visually, which
    is what you want for spotting laundering structure.
    """
    fig, ax = plt.subplots(figsize=figsize)
    if g.number_of_nodes() == 0:
        ax.text(0.5, 0.5, "empty graph", ha="center", va="center",
                transform=ax.transAxes)
        ax.set_axis_off()
        return fig, ax

    # --- layout ---
    if layout == "spring":
        # k controls inter-node spacing. The default 1/sqrt(N) bunches
        # everything; 2.5/sqrt(N) gives the laundering structure room
        # to breathe so peripheral nodes (clean exits, unused
        # distractors) don't get yanked into the centre cluster.
        n = max(g.number_of_nodes(), 1)
        pos = nx.spring_layout(g, seed=seed, k=2.5 / math.sqrt(n))
    elif layout == "kamada_kawai":
        pos = nx.kamada_kawai_layout(g)
    elif layout == "circular":
        pos = nx.circular_layout(g)
    elif layout == "shell":
        pos = nx.shell_layout(g)
    else:
        raise ValueError(f"unknown layout {layout!r}")

    # --- nodes ---
    # Bucket by label so we can draw with the right colour in one call
    # per label (cleaner than per-node loop).
    nodes_by_label: dict[str, list] = {}
    for node, data in g.nodes(data=True):
        lab = data.get("label", LABEL_UNKNOWN)
        nodes_by_label.setdefault(lab, []).append(node)

    for label, nodes in nodes_by_label.items():
        colour = LABEL_COLOURS.get(label, LABEL_COLOURS[LABEL_UNKNOWN])
        sizes = [_node_size(g, n) for n in nodes]
        nx.draw_networkx_nodes(
            g, pos, ax=ax, nodelist=nodes, node_color=colour,
            node_size=sizes, edgecolors="white", linewidths=0.6,
            label=label,
        )

    # --- edges ---
    num_edges = g.number_of_edges()
    # Bucket edges by kind so each kind is drawn with its style in one call.
    edges_by_kind: dict[str, list[tuple]] = {}
    for u, v, key, data in g.edges(keys=True, data=True):
        kind = data.get("kind", "unknown")
        edges_by_kind.setdefault(kind, []).append((u, v, key, data))

    # Render in explicit priority order: background kinds first, mixer
    # (the visual focus) LAST so it paints on top of everything else.
    # Any kind not in the priority list falls through at the end.
    ordered_kinds: list[str] = (
        [k for k in _EDGE_DRAW_ORDER if k in edges_by_kind]
        + [k for k in edges_by_kind if k not in _EDGE_DRAW_ORDER]
    )

    for kind in ordered_kinds:
        edge_list = edges_by_kind[kind]
        style, base_colour, base_alpha = EDGE_STYLES.get(
            kind, ("solid", "#7f8c8d", 0.5),
        )
        # Mixer edges are full-opacity by design (they're the thesis
        # contribution) — let them ignore the density-fade we apply to
        # background edges.
        if base_alpha >= 1.0:
            alpha = 1.0
        else:
            alpha = _adaptive_alpha(num_edges, base_alpha)
        boost = _EDGE_KIND_WIDTH_BOOST.get(kind, 1.0)
        widths = [_edge_width(d.get("value")) * boost
                  for _, _, _, d in edge_list]
        nx.draw_networkx_edges(
            g, pos, ax=ax,
            edgelist=[(u, v) for u, v, _, _ in edge_list],
            style=style, edge_color=base_colour, alpha=alpha,
            width=widths, arrows=True, arrowsize=10,
            connectionstyle="arc3,rad=0.08",   # curve so parallel edges separate
        )

    # --- decoration ---
    if title is None:
        kind = g.graph.get("run_kind", "?")
        title = (
            f"{kind} run · {g.number_of_nodes()} nodes · "
            f"{g.number_of_edges()} edges"
        )
    ax.set_title(title, fontsize=12)
    ax.set_axis_off()

    if legend:
        # One legend entry per label that actually appears; plus an
        # edge-kind line for each edge type present.
        node_handles = []
        for label in nodes_by_label:
            colour = LABEL_COLOURS.get(label, LABEL_COLOURS[LABEL_UNKNOWN])
            node_handles.append(
                plt.scatter([], [], c=colour, s=60, label=label,
                            edgecolors="white", linewidths=0.6),
            )
        edge_handles = []
        for kind in edges_by_kind:
            style, base_colour, _ = EDGE_STYLES.get(
                kind, ("solid", "#7f8c8d", 0.5),
            )
            edge_handles.append(
                plt.Line2D([0], [0], color=base_colour, linestyle=style,
                           linewidth=2.0, label=kind),
            )
        ax.legend(
            handles=node_handles + edge_handles,
            loc="upper left", bbox_to_anchor=(1.01, 1.0),
            fontsize=8, frameon=False,
        )
        fig.tight_layout()

    return fig, ax


def draw_run_graph(
    run: RunData, out_path: str | Path, *,
    title: str | None = None,
    layout: str = "spring",
    seed: int = 7,
    figsize: tuple[float, float] = (12.0, 9.0),
    dpi: int = 160,
) -> Path:
    """Render a run to an image file. Convenience wrapper.

    Builds the graph via to_networkx, calls draw_graph, saves the figure
    at `out_path` (parent directory created if needed), closes the
    figure. Returns the path written. Default title is derived from the
    run's meta if not supplied.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    g = to_networkx(run)
    if title is None:
        title = _default_title(run, g)
    fig, _ = draw_graph(g, title=title, layout=layout, seed=seed, figsize=figsize)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _default_title(run: RunData, g: nx.MultiDiGraph) -> str:
    meta = run.meta or {}
    if run.kind == "benign":
        return (
            f"Benign run · seed {meta.get('seed', '?')} · "
            f"{meta.get('num_users', '?')} users → {g.number_of_nodes()} nodes · "
            f"{g.number_of_edges()} edges"
        )
    return (
        f"Attacker run · {meta.get('scenario', '?')} · "
        f"seed {meta.get('seed', '?')} · "
        f"{meta.get('amount', '?')} {meta.get('asset', '?')} · "
        f"{g.number_of_nodes()} nodes · {g.number_of_edges()} edges"
    )
