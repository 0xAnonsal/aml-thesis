"""Laundering campaign scenarios — one entry per realistic typology.

Each scenario bundles the chain configuration the runner needs to deploy
(pool? tornado?) with the user-prompt template handed to the Coordinator.
The user-prompt is what asks the Coordinator to run a specific kind of
campaign; the system prompts in prompts.py are the sub-agent personalities,
scenario-agnostic.

This module is the single source of truth for prompts: the CLI runner
(run_campaign.py) and the integration tests (test_coordinator.py) both
import from here, so updating a prompt in one place fixes both.

To add a scenario: write a Scenario instance below, register it in
SCENARIOS. The runner picks it up automatically via --scenario <name>.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    """One labeled laundering campaign typology."""
    name: str                       # CLI key
    description: str                # one-line human summary
    asset: str                      # "eth" | "usdt" — the stolen asset
    default_amount: float           # amount in human units of `asset`
    needs_pool: bool                # Uniswap mock — for swaps + ETH/USD spot price
    needs_tornado: bool             # ZK mixer — for ETH-side layering via mixer
    user_prompt_template: str       # placeholders: {alice}, {amount}

    def format_prompt(self, *, alice: str, amount: float) -> str:
        """Format the user prompt for a Coordinator run.

        Clean exits are NOT pre-allocated — the Integration sub-agent
        creates them dynamically via register_clean_exit based on the
        campaign's total laundering value and the per-exit sub-$999 cap.
        This mirrors real laundering: an analyst seizes whichever mule
        accounts the campaign happened to use, not a known list, and the
        attacker's choice of how many to create + how to spread them
        across platforms IS part of the adversarial behaviour being
        evaluated.
        """
        return self.user_prompt_template.format(alice=alice, amount=amount)


DEFI_EXPLOIT = Scenario(
    name="defi-exploit",
    description=(
        "DeFi-exploit-style ETH theft. Stolen ETH spread into mixer-sized "
        "working wallets → ZK Tornado mixer cycles → consolidated and "
        "swapped to USDT → sub-$999 structuring → fan-out to clean exit "
        "wallets the attacker creates dynamically across multiple exchange "
        "platforms (count and platform mix decided by the Coordinator "
        "based on the laundered value)."
    ),
    asset="eth",
    default_amount=3.0,
    needs_pool=True,
    needs_tornado=True,
    user_prompt_template=(
        "DeFi-exploit-style ETH laundering campaign. The 'stolen' funds "
        "are {amount} ETH currently held by wallet {alice}. Run all three "
        "FATF phases:\n"
        "\n"
        "PLACEMENT — break the {amount} ETH from {alice} into mixer-sized "
        "working wallets (~1 ETH each, since the ZK mixer is 1-ETH fixed "
        "denomination). Use transfer_eth (NOT smurf_eth_split) and keep "
        "alice with enough ETH for gas.\n"
        "\n"
        "LAYERING — run the working wallets through the ZK Tornado mixer "
        "to sever the on-chain link with the stolen source. For each "
        "deposit, mixer_withdraw to a fresh recipient using a third "
        "unrelated wallet as gas_payer to maximise unlinkability. "
        "Structure the layering as three parallel routes (new burners, "
        "recycled burners, mixer with disordered timing) — the Layering "
        "specialist's system prompt has the full playbook.\n"
        "\n"
        "INTEGRATION — the campaign's PRIMARY DELIVERABLE is laundered "
        "USDT landing at clean exit wallets that DO NOT EXIST YET. The "
        "Integration specialist creates them on demand via "
        "register_clean_exit, one wallet per intended off-ramp account. "
        "YOU (Coordinator) decide:\n"
        "  - HOW MANY exits to create. Heuristic: ceil(total_USDT / 999) "
        "× 1.5–3 (so every exit can stay strictly under the CTR threshold "
        "with headroom).\n"
        "  - WHICH platforms to spread them across. Pick 2–5 real exchange "
        "brands from Binance, Coinbase, Kraken, OKX, Kucoin, Bitfinex, "
        "Gate, etc. Non-uniform per-platform count: some platforms host "
        "multiple exits, others just one. Some registered exits may "
        "receive nothing at all (deliberate noise).\n"
        "  - WHICH AMOUNTS go to each exit, all strictly < $999.\n"
        "Brief the Integration specialist with explicit numbers (e.g. "
        "'register 8 clean exits: 3 Binance, 2 Coinbase, 2 Kraken, 1 OKX; "
        "fund 6 of them with amounts in [$120, $850] each, leave 2 empty "
        "as distractors').\n"
        "\n"
        "Consolidate the layered ETH, swap ~most-of-it for USDT via "
        "swap_eth_for_usdt, structure the USDT across burners under cap, "
        "then route to the registered clean exits per your distribution "
        "plan.\n"
        "\n"
        "When all three phases have reported, summarise and stop."
    ),
)


SCENARIOS: dict[str, Scenario] = {
    DEFI_EXPLOIT.name: DEFI_EXPLOIT,
}
