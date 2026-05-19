"""Structural tests for the run-graph visualiser.

Matplotlib uses the Agg backend (headless) so tests run in any CI
environment without a display. We don't compare pixels (brittle, slow);
we verify:
  - the helpers compute sensible numbers (sizes, widths, alpha)
  - draw_graph returns a (fig, ax) pair and adds nodes/edges to the axes
  - draw_run_graph actually writes a PNG file when handed a real run
  - empty / minimal graphs don't crash
  - the legend includes every label that appears

All synthetic — no Anvil, no API. Uses tmp_path for file artifacts.
"""
from __future__ import annotations

import json

import matplotlib
matplotlib.use("Agg")   # headless backend before any pyplot import in tests

import matplotlib.pyplot as plt   # noqa: E402
import networkx as nx              # noqa: E402
import pytest                      # noqa: E402

from aml.detectors.graph import (   # noqa: E402
    LABEL_ATTACKER_BURNER,
    LABEL_ATTACKER_SOURCE,
    LABEL_CLEAN_EXIT_FUNDED,
    LABEL_CONTRACT,
    LABEL_UNKNOWN,
    load_run,
)
from aml.detectors.viz import (     # noqa: E402
    EDGE_STYLES,
    LABEL_COLOURS,
    _adaptive_alpha,
    _edge_width,
    _node_size,
    draw_graph,
    draw_run_graph,
)


# --- helpers ------------------------------------------------------------


def _addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def _write_attacker_run(tmp_path):
    """Build a tiny attacker run-dir with one mixer cycle + 2 final exits."""
    src = _addr(1)
    burner = _addr(10)
    exit_a, exit_b = _addr(2), _addr(3)
    usdt, pool, tornado = _addr(97), _addr(98), _addr(99)
    run_dir = tmp_path / "tiny-attacker"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text(json.dumps({
        "scenario": "defi-exploit", "asset": "eth", "amount": 1.0, "seed": 99,
    }))
    (run_dir / "addresses.json").write_text(json.dumps({
        "source_wallet": src,
        "operator_wallet": _addr(50),
        "burners_generated_during_campaign": [burner],
        "clean_exit_wallets": [exit_a, exit_b],
        "clean_exits_funded": [exit_a],
        "attacker_wallets": [src, burner],
        "contracts": {"usdt": usdt, "pool": pool, "tornado": tornado},
    }))
    trace = [
        # Source → burner (ETH transfer)
        {"tx_hash": "0x1", "block": 1, "status": 1,
         "from": src, "to": burner, "value_wei": str(10**18),
         "value_eth": 1.0, "gas_used": 21000, "events": []},
        # Mixer deposit
        {"tx_hash": "0x2", "block": 2, "status": 1,
         "from": burner, "to": tornado, "value_wei": str(10**18),
         "value_eth": 1.0, "gas_used": 200_000,
         "events": [{"contract": "tornado", "event": "Deposit",
                     "args": {"commitment": "0xdead", "leafIndex": 0}}]},
        # Mixer withdraw → exit_a
        {"tx_hash": "0x3", "block": 3, "status": 1,
         "from": _addr(50), "to": tornado, "value_wei": "0",
         "value_eth": 0.0, "gas_used": 300_000,
         "events": [{"contract": "tornado", "event": "Withdrawal",
                     "args": {"to": exit_a, "nullifierHash": "0xbeef"}}]},
    ]
    with (run_dir / "chain_trace.jsonl").open("w") as f:
        for rec in trace:
            f.write(json.dumps(rec) + "\n")
    return run_dir


# --- styling helpers -----------------------------------------------------


def test_node_size_scales_with_degree():
    """Higher-degree nodes get larger glyphs; isolated nodes still have a floor."""
    g = nx.MultiDiGraph()
    g.add_node("a")
    g.add_edge("b", "c"); g.add_edge("b", "c")   # b has out_degree 2
    s_iso = _node_size(g, "a")
    s_hub = _node_size(g, "b")
    assert s_iso > 0, "isolated node must still be visible"
    assert s_hub > s_iso, "hub should be bigger than isolate"


def test_edge_width_log_scaled_and_floored():
    """Width grows with value; missing/zero falls back to a constant floor."""
    assert _edge_width(None) > 0
    assert _edge_width(0) > 0
    w1 = _edge_width(1.0)
    w100 = _edge_width(100.0)
    w10k = _edge_width(10_000.0)
    assert w1 < w100 < w10k, "width should grow with value"


def test_adaptive_alpha_fades_with_density():
    """Dense graphs get more transparent edges."""
    base = 0.6
    sparse = _adaptive_alpha(50, base)
    medium = _adaptive_alpha(250, base)
    dense = _adaptive_alpha(500, base)
    assert sparse > medium > dense


def test_label_colour_palette_covers_every_known_label():
    """Every label constant in graph.py has an assigned colour here."""
    for lab in (
        LABEL_ATTACKER_SOURCE, LABEL_ATTACKER_BURNER,
        LABEL_CLEAN_EXIT_FUNDED, LABEL_CONTRACT, LABEL_UNKNOWN,
    ):
        assert lab in LABEL_COLOURS


def test_edge_styles_cover_every_emitted_edge_kind():
    """Every edge kind the graph extractor emits has a matching style."""
    for kind in (
        "transfer_eth", "transfer_usdt",
        "mixer_deposit", "mixer_withdraw", "swap",
    ):
        assert kind in EDGE_STYLES


# --- draw_graph -----------------------------------------------------------


def test_draw_graph_empty_returns_axes_with_message():
    """An empty graph renders a placeholder, doesn't crash."""
    g = nx.MultiDiGraph()
    fig, ax = draw_graph(g, title="empty")
    assert isinstance(fig, plt.Figure)
    assert isinstance(ax, plt.Axes)
    plt.close(fig)


def test_draw_graph_returns_axes_with_collections_for_real_graph():
    """A non-empty graph produces drawn collections (nodes + edges)."""
    g = nx.MultiDiGraph()
    g.add_node("a", label=LABEL_ATTACKER_SOURCE, kind="attacker")
    g.add_node("b", label=LABEL_ATTACKER_BURNER, kind="attacker")
    g.add_node("c", label=LABEL_CLEAN_EXIT_FUNDED, kind="attacker_exit")
    g.add_edge("a", "b", kind="transfer_eth", asset="ETH", value=1.0)
    g.add_edge("b", "c", kind="mixer_deposit", asset="ETH", value=1.0)
    g.graph["run_kind"] = "attacker"

    fig, ax = draw_graph(g)
    # Networkx adds PathCollections for nodes and LineCollections (or
    # FancyArrowPatch list) for edges. We just verify *something* was
    # added to the axes — pixel-level checks are brittle and slow.
    assert len(ax.collections) > 0 or len(ax.patches) > 0
    # Title is set from defaults when not supplied
    assert "attacker" in ax.get_title().lower()
    plt.close(fig)


def test_draw_graph_rejects_unknown_layout():
    """Bad layout name → clear ValueError."""
    g = nx.MultiDiGraph()
    g.add_node("a", label=LABEL_UNKNOWN, kind="unknown")
    with pytest.raises(ValueError, match="unknown layout"):
        draw_graph(g, layout="not-a-real-layout")


def test_draw_graph_legend_lists_present_labels_only():
    """Legend includes one entry per label that actually appears in the graph."""
    g = nx.MultiDiGraph()
    g.add_node("a", label=LABEL_ATTACKER_SOURCE, kind="attacker")
    g.add_node("b", label=LABEL_CONTRACT, kind="contract")
    g.add_edge("a", "b", kind="transfer_eth", asset="ETH", value=1.0)
    fig, ax = draw_graph(g, legend=True)
    legend = ax.get_legend()
    assert legend is not None
    labels = [t.get_text() for t in legend.get_texts()]
    # both node labels should appear
    assert LABEL_ATTACKER_SOURCE in labels
    assert LABEL_CONTRACT in labels
    # and the edge kind
    assert "transfer_eth" in labels
    # a label we didn't put in the graph should NOT be in the legend
    assert LABEL_CLEAN_EXIT_FUNDED not in labels
    plt.close(fig)


# --- draw_run_graph end-to-end ------------------------------------------


def test_draw_run_graph_writes_png(tmp_path):
    """draw_run_graph(run, path) actually produces a non-empty file."""
    run = load_run(_write_attacker_run(tmp_path))
    out = tmp_path / "fig" / "attacker.png"
    written = draw_run_graph(run, out)
    assert written == out
    assert out.exists()
    assert out.stat().st_size > 1000   # any real PNG should be >1KB


def test_draw_run_graph_creates_parent_dirs(tmp_path):
    """draw_run_graph mkdir -p's the parent so callers don't have to."""
    run = load_run(_write_attacker_run(tmp_path))
    out = tmp_path / "deeply" / "nested" / "dir" / "fig.png"
    assert not out.parent.exists()
    draw_run_graph(run, out)
    assert out.exists()


def test_draw_run_graph_default_title_includes_run_info(tmp_path):
    """The scenario-aware default title built by _default_title() mentions
    scenario name + amount + node count.

    Tests `_default_title` directly because that's the function that builds
    the scenario-aware string. (`draw_graph(g)` without a title falls back
    to a GENERIC title — that path is intentional for ad-hoc graphs and
    isn't what we want to assert on here.)
    """
    from aml.detectors.graph import to_networkx
    from aml.detectors.viz import _default_title

    run = load_run(_write_attacker_run(tmp_path))
    g = to_networkx(run)
    title = _default_title(run, g)
    assert "defi-exploit" in title
    assert "1.0" in title or "1" in title
    assert "nodes" in title
