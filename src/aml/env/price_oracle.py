"""Historical USD price oracle for ETH, TRX, and USDT.

Used by both attacker and defender to:
  - size laundering legs under USD-denominated FATF thresholds
  - USD-normalize cross-asset flows for detection
  - compute USD-weighted thesis metrics (USD-volume-weighted ASR etc.)

Pricing is data, not strategy: this is a deterministic Python service, NOT an
LLM agent. See ROADMAP §3.2.

Data source: CoinGecko free tier, downloaded once via
``scripts/download_prices.py`` into a local CSV cache. The free tier returns
daily prices for ranges >90 days, which is sufficient for laundering campaigns
that operate at day-or-greater scale.

Lookup semantics: last-known price <= requested timestamp (no peeking ahead).
Timestamps before the cache start or after the cache end raise ``ValueError`` —
re-run the downloader to extend the cached range.

USDT special case: cached prices already reflect real historical depegs.
For ablation experiments, synthetic depeg overlays can be injected via
``synthetic_depeg_events`` in the constructor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SUPPORTED_ASSETS: frozenset[str] = frozenset({"eth", "trx", "usdt"})


@dataclass(frozen=True)
class DepegEvent:
    """Synthetic depeg overlay for ablation experiments.

    Within ``[start, end)`` the oracle returns ``peg_value`` for ``asset``
    instead of the cached CoinGecko price. If multiple overlapping events are
    configured for the same asset, the first match in input order wins.
    """
    asset: str
    start: datetime
    end: datetime
    peg_value: float

    def __post_init__(self):
        if self.asset.lower() not in SUPPORTED_ASSETS:
            raise ValueError(f"DepegEvent: unknown asset {self.asset!r}")
        if self.start >= self.end:
            raise ValueError("DepegEvent: start must be strictly before end")
        if self.peg_value <= 0:
            raise ValueError("DepegEvent: peg_value must be positive")


class PriceOracle:
    """Map ``(asset, timestamp) -> USD price`` from a local CoinGecko cache."""

    def __init__(
        self,
        cache_dir: Path | str,
        synthetic_depeg_events: list[DepegEvent] | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self._tables: dict[str, np.ndarray] = {}
        self._depegs: list[DepegEvent] = list(synthetic_depeg_events or [])
        self._load_all()

    def _load_all(self) -> None:
        for asset in SUPPORTED_ASSETS:
            csv_path = self.cache_dir / f"{asset}.csv"
            if not csv_path.exists():
                raise FileNotFoundError(
                    f"missing price cache for {asset!r}: {csv_path}. "
                    f"Run `python scripts/download_prices.py` to populate."
                )
            df = pd.read_csv(csv_path)
            if list(df.columns) != ["timestamp_unix", "price_usd"]:
                raise ValueError(
                    f"{csv_path} has columns {list(df.columns)!r}, expected "
                    f"['timestamp_unix', 'price_usd']"
                )
            df = df.sort_values("timestamp_unix").reset_index(drop=True)
            self._tables[asset] = df.to_numpy()

    @staticmethod
    def _to_unix(ts: int | float | datetime) -> int:
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return int(ts.timestamp())
        return int(ts)

    def price(self, asset: str, timestamp: int | float | datetime) -> float:
        """Last-known USD price of ``asset`` at or before ``timestamp``."""
        asset = asset.lower()
        if asset not in self._tables:
            raise KeyError(
                f"unsupported asset: {asset!r}. supported={sorted(SUPPORTED_ASSETS)}"
            )
        ts_unix = self._to_unix(timestamp)

        for d in self._depegs:
            if (
                d.asset.lower() == asset
                and self._to_unix(d.start) <= ts_unix < self._to_unix(d.end)
            ):
                return d.peg_value

        timestamps = self._tables[asset][:, 0]
        prices = self._tables[asset][:, 1]
        idx = int(np.searchsorted(timestamps, ts_unix, side="right")) - 1

        if idx < 0:
            raise ValueError(
                f"timestamp {ts_unix} "
                f"({datetime.fromtimestamp(ts_unix, tz=timezone.utc).isoformat()}) "
                f"is before the earliest cached price for {asset!r} "
                f"({datetime.fromtimestamp(int(timestamps[0]), tz=timezone.utc).isoformat()})"
            )
        if ts_unix > int(timestamps[-1]):
            raise ValueError(
                f"timestamp {ts_unix} "
                f"({datetime.fromtimestamp(ts_unix, tz=timezone.utc).isoformat()}) "
                f"is past the latest cached price for {asset!r} "
                f"({datetime.fromtimestamp(int(timestamps[-1]), tz=timezone.utc).isoformat()}). "
                f"Re-run scripts/download_prices.py to extend the cache."
            )
        return float(prices[idx])

    def usd_value(
        self, amount: float, asset: str, timestamp: int | float | datetime
    ) -> float:
        """Convert ``amount`` of ``asset`` to USD at the given ``timestamp``."""
        return amount * self.price(asset, timestamp)

    def loaded_range(self, asset: str) -> tuple[datetime, datetime]:
        """First and last cached timestamps for ``asset`` (UTC)."""
        asset = asset.lower()
        if asset not in self._tables:
            raise KeyError(f"unsupported asset: {asset!r}")
        table = self._tables[asset]
        return (
            datetime.fromtimestamp(int(table[0, 0]), tz=timezone.utc),
            datetime.fromtimestamp(int(table[-1, 0]), tz=timezone.utc),
        )

    @property
    def assets(self) -> list[str]:
        return sorted(self._tables.keys())
