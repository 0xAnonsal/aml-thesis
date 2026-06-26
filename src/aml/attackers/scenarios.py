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


STABLECOIN_SCAM = Scenario(
    name="stablecoin-scam",
    description=(
        "Stablecoin-scam-style USDT laundering — phishing/romance/ponzi "
        "scam revenue arrives in a single wallet as USDT. NO mixer "
        "available (Tornado Cash is ETH-only and most stablecoin "
        "laundering operates entirely in USDT to keep the asset stable "
        "while moving). Layering is structuring + multi-hop transfers "
        "across burner chains, optionally cycling through DEX swaps. "
        "Integration fans out to clean exits across exchange platforms "
        "with strict sub-$999 per-exit caps."
    ),
    asset="usdt",
    default_amount=8000.0,
    needs_pool=True,             # optional swap available (agent may use it)
    needs_tornado=False,         # NO mixer in this campaign typology
    user_prompt_template=(
        "Stablecoin-scam-style USDT laundering campaign. The 'stolen' "
        "funds are {amount} USDT currently held by wallet {alice} (this "
        "represents pooled victim payments from a phishing or romance "
        "scam). Run all three FATF phases:\n"
        "\n"
        "PLACEMENT — break the {amount} USDT from {alice} into many "
        "structuring burners. Use smurf_split (the USDT structuring "
        "tool) with random sub-$999 chunks. Keep alice with a tiny "
        "USDT residual so the source wallet doesn't look 'drained-to-"
        "zero' (a textbook detection signal).\n"
        "\n"
        "LAYERING — no ZK mixer is deployed in this campaign (mixer is "
        "ETH-only in real life and most stablecoin laundering stays in "
        "USDT for price stability). Layering instead uses long burner "
        "chains: from each placement burner, multi-hop the USDT through "
        "2-4 fresh intermediate wallets before any of it reaches the "
        "integration phase. Apply the trifurcation pattern WITHOUT "
        "ROUTE C: ROUTE A (forward to new burners + extra hops) and "
        "ROUTE B (recycle existing burners to create CYCLES — Louvain "
        "modularity drops on cyclic graphs). Optionally use "
        "swap_usdt_for_eth then swap_eth_for_usdt to asset-cycle a "
        "fraction of the funds (real launderers do this to break "
        "token-level tracing).\n"
        "\n"
        "INTEGRATION — same as the other scenarios: the Integration "
        "specialist creates clean exits dynamically via "
        "register_clean_exit. YOU (Coordinator) decide:\n"
        "  - HOW MANY exits to create. Heuristic: "
        "ceil({amount} / 999) × 1.0-1.5.\n"
        "  - WHICH platforms to spread them across (2-5 from Binance, "
        "Coinbase, Kraken, OKX, Kucoin, Bitfinex, Gate). Non-uniform "
        "per-platform count.\n"
        "  - WHICH AMOUNTS go to each exit, all strictly < $999.\n"
        "Brief Integration with explicit numbers.\n"
        "\n"
        "When all three phases have reported, summarise and stop."
    ),
)


RANSOMWARE_CASHOUT = Scenario(
    name="ransomware-cashout",
    description=(
        "Ransomware-cashout-style ETH laundering — a single ransom "
        "payment of multiple ETH lands in the attacker wallet. Larger "
        "scale than defi-exploit (5 ETH vs 3 ETH) drives more mixer "
        "cycles, more burners, more clean exits. Heavy ZK mixer use "
        "is the defining tactic — ransomware operators routinely cycle "
        "ransom payments through Tornado Cash to break the on-chain "
        "link with the victim's wallet."
    ),
    asset="eth",
    default_amount=5.0,
    needs_pool=True,
    needs_tornado=True,
    user_prompt_template=(
        "Ransomware-cashout-style ETH laundering campaign. The 'stolen' "
        "funds are {amount} ETH currently held by wallet {alice} "
        "(representing a single ransom payment from the victim). Run "
        "all three FATF phases:\n"
        "\n"
        "PLACEMENT — break the {amount} ETH from {alice} into "
        "mixer-sized working wallets (~1 ETH each, since the ZK mixer "
        "is 1-ETH fixed denomination). With {amount} ETH this means "
        "roughly {amount:.0f}-5 working wallets. Use transfer_eth "
        "(NOT smurf_eth_split). Keep alice with enough ETH for gas.\n"
        "\n"
        "LAYERING — heavy ZK Tornado mixer use is the SIGNATURE of "
        "this scenario. Push EVERY 1-ETH working wallet through the "
        "mixer. Some funds should cycle through the mixer TWICE "
        "(deposit, withdraw to a new wallet, deposit again from that "
        "new wallet, withdraw to yet another wallet) for extra "
        "unlinkability. Apply the full trifurcation: ROUTE A (new "
        "burners), ROUTE B (recycle existing burners for cycles), "
        "ROUTE C (mixer with disordered timing). For each "
        "mixer_withdraw pass gas_payer to an unrelated wallet.\n"
        "\n"
        "INTEGRATION — the larger scale means more clean exits than "
        "a defi-exploit run. The Integration specialist creates them "
        "dynamically via register_clean_exit. YOU (Coordinator) decide:\n"
        "  - HOW MANY exits to create. Heuristic: at this scale, "
        "around 15-25 exits is appropriate.\n"
        "  - WHICH platforms to spread them across (3-6 platforms — "
        "ransomware operators diversify off-ramp risk widely).\n"
        "  - WHICH AMOUNTS go to each exit, all strictly < $999.\n"
        "Brief Integration with explicit numbers.\n"
        "\n"
        "Consolidate the layered ETH, swap most of it for USDT via "
        "swap_eth_for_usdt, then route to the registered clean exits "
        "per your distribution plan.\n"
        "\n"
        "When all three phases have reported, summarise and stop."
    ),
)


SCENARIOS: dict[str, Scenario] = {
    DEFI_EXPLOIT.name: DEFI_EXPLOIT,
    STABLECOIN_SCAM.name: STABLECOIN_SCAM,
    RANSOMWARE_CASHOUT.name: RANSOMWARE_CASHOUT,
}
