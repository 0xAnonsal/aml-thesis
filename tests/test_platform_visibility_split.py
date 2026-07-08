"""Tests for partial_visibility_split_by_platform (PR #52).

Validates that:
- Clean-exit wallets registered at a platform go to that platform's partition
- Other wallets (source, burners, benigns) are randomly distributed
- Contracts are shared across all partitions
- The split is deterministic under a fixed seed
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import networkx as nx
import pytest

from aml.detectors.dataset import (
    CombinedDataset,
    ExchangeView,
    partial_visibility_split_by_platform,
)
from aml.detectors.graph import (
    LABEL_ATTACKER_BURNER,
    LABEL_ATTACKER_SOURCE,
    LABEL_BENIGN_USER,
    LABEL_CLEAN_EXIT_FUNDED,
    LABEL_CONTRACT,
)


@dataclass
class _FakeRun:
    """Minimal RunData stub for tests."""
    kind: str
    addresses: dict


def _make_dataset():
    """Build a small combined dataset with known platform metadata."""
    g = nx.MultiDiGraph()

    # 3 clean_exit wallets at different platforms
    g.add_node("0xBIN1", label=LABEL_CLEAN_EXIT_FUNDED)
    g.add_node("0xCOI1", label=LABEL_CLEAN_EXIT_FUNDED)
    g.add_node("0xKRA1", label=LABEL_CLEAN_EXIT_FUNDED)
    # Attacker source + 2 burners (no platform)
    g.add_node("0xSRC1", label=LABEL_ATTACKER_SOURCE)
    g.add_node("0xBUR1", label=LABEL_ATTACKER_BURNER)
    g.add_node("0xBUR2", label=LABEL_ATTACKER_BURNER)
    # 2 benign users
    g.add_node("0xBEN1", label=LABEL_BENIGN_USER)
    g.add_node("0xBEN2", label=LABEL_BENIGN_USER)
    # 1 contract
    g.add_node("0xUSDT", label=LABEL_CONTRACT)

    labels = {n: d["label"] for n, d in g.nodes(data=True)}

    # A single attacker run whose addresses.json contains the platform map
    fake_run = _FakeRun(
        kind="attacker",
        addresses={
            "clean_exit_per_address": [
                {"address": "0xBIN1", "exchange_platform": "Binance"},
                {"address": "0xCOI1", "exchange_platform": "Coinbase"},
                {"address": "0xKRA1", "exchange_platform": "Kraken"},
            ],
        },
    )
    return CombinedDataset(graph=g, node_labels=labels, runs=[fake_run])


def test_clean_exit_wallets_go_to_their_platform():
    dataset = _make_dataset()
    views = partial_visibility_split_by_platform(
        dataset, platforms=["Binance", "Coinbase", "Kraken"], seed=0,
    )
    views_by_name = {v.name: v for v in views}

    # Each Binance-labeled wallet MUST be in the Binance view
    assert "0xBIN1" in views_by_name["Binance"].visible_addresses
    assert "0xBIN1" not in views_by_name["Coinbase"].visible_addresses
    assert "0xBIN1" not in views_by_name["Kraken"].visible_addresses

    assert "0xCOI1" in views_by_name["Coinbase"].visible_addresses
    assert "0xCOI1" not in views_by_name["Binance"].visible_addresses

    assert "0xKRA1" in views_by_name["Kraken"].visible_addresses


def test_contracts_visible_to_all_platforms():
    dataset = _make_dataset()
    views = partial_visibility_split_by_platform(
        dataset, platforms=["Binance", "Coinbase", "Kraken"], seed=0,
    )
    for v in views:
        assert "0xUSDT" in v.visible_addresses


def test_unlabeled_wallets_go_to_exactly_one_platform():
    dataset = _make_dataset()
    views = partial_visibility_split_by_platform(
        dataset, platforms=["Binance", "Coinbase", "Kraken"], seed=0,
    )
    for unlabeled in ("0xSRC1", "0xBUR1", "0xBUR2", "0xBEN1", "0xBEN2"):
        seen_in = [v.name for v in views if unlabeled in v.visible_addresses]
        assert len(seen_in) == 1, (
            f"{unlabeled} appears in {seen_in}, expected exactly 1 platform"
        )


def test_default_uses_seven_attacker_platforms():
    dataset = _make_dataset()
    views = partial_visibility_split_by_platform(dataset, seed=0)
    assert len(views) == 7
    names = {v.name for v in views}
    assert names == {
        "Binance", "Coinbase", "Kraken", "OKX",
        "Kucoin", "Bitfinex", "Gate",
    }


def test_deterministic_under_same_seed():
    dataset = _make_dataset()
    a = partial_visibility_split_by_platform(dataset, seed=42)
    b = partial_visibility_split_by_platform(dataset, seed=42)
    for va, vb in zip(a, b):
        assert va.visible_addresses == vb.visible_addresses


def test_empty_platforms_list_raises():
    dataset = _make_dataset()
    with pytest.raises(ValueError, match="platforms"):
        partial_visibility_split_by_platform(dataset, platforms=[], seed=0)


def test_exit_at_platform_outside_federation_is_randomized():
    """A Gate-registered wallet, if Gate not in federation, gets random assignment."""
    dataset = _make_dataset()
    # Add a Gate-registered exit
    dataset.graph.add_node("0xGAT1", label=LABEL_CLEAN_EXIT_FUNDED)
    dataset.node_labels["0xGAT1"] = LABEL_CLEAN_EXIT_FUNDED
    dataset.runs[0].addresses["clean_exit_per_address"].append(
        {"address": "0xGAT1", "exchange_platform": "Gate"},
    )
    # Federation of only 3 (Gate not included)
    views = partial_visibility_split_by_platform(
        dataset, platforms=["Binance", "Coinbase", "Kraken"], seed=0,
    )
    # 0xGAT1 must land in exactly ONE of the 3 (random assignment)
    seen_in = [v.name for v in views if "0xGAT1" in v.visible_addresses]
    assert len(seen_in) == 1
