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
    default_num_clean_exits: int    # how many labeled off-ramp destinations
    user_prompt_template: str       # placeholders: {alice}, {clean_exits}, {amount}

    def format_prompt(
        self, *, alice: str, clean_exits: list[str], amount: float,
    ) -> str:
        """Format the user prompt for a Coordinator run.

        `clean_exits` is a list of labeled off-ramp destination addresses
        the agent can fan out to. Real laundering doesn't funnel into a
        single account; the agent picks a SUBSET of these and routes
        varied amounts so detection has to do actor-level clustering to
        recover the campaign — which is the locked-scope novelty claim.
        """
        if not clean_exits:
            raise ValueError("clean_exits must contain at least one address")
        clean_exits_block = "\n".join(f"  - {addr}" for addr in clean_exits)
        return self.user_prompt_template.format(
            alice=alice, clean_exits=clean_exits_block, amount=amount,
        )


DEFI_EXPLOIT = Scenario(
    name="defi-exploit",
    description=(
        "DeFi-exploit-style ETH theft. Stolen ETH spread into mixer-sized "
        "working wallets → ZK Tornado mixer cycles → consolidated and "
        "swapped to USDT → sub-$999 structuring → fan-out to multiple "
        "labeled clean exit wallets."
    ),
    asset="eth",
    default_amount=3.0,
    needs_pool=True,
    needs_tornado=True,
    default_num_clean_exits=4,
    user_prompt_template=(
        "DeFi-exploit-style ETH laundering campaign. The 'stolen' funds "
        "are {amount} ETH currently held by wallet {alice}. Run all three "
        "FATF phases:\n"
        "\n"
        "PLACEMENT — using transfer_eth (NOT smurf_eth_split), spread the "
        "{amount} ETH from {alice} into exactly 3 fresh burner wallets of "
        "~1 ETH each. These are sized to feed the ZK mixer (1 ETH fixed "
        "denomination). Keep alice with enough ETH for gas.\n"
        "\n"
        "LAYERING — the deployed simulator includes a ZK Tornado mixer "
        "(1 ETH per deposit). For each of the 3 placement wallets, "
        "mixer_deposit 1 ETH from it (save the deposit_note!), then "
        "mixer_withdraw to a fresh burner wallet using that note. For each "
        "mixer_withdraw pass `gas_payer` set to yet another fresh "
        "unrelated wallet to maximise unlinkability. This produces 3 "
        "'layered-clean' recipient wallets each holding 1 ETH.\n"
        "\n"
        "INTEGRATION — the campaign's PRIMARY DELIVERABLE is laundered "
        "value landing at the clean exit wallets below (the labeled "
        "off-ramp destinations — different exchanges, different KYC'd "
        "identities in real life):\n"
        "{clean_exits}\n"
        "\n"
        "Consolidate the 3 layered recipient wallets' ETH into a working "
        "wallet via transfer_eth. Swap ~1.5 ETH for USDT via "
        "swap_eth_for_usdt. Distribute the USDT across 5-8 fresh "
        "structuring burners with each transfer strictly under $999 "
        "(CTR-evasion). From those structuring burners, ROUTE PORTIONS "
        "to several of the clean exit wallets above — realistic "
        "laundering FANS OUT across multiple off-ramp accounts (different "
        "exchanges, different identities), so use 2-4 different clean "
        "exits with varied amounts. Do NOT funnel everything into one "
        "exit. Verify with get_balance that AT LEAST 2 clean exits "
        "received non-zero USDT before reporting — the clean exits "
        "getting funded IS the success criterion.\n"
        "\n"
        "When all three phases have reported, summarise and stop."
    ),
)


SCENARIOS: dict[str, Scenario] = {
    DEFI_EXPLOIT.name: DEFI_EXPLOIT,
}
