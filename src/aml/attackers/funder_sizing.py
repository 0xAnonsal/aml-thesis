"""Tier-based funder pool sizing — one source of truth for Anvil + Sepolia.

Design (locked 2026-08-14):

  * Pool INVARIANT: total operating capital = 5% of the laundering
    amount, regardless of tier. Keeps the "operating overhead" metric
    consistent across scales for the recovery_pct_of_capital reporting.

  * TIER decides only the NUMBER of funders. More funders at higher
    laundering scales because more burners are seeded, and dilution
    across more funders weakens the co-funding heuristic used by the
    GNN detector.

  * NON-UNIFORM per-funder amounts. Each funder gets a uniform-random
    fraction of the pool, normalized to sum to the pool exactly. Real
    attacker cold wallets are never identically sized; equal funders
    would give the classifier a trivial "these two are the same actor"
    signal that our design has to fight, not create.

  * PER-FUNDER CAP: no single funder exceeds MAX_PER_FUNDER_ETH (1.0
    for the amounts we test, up to 125 ETH). Chosen to keep any single
    funder from becoming a de-facto "second Alice" that draws the
    detector's attention on its own.

Tiers:

  amount < 5:          2 funders    (small: minimal dilution needed)
  5 <= amount < 15:    3 funders
  15 <= amount < 40:   5 funders
  40 <= amount < 100:  7 funders
  amount >= 100:       10 funders   (max dilution before deployer→
                                     funder edges saturate the trace)
"""
from __future__ import annotations

import random as _random_module

# Absolute per-funder cap in ETH. Fits every amount up to 125 ETH
# with the 5% pool and 10-funder tier (0.05 * 125 / 10 = 0.625 avg,
# ×1.4 jitter head-room = 0.875 max). Above 125 ETH the tier would
# need to be re-checked — not tested in the thesis scope.
MAX_PER_FUNDER_ETH = 1.0

# Absolute per-funder floor. 2× the gas-seed size (_DEFAULT_GAS_RESERVE_ETH
# = 0.01 in tools.py) so every funder can do at least one seed-transfer
# with margin before rotation kicks in.
MIN_PER_FUNDER_ETH = 0.02

# Fraction of amount stolen allocated to funder pool (5%).
POOL_PCT_OF_AMOUNT = 0.05


def _num_funders_for(amount: float) -> int:
    """Tier-based funder-count lookup. Only surface that varies with scale."""
    if amount < 5.0:
        return 2
    if amount < 15.0:
        return 3
    if amount < 40.0:
        return 5
    if amount < 100.0:
        return 7
    return 10


def allocate_funder_amounts(
    amount: float, rng: _random_module.Random | None = None
) -> list[float]:
    """Return non-uniform per-funder ETH allocations summing to 5% of amount.

    Sampling: uniform in [avg * 0.5, avg * 1.4], normalized so the sum
    equals the 5% target exactly. The asymmetric jitter (min factor
    0.5, max factor 1.4) keeps post-normalization amounts safely
    under MAX_PER_FUNDER_ETH for every tested amount up to 125 ETH.

    Uses the seeded random state so runs are reproducible per seed
    (run_campaign calls `random.seed(seed)` before invoking this).
    """
    r = rng if rng is not None else _random_module

    n = _num_funders_for(amount)
    pool_cap = POOL_PCT_OF_AMOUNT * amount   # 5% is a CEILING, not a target

    # Floor takes precedence when the tier's minimum can't fit inside
    # the 5% pool (happens only for very small amounts).
    if n * MIN_PER_FUNDER_ETH >= pool_cap:
        return [MIN_PER_FUNDER_ETH] * n

    # Sample around the average-of-the-cap with asymmetric jitter (bias
    # slightly under to keep the natural pool below cap without needing
    # to scale). Average raw sum ≈ 0.95 × pool_cap, so most seeds land
    # below the cap naturally.
    avg = pool_cap / n
    lo, hi = avg * 0.5, avg * 1.4

    raws = [r.uniform(lo, hi) for _ in range(n)]
    total = sum(raws)
    if total > pool_cap:
        # Only scale down when the sample exceeded the cap — keeps the
        # 5% as a true ceiling, not a target the pool always hits.
        scale = pool_cap / total
        raws = [x * scale for x in raws]

    # Enforce absolute floor + per-funder cap (safety net).
    return [
        max(MIN_PER_FUNDER_ETH, min(MAX_PER_FUNDER_ETH, x)) for x in raws
    ]
