"""Market-context helpers — one source of truth for attacker + defender.

Both the attacker Coordinator (aml.attackers.run_campaign) and the LLM
defender Coordinator (aml.detectors.multi_agent) need to inject a
USD-denominated market snapshot into their LLM prompts, so each side
reasons under the same spot-price regime. This module owns the two
helpers so both call sites stay in sync.

`resolve_campaign_ts` picks a timestamp safe against the oracle cache:
Anvil runs default to now(UTC) clamped to the last cached day (or use
`--campaign-ts` for reproducibility across seeds); Sepolia runs pass
now(UTC) directly after refreshing the cache.

`build_market_context` renders the snapshot as a human-readable block
that the LLM parses fine. Format is stable — schema-parseable if
downstream code ever wants to extract the numbers back out.

`ensure_fresh_prices` transparently re-runs scripts/download_prices.py
when the local cache is stale (>threshold hours old). Both runners
call it at startup so every campaign gets today's spot price without
manual intervention. Silent no-op when the cache is already fresh.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .price_oracle import PriceOracle

_STALE_THRESHOLD_HOURS = 24
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOWNLOAD_SCRIPT = _REPO_ROOT / "scripts" / "download_prices.py"


def ensure_fresh_prices(
    cache_dir: Path | str,
    *,
    force: bool = False,
    stale_hours: int = _STALE_THRESHOLD_HOURS,
) -> PriceOracle:
    """Load the oracle; refresh the CSV cache first if it's stale.

    Runs scripts/download_prices.py when the ETH cache lags now(UTC)
    by more than `stale_hours` hours (or when `force=True`). Silent
    no-op if the cache is already fresh. On refresh failure (network
    down, API rate-limited, missing key), returns the oracle over the
    STALE cache with a warning to stderr — the campaign proceeds under
    the last-known price rather than crashing on start-up. Returns the
    fully-loaded PriceOracle either way.
    """
    cache_dir = Path(cache_dir)
    needs_refresh = force
    if not force:
        try:
            probe = PriceOracle(cache_dir=cache_dir)
            _, last_cached = probe.loaded_range("eth")
            age = datetime.now(timezone.utc) - last_cached
            if age.total_seconds() > stale_hours * 3600:
                needs_refresh = True
                print(
                    f"[oracle] cache is {age.days}d {age.seconds//3600}h "
                    f"stale (last={last_cached.date().isoformat()}); "
                    f"refreshing…",
                    file=sys.stderr,
                )
        except (FileNotFoundError, ValueError) as exc:
            # Cache missing or malformed — force download.
            print(f"[oracle] cache probe failed: {exc}; downloading…",
                  file=sys.stderr)
            needs_refresh = True

    if needs_refresh:
        try:
            result = subprocess.run(
                [sys.executable, str(_DOWNLOAD_SCRIPT)],
                capture_output=True, text=True, timeout=180, check=False,
            )
            if result.returncode != 0:
                print(
                    f"[oracle] WARNING: download_prices.py failed "
                    f"(exit {result.returncode}). Falling back to "
                    f"stale cache. stderr: {result.stderr.strip()[:200]}",
                    file=sys.stderr,
                )
            else:
                print("[oracle] cache refreshed.", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(
                "[oracle] WARNING: download_prices.py timed out after "
                "180s; using stale cache.",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[oracle] WARNING: refresh raised {type(exc).__name__}: "
                f"{exc}. Using stale cache.",
                file=sys.stderr,
            )

    return PriceOracle(cache_dir=cache_dir)


def resolve_campaign_ts(
    oracle: PriceOracle, override_iso: str | None
) -> datetime:
    """Pick a deterministic-but-safe campaign timestamp for the oracle.

    Anvil runs are reproducible: pass an ISO override (YYYY-MM-DD or
    full ISO 8601) to freeze the market snapshot across seeds. Without
    an override, defaults to now(UTC) clamped to the last cached day
    so oracle.price never raises on a milliseconds-past-cache-end
    query. Sepolia's runner ignores the override and passes live
    now(UTC) after refreshing the CSV cache.
    """
    if override_iso is not None:
        ts = datetime.fromisoformat(override_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    _, last_cached = oracle.loaded_range("eth")
    return min(datetime.now(timezone.utc), last_cached)


def build_market_context(
    oracle: PriceOracle, campaign_ts: datetime
) -> str:
    """Render a market snapshot block for LLM prompt injection.

    Both attacker and defender append this to their system/user prompt
    so both jugadores reason under the same USD regime. Keeps the FATF
    thresholds computed dynamically from live-spot prices rather than
    hardcoding an ETH-cap that drifts with volatility.
    """
    eth_usd = oracle.price("eth", campaign_ts)
    usdt_usd = oracle.price("usdt", campaign_ts)
    trx_usd = oracle.price("trx", campaign_ts)
    return (
        f"MARKET CONTEXT (spot @ {campaign_ts.strftime('%Y-%m-%d')} UTC):\n"
        f"  1 ETH  = ${eth_usd:,.2f}   1 USDT = ${usdt_usd:.4f}   "
        f"1 TRX = ${trx_usd:.4f}\n"
        f"FATF thresholds in current spot terms:\n"
        f"  $10,000 CTR   ~ {10000/eth_usd:.3f} ETH   ~ {10000/usdt_usd:,.0f} USDT\n"
        f"  $999 sub-CTR  ~ {999/eth_usd:.4f} ETH  ~ {999/usdt_usd:,.0f} USDT"
    )
