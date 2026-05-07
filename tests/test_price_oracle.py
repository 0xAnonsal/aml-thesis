"""Unit tests for PriceOracle."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from aml.env import DepegEvent, PriceOracle


def _write_csv(path: Path, rows: list[tuple[int, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.write("timestamp_unix,price_usd\n")
        for ts, price in rows:
            f.write(f"{ts},{price}\n")


def _make_cache(tmp_path: Path) -> Path:
    cache_dir = tmp_path / "prices"
    base = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    day = 86400
    # 7 daily snapshots: 2024-01-01 .. 2024-01-07
    _write_csv(cache_dir / "eth.csv", [(base + i * day, 2000.0 + i * 50) for i in range(7)])
    _write_csv(cache_dir / "trx.csv", [(base + i * day, 0.10 + i * 0.005) for i in range(7)])
    _write_csv(cache_dir / "usdt.csv", [(base + i * day, 1.00) for i in range(7)])
    return cache_dir


def test_loads_all_supported_assets(tmp_path):
    oracle = PriceOracle(_make_cache(tmp_path))
    assert oracle.assets == ["eth", "trx", "usdt"]


def test_exact_timestamp_returns_exact_price(tmp_path):
    oracle = PriceOracle(_make_cache(tmp_path))
    ts = datetime(2024, 1, 4, tzinfo=timezone.utc)
    # day index 3 (Jan 4): 2000 + 3*50 = 2150
    assert oracle.price("eth", ts) == 2150.0


def test_between_timestamps_returns_last_known(tmp_path):
    oracle = PriceOracle(_make_cache(tmp_path))
    ts = datetime(2024, 1, 4, 18, 30, tzinfo=timezone.utc)
    # last-known semantics: still Jan 4 price
    assert oracle.price("eth", ts) == 2150.0


def test_timestamp_before_cache_raises(tmp_path):
    oracle = PriceOracle(_make_cache(tmp_path))
    with pytest.raises(ValueError, match="before the earliest"):
        oracle.price("eth", datetime(2023, 12, 31, tzinfo=timezone.utc))


def test_timestamp_after_cache_raises(tmp_path):
    oracle = PriceOracle(_make_cache(tmp_path))
    with pytest.raises(ValueError, match="past the latest"):
        oracle.price("eth", datetime(2024, 6, 1, tzinfo=timezone.utc))


def test_unknown_asset_raises(tmp_path):
    oracle = PriceOracle(_make_cache(tmp_path))
    with pytest.raises(KeyError, match="unsupported asset"):
        oracle.price("doge", datetime(2024, 1, 4, tzinfo=timezone.utc))


def test_usd_value(tmp_path):
    oracle = PriceOracle(_make_cache(tmp_path))
    ts = datetime(2024, 1, 4, tzinfo=timezone.utc)
    # 0.5 ETH at $2150 = $1075
    assert oracle.usd_value(0.5, "eth", ts) == pytest.approx(1075.0)


def test_synthetic_depeg_overlay_inside_window(tmp_path):
    depeg = DepegEvent(
        asset="usdt",
        start=datetime(2024, 1, 3, tzinfo=timezone.utc),
        end=datetime(2024, 1, 5, tzinfo=timezone.utc),
        peg_value=0.92,
    )
    oracle = PriceOracle(_make_cache(tmp_path), synthetic_depeg_events=[depeg])
    assert oracle.price("usdt", datetime(2024, 1, 4, tzinfo=timezone.utc)) == 0.92


def test_synthetic_depeg_overlay_outside_window(tmp_path):
    depeg = DepegEvent(
        asset="usdt",
        start=datetime(2024, 1, 3, tzinfo=timezone.utc),
        end=datetime(2024, 1, 5, tzinfo=timezone.utc),
        peg_value=0.92,
    )
    oracle = PriceOracle(_make_cache(tmp_path), synthetic_depeg_events=[depeg])
    assert oracle.price("usdt", datetime(2024, 1, 6, tzinfo=timezone.utc)) == 1.00


def test_unix_timestamp_input(tmp_path):
    oracle = PriceOracle(_make_cache(tmp_path))
    ts_unix = int(datetime(2024, 1, 4, tzinfo=timezone.utc).timestamp())
    assert oracle.price("eth", ts_unix) == 2150.0


def test_loaded_range(tmp_path):
    oracle = PriceOracle(_make_cache(tmp_path))
    start, end = oracle.loaded_range("eth")
    assert start == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert end == datetime(2024, 1, 7, tzinfo=timezone.utc)


def test_missing_cache_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="missing price cache"):
        PriceOracle(empty)


def test_depeg_validation():
    with pytest.raises(ValueError, match="start must be strictly before end"):
        DepegEvent(
            asset="usdt",
            start=datetime(2024, 1, 5, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            peg_value=0.92,
        )
