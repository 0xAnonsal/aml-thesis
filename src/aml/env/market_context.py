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
"""
from __future__ import annotations

from datetime import datetime, timezone

from .price_oracle import PriceOracle


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
