"""Chain action tools for the AML attacker agents.

The agent layer (LLMClient + tool_use blocks) calls these via ToolDispatcher.
Each tool wraps an on-chain action — read or write — on the Anvil simulator.
The dispatcher owns the chain context (Web3 client, deployed contracts,
attacker-controlled wallet keys) so the agent never sees crypto plumbing —
it just calls `dispatch(name, input)` and gets a structured ToolResult back.

Tools shipped:
    get_balance              — read ETH or USDT balance for an address
    get_gas_budget           — read-only: spendable ETH above the gas-reserve
                               floor; lets the LLM plan hop budgets without
                               flying blind
    transfer_usdt            — write: single USDT transfer
    transfer_eth             — write: single ETH transfer (gas-reserve aware)
    generate_burner_wallet   — fresh keypair, auto-registered AND auto-seeded
                               with gas dust so the burner can immediately
                               be used as a sender (without this, multi-hop
                               laundering is impossible — burners with no ETH
                               can't even submit transactions)
    mint_usdt                — permissionless mint (research convenience)
    smurf_split              — large-scale USDT structuring across N random
                               burner wallets in one call. Each burner is
                               also gas-seeded.
    smurf_eth_split          — ETH-denominated structuring with per-wallet
                               USDT-equivalent cap (defaults to $999, below
                               US CTR threshold). Pool spot price drives the
                               conversion. Each burner gas-seeded. Primary
                               Placement-stage tool when the stolen asset
                               is ETH.
    swap_eth_for_usdt        — Uniswap-style ETH -> USDT swap (gas-reserve
                               aware: refuses if it would drop wallet below
                               reserve_eth)
    swap_usdt_for_eth        — Uniswap-style USDT -> ETH swap (approve +
                               swap done atomically inside the dispatcher;
                               gas-reserve aware)
    get_swap_quote           — view-only quote: expected output for a given
                               input given the pool's current reserves
    mixer_deposit            — deposit 1 ETH into the ZK Tornado mixer;
                               returns a secret deposit note
    mixer_withdraw           — withdraw 1 ETH from the mixer to any
                               recipient using a deposit note + a Groth16
                               proof generated internally via snarkjs
    mixer_batch_deposit      — batch N deposits into the mixer in one call;
                               returns N deposit notes. Collapses N
                               Coordinator round-trips into one.
    mixer_batch_withdraw     — batch N withdrawals from the mixer in one
                               call; per-note success/failure preserved.
"""
from __future__ import annotations

import json
import os
import random
import secrets
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eth_account import Account
from web3 import Web3
from web3.logs import DISCARD


# Hard cap on smurf_split's wallet count to prevent runaway gas / runtime
# from a misbehaving agent. 5000 burners ≈ 100 seconds on Anvil + ~0.25 ETH
# in gas — both fine; anything 10× that risks operator pain.
_MAX_BURNERS_PER_SMURF = 5000

# Default ETH dust seeded into every freshly-generated burner wallet, AND
# the default protected gas-floor below which transfer_eth / swap_* refuse
# to drop a wallet (override per-call with reserve_eth=0 to drain a wallet
# at end-of-campaign). Single number on purpose: a burner created with
# `_DEFAULT_GAS_RESERVE_ETH` of ETH is at floor — every operation it does
# must keep it at or above floor unless explicitly told to drain. Matches
# real-world launderer OPSEC where the operator drips fixed gas dust into
# each disposable wallet and never strands one mid-campaign.
_DEFAULT_GAS_RESERVE_ETH = 0.005

# Gwei budget for a standard ETH transfer (21k gas baseline).
_ETH_TRANSFER_GAS = 21_000

# Conservative gas headroom we add to `reserve_eth` when deciding whether a
# swap or eth-transfer is safe to submit — covers this tx's own gas at the
# current gas price so the wallet doesn't dip below reserve when it lands.
_GAS_HEADROOM_TX = 300_000   # generous: covers swap (~250k) or USDT xfer (~100k)


# --- ZK mixer (Tornado) wiring ------------------------------------------
# The mixer tools shell out to Node (zk_helpers.js — MiMC hashing + Merkle
# path reconstruction) and snarkjs (Groth16 witness + proof generation).
# Paths are resolved relative to the repo root so they work regardless of
# the caller's CWD. tools.py lives at src/aml/attackers/, so parents[3] is
# the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ZK_HELPER_JS = _REPO_ROOT / "scripts" / "zk_helpers.js"
_ZK_CIRCUIT = "withdraw"
_ZK_BUILD = _REPO_ROOT / "circuits" / "build" / _ZK_CIRCUIT
_ZK_WASM = _ZK_BUILD / f"{_ZK_CIRCUIT}_js" / f"{_ZK_CIRCUIT}.wasm"
_ZK_ZKEY = _ZK_BUILD / f"{_ZK_CIRCUIT}_final.zkey"

# Must match `component main = Withdraw(10)` in circuits/withdraw.circom and
# the `_levels` MockTornado was deployed with.
_MERKLE_DEPTH = 10

# MockTornado.DENOMINATION — the fixed mixer deposit/withdraw size.
_MIXER_DENOMINATION_WEI = 10**18

# Hard cap on mixer_batch_{deposit,withdraw} to prevent runaway ZK proof
# generation time. Each deposit re-hashes the full Merkle path (~3M gas,
# a few seconds on-chain). Each withdraw generates a Groth16 proof
# (~10-30s per note off-chain via snarkjs). A batch of 100 withdraws is
# already ~15-50 min of proof generation — reasonable ceiling.
_MAX_MIXER_BATCH = 100

# Deposit-note wire format: "<prefix>:<nullifier_hex>:<secret_hex>", each
# component zero-padded to 64 hex chars. The note is the *only* secret
# needed to withdraw — whoever holds it controls the deposited 1 ETH.
_NOTE_PREFIX = "aml-mixer-note-v1"


@dataclass
class ToolResult:
    """Structured result from a tool call.

    Either `output` (success — JSON-serializable dict) or `error` (string
    explaining why it failed). Mirrors Anthropic's tool_result block shape:
    success payload becomes the `content`, `is_error` flag set on failure.
    """
    output: Any | None = None
    error: str | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def to_content(self) -> str:
        """Serialize for inclusion in a tool_result message block."""
        if self.is_error:
            return f"Error: {self.error}"
        return json.dumps(self.output)


# Anthropic-format tool schemas. Pass `dispatcher.tool_definitions` to
# `LLMClient.complete(tools=...)` and the model can call them by name.
_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_balance",
        "description": (
            "Get the current balance of ETH or USDT for an Ethereum address on "
            "the simulated chain. Returns the balance as a decimal number in "
            "human units (ETH or USDT — not wei or 6-decimal base units)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Ethereum address in 0x... format.",
                },
                "asset": {
                    "type": "string",
                    "enum": ["ETH", "USDT"],
                    "description": "Which asset to query.",
                },
            },
            "required": ["address", "asset"],
        },
    },
    {
        "name": "transfer_usdt",
        "description": (
            "Transfer USDT from one wallet to another. The sender must be a "
            "wallet the dispatcher knows the private key for (i.e., a wallet "
            "the attacker controls — added via the wallets registry). Amount "
            "is in human units of USDT (e.g. 100.5 = 100.50 USDT), not the "
            "6-decimal base units the contract uses internally. The sender "
            "pays ETH gas (~50,000 gas per transfer)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_address": {
                    "type": "string",
                    "description": "Sender address. Must be in the dispatcher's wallet registry.",
                },
                "to_address": {
                    "type": "string",
                    "description": "Recipient address (does NOT need to be in the registry).",
                },
                "amount_usdt": {
                    "type": "number",
                    "description": "USDT amount in human units (positive number).",
                },
            },
            "required": ["from_address", "to_address", "amount_usdt"],
        },
    },
    {
        "name": "generate_burner_wallet",
        "description": (
            "Generate a fresh Ethereum keypair and register it in the "
            "dispatcher's wallet registry. The agent only sees the address; "
            "the private key is kept internally so the dispatcher can sign "
            "future transactions on the wallet's behalf. The new burner is "
            f"AUTO-SEEDED with {_DEFAULT_GAS_RESERVE_ETH} ETH from the "
            "operator's faucet wallet so it can immediately pay gas as a "
            "sender (without this, USDT received by the burner would be "
            "stranded — burners with no ETH can't even submit transactions). "
            "Returns {address, gas_seed_eth}. Still holds 0 USDT — fund it "
            "via transfer_usdt or mint_usdt to give it a laundering balance."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "register_clean_exit",
        "description": (
            "Create a fresh wallet AND register it as an intended CLEAN EXIT "
            "(off-ramp destination) on a named exchange platform. Use this "
            "instead of generate_burner_wallet for any wallet that the "
            "campaign intends to land laundered USDT on — these are the "
            "campaign's primary deliverables (the labeled 'mule' accounts "
            "an analyst would seize in a real investigation). The agent "
            "decides how many to create and across which platforms based on "
            "the amount being laundered and the sub-$999 per-exit cap. "
            "Heuristic: at least ceil(total_USDT / 999) exits, multiplied by "
            "~1.5-3x for headroom; spread across 2-5 real exchange platforms "
            "(Binance, Coinbase, Kraken, OKX, Kucoin, Bitfinex, Gate); some "
            "platforms should host multiple exits, others just one. "
            "The wallet is auto-seeded with gas dust (same as a burner) so "
            "it can immediately receive USDT and would be usable downstream. "
            "Returns {address, exchange_platform}. The exchange_platform is "
            "stored in the dispatcher's registered_clean_exits log and "
            "surfaced as ground-truth labels in the campaign artifacts so "
            "the detector training pipeline can score per-platform recovery."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "exchange_platform": {
                    "type": "string",
                    "description": (
                        "Human-readable exchange/platform name this exit "
                        "represents — e.g. 'Binance', 'Coinbase', 'Kraken', "
                        "'OKX', 'Kucoin'. Used purely as a ground-truth "
                        "label; does not affect chain behaviour."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": (
                        "Optional short rationale (max ~80 chars) — e.g. "
                        "'second Binance account, structured to dodge per-"
                        "account daily limit'. Stored alongside the exit "
                        "metadata. Optional."
                    ),
                },
            },
            "required": ["exchange_platform"],
        },
    },
    {
        "name": "peel_chain",
        "description": (
            "Execute a PEEL CHAIN — the most common crypto laundering "
            "technique, appearing in ~70% of real cryptocurrency theft "
            "cases (Merkle Science, TRM Labs). At each of N hops, a small "
            "percentage of the funds is 'peeled off' to a fresh burner "
            "wallet (which sits as a dormant sink) while the bulk continues "
            "to the next hop in the chain. This produces a long linear "
            "topology that is fundamentally different from mixer cycles or "
            "trifurcated fan-outs, and is what real-world analysts see most "
            "often in Bitcoin and Ethereum theft cases (e.g. Lazarus Group, "
            "HTX Bridge exploit). Works for either ETH or USDT. Returns "
            "the list of hop addresses, the list of peel-off addresses, and "
            "the amount remaining at the tail of the chain."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_address": {
                    "type": "string",
                    "description": (
                        "Starting wallet (must be in dispatcher registry). "
                        "Holds the initial amount at the head of the chain."
                    ),
                },
                "asset": {
                    "type": "string",
                    "enum": ["ETH", "USDT"],
                    "description": "Asset flowing through the peel chain.",
                },
                "initial_amount": {
                    "type": "number",
                    "description": (
                        "Amount of `asset` at the head of the chain, in "
                        "human units (ETH or USDT). Sender must hold this "
                        "amount. Real-case examples: 400 ETH (Lazarus/"
                        "Bybit), ~$100M (HTX Bridge)."
                    ),
                },
                "num_hops": {
                    "type": "integer",
                    "description": (
                        "Number of hops in the chain (default 6, typical "
                        "real-case range 6-15 for professional operations; "
                        "10-30 for extended dormancy). Longer chains obscure "
                        "the trail more but cost more gas and lock more "
                        "capital in sinks."
                    ),
                    "default": 6,
                },
                "peel_pct": {
                    "type": "number",
                    "description": (
                        "Target average percentage peeled off at each hop, "
                        "in [0.01, 0.20]. Default 0.02 (2%). Empirical "
                        "range: large hacks (Lazarus/Bybit, HTX Bridge, "
                        "Ronin — $50M+) use 1-3% (preserving 60-80% at "
                        "tail); small retail scams use 5-10% (more "
                        "fragmentation, less preservation). The linear "
                        "topology is the detection signature — magnitude "
                        "of the peel is secondary — so LOWER peels keep "
                        "more capital under attacker control without "
                        "losing evasion value."
                    ),
                    "default": 0.02,
                },
                "peel_jitter": {
                    "type": "number",
                    "description": (
                        "Fractional jitter around peel_pct sampled uniformly "
                        "per hop, in [0, 1]. Default 0 (deterministic). "
                        "Recommend 0.5 for realistic per-hop variation: "
                        "with peel_pct=0.02 and jitter=0.5, per-hop peels "
                        "vary in [1%, 3%]. Random peels prevent the "
                        "detector from spotting a fixed-ratio fingerprint "
                        "across hops."
                    ),
                    "default": 0.0,
                },
                "seed": {
                    "type": "integer",
                    "description": (
                        "Optional seed for the per-hop jitter RNG "
                        "(reproducibility). Omit for non-deterministic."
                    ),
                },
            },
            "required": ["from_address", "asset", "initial_amount"],
        },
    },
    {
        "name": "advance_blocks",
        "description": (
            "Advance the blockchain by N blocks — used to simulate "
            "TIMING DELAYS between laundering phases. Real-world crypto "
            "laundering commonly involves waits of days to months "
            "between operations (Lazarus/Bybit waited weeks before the "
            "first Tornado Cash deposit; HTX/HECO Bridge attacker "
            "waited 4 months). Delays differentiate a hit-and-run from "
            "sophisticated APT operations. On Anvil, the per-block "
            "interval is randomised each call from a weighted APT "
            "distribution (60% quick 12s-5min/block, 25% cooling-off "
            "5min-1h/block, 15% deep dormancy 1h-12h/block) — chain "
            "time is realistic, wall clock is instant. On live testnets "
            "the call sleeps a random 10-360s wall clock (network "
            "controls actual block progression). Response includes the "
            "sampled interval so you can see what delay you got."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "num_blocks": {
                    "type": "integer",
                    "description": (
                        "How many blocks to advance. On Anvil [100, "
                        "1000000]; combined with the random per-block "
                        "interval this yields chain-time from minutes "
                        "(100 blocks × 12s) up to years (1M blocks × "
                        "12h) — pick num_blocks to bound roughly what "
                        "you want, the sampler decides the exact gap. "
                        "On live chains [5, 30]; num_blocks is a hint "
                        "only (wall-clock jitter is fixed 10-360s)."
                    ),
                },
            },
            "required": ["num_blocks"],
        },
    },
    {
        "name": "mint_usdt",
        "description": (
            "Permissionlessly mint USDT to an address. MockUSDT has a "
            "no-auth mint() function (research convenience — would be "
            "catastrophic on a real chain). Used to bootstrap fresh burners "
            "without depleting the main wallet. Gas paid by the first wallet "
            "in the dispatcher's registry."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_address": {"type": "string", "description": "Recipient of the minted USDT."},
                "amount_usdt": {"type": "number", "description": "Amount in human USDT units (positive)."},
            },
            "required": ["to_address", "amount_usdt"],
        },
    },
    {
        "name": "get_swap_quote",
        "description": (
            "View-only quote: expected output for swapping `amount` of "
            "`from_asset` ('ETH' or 'USDT') through the Uniswap-style pool, "
            "given current reserves. Returns the expected output amount "
            "(after the pool's 0.3% fee), the current spot price (ETH/USDT), "
            "and the slippage caused by your swap (in percent vs spot). "
            "Costs no gas. Use this to pick a sensible min_out for the "
            "actual swap call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_asset": {
                    "type": "string",
                    "enum": ["ETH", "USDT"],
                    "description": "Which asset you're swapping FROM.",
                },
                "amount": {
                    "type": "number",
                    "description": "How much of from_asset to swap (human units).",
                },
            },
            "required": ["from_asset", "amount"],
        },
    },
    {
        "name": "swap_eth_for_usdt",
        "description": (
            "Swap ETH for USDT via the Uniswap-style pool (constant-product, "
            "0.3% fee). The caller's wallet (must be in the dispatcher's "
            "registry) pays `eth_amount` of ETH plus ETH gas, receives USDT. "
            "Pass `min_usdt_out` to enforce slippage protection (the call "
            "reverts if the actual output would be less). Respects a "
            f"gas-reserve floor (default {_DEFAULT_GAS_RESERVE_ETH} ETH): the "
            "call refuses if `eth_amount` + gas would drop the wallet below "
            "`reserve_eth`. Pass `reserve_eth=0` to drain — only at "
            "end-of-campaign."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_address": {
                    "type": "string",
                    "description": "Wallet doing the swap. Must be in the registry.",
                },
                "eth_amount": {
                    "type": "number",
                    "description": "ETH to swap, human units (e.g. 1.5 for 1.5 ETH).",
                },
                "min_usdt_out": {
                    "type": "number",
                    "description": "Slippage floor: minimum USDT to accept. Default 0.",
                },
                "reserve_eth": {
                    "type": "number",
                    "description": (
                        f"Minimum ETH the wallet must retain post-swap "
                        f"(default {_DEFAULT_GAS_RESERVE_ETH}). 0 = drain."
                    ),
                },
            },
            "required": ["from_address", "eth_amount"],
        },
    },
    {
        "name": "swap_usdt_for_eth",
        "description": (
            "Swap USDT for ETH via the Uniswap-style pool. The caller's "
            "wallet (must be in the dispatcher's registry) pays USDT and "
            "gas, receives ETH. This tool does the ERC-20 approve + swap "
            "as two separate transactions internally (the LLM doesn't need "
            "to handle approval; just call swap_usdt_for_eth with the "
            "amount and the dispatcher handles both txs). Pass `min_eth_out` "
            "to enforce slippage protection. Requires the wallet to hold "
            f"at least `reserve_eth` (default {_DEFAULT_GAS_RESERVE_ETH}) "
            "plus gas for the approve+swap — the swap itself increases ETH, "
            "but you can't even submit the approve tx with zero ETH."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_address": {
                    "type": "string",
                    "description": "Wallet doing the swap. Must be in the registry.",
                },
                "usdt_amount": {
                    "type": "number",
                    "description": "USDT to swap, human units.",
                },
                "min_eth_out": {
                    "type": "number",
                    "description": "Slippage floor: minimum ETH to accept. Default 0.",
                },
                "reserve_eth": {
                    "type": "number",
                    "description": (
                        f"Pre-swap gas floor (default "
                        f"{_DEFAULT_GAS_RESERVE_ETH}). The wallet must hold "
                        "at least this much ETH plus enough for two txs' gas."
                    ),
                },
            },
            "required": ["from_address", "usdt_amount"],
        },
    },
    {
        "name": "smurf_split",
        "description": (
            "Random-amount structuring: distribute USDT from one wallet across "
            "many newly-generated burner wallets in a single call. Each burner "
            "receives a random amount in [0, max_per_wallet] base units; the "
            "amounts sum to total_usdt EXACTLY (last wallet absorbs any "
            "residual). All burners are auto-registered in the dispatcher; "
            "from_address pays all gas in ETH (~50,000 gas × num_wallets). "
            "Use this for large-scale structuring (hundreds or thousands of "
            f"burners; capped at {_MAX_BURNERS_PER_SMURF}). Single-wallet "
            "transfers should still go through transfer_usdt. Returns a "
            "bounded summary: wallets_created, totals, gas usage, and a "
            "sample of the first 5 recipients (not all of them — that would "
            "blow up the LLM context for thousand-wallet campaigns)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_address": {
                    "type": "string",
                    "description": "Sender wallet (must be in registry, must hold ≥ total_usdt).",
                },
                "total_usdt": {
                    "type": "number",
                    "description": "Total USDT to distribute across the burners. Sum of all burner amounts will equal this exactly.",
                },
                "num_wallets": {
                    "type": "integer",
                    "description": (
                        f"Number of burner wallets to generate and fund. "
                        f"Capped at {_MAX_BURNERS_PER_SMURF}. Must satisfy "
                        f"num_wallets * max_per_wallet >= total_usdt."
                    ),
                },
                "max_per_wallet": {
                    "type": "number",
                    "description": (
                        "Per-wallet ceiling in USDT (e.g. 999.999 to stay "
                        "strictly under a $1000 reporting threshold)."
                    ),
                },
                "seed": {
                    "type": "integer",
                    "description": "Optional random seed for reproducibility.",
                },
            },
            "required": ["from_address", "total_usdt", "num_wallets", "max_per_wallet"],
        },
    },
    {
        "name": "transfer_eth",
        "description": (
            "Transfer ETH from one wallet to another. The sender must be in "
            "the dispatcher's wallet registry. Amount is in human ETH units "
            "(not wei). Respects a gas-reserve floor: the call refuses to "
            "proceed if it would drop the sender's ETH balance below "
            f"`reserve_eth` (default {_DEFAULT_GAS_RESERVE_ETH} ETH). Pass "
            "`reserve_eth=0` to drain the wallet — only do that at "
            "end-of-campaign when the wallet is being decommissioned, "
            "otherwise you'll strand it (no gas → can't transact)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_address": {
                    "type": "string",
                    "description": "Sender address. Must be in the registry.",
                },
                "to_address": {
                    "type": "string",
                    "description": "Recipient address (need not be in the registry).",
                },
                "amount_eth": {
                    "type": "number",
                    "description": "ETH to transfer, human units (positive).",
                },
                "reserve_eth": {
                    "type": "number",
                    "description": (
                        f"Minimum ETH to leave in sender after the transfer "
                        f"(default {_DEFAULT_GAS_RESERVE_ETH}). Pass 0 to "
                        "drain at end-of-campaign."
                    ),
                },
            },
            "required": ["from_address", "to_address", "amount_eth"],
        },
    },
    {
        "name": "smurf_eth_split",
        "description": (
            "ETH-denominated structuring: distribute `total_eth` from one "
            "wallet across `num_wallets` newly-generated burner wallets in a "
            "single call. Each burner receives a random ETH amount strictly "
            "BELOW `max_per_wallet_usdt` worth of ETH (converted at the "
            "Uniswap pool's current spot price). The amounts sum to "
            "`total_eth` exactly. Every fresh burner is auto-seeded with "
            f"{_DEFAULT_GAS_RESERVE_ETH} ETH from the faucet (separate from "
            "the laundered amount) so it can immediately pay gas as a sender. "
            f"Default cap is $999 — well below the US $10k CTR threshold. "
            "Use this as the Placement-stage structuring primitive when the "
            "stolen asset is ETH. Returns a bounded summary: wallets_created, "
            "totals, gas usage, and a sample of the first 5 recipients."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_address": {
                    "type": "string",
                    "description": "Sender wallet (in registry, holds >= total_eth + gas).",
                },
                "total_eth": {
                    "type": "number",
                    "description": "Total ETH to distribute across the burners.",
                },
                "num_wallets": {
                    "type": "integer",
                    "description": (
                        f"Number of burner wallets to generate. Capped at "
                        f"{_MAX_BURNERS_PER_SMURF}. Must be large enough that "
                        "num_wallets × (max_per_wallet_usdt-equivalent in ETH) "
                        ">= total_eth."
                    ),
                },
                "max_per_wallet_usdt": {
                    "type": "number",
                    "description": (
                        "Per-wallet ceiling expressed in USDT-equivalent. "
                        "Default 999 (strictly under a $1k structuring "
                        "threshold). The cap is converted to ETH using the "
                        "Uniswap pool's current spot price; passing a higher "
                        "value relaxes the cap."
                    ),
                },
                "seed": {
                    "type": "integer",
                    "description": "Optional random seed for reproducibility.",
                },
            },
            "required": ["from_address", "total_eth", "num_wallets"],
        },
    },
    {
        "name": "get_gas_budget",
        "description": (
            "Read-only: report a wallet's gas-spending budget. Returns the "
            "current ETH balance, the protected `reserve_eth` floor (default "
            f"{_DEFAULT_GAS_RESERVE_ETH}), the spendable ETH above that "
            "floor, current gas price (gwei), an estimated per-tx ETH cost, "
            "and an estimated number of typical txs the wallet can still pay "
            "for before hitting the reserve. Costs no gas. Use this before "
            "planning a sequence of hops or swaps so you don't strand a "
            "wallet mid-campaign."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Address to query (need not be in the registry).",
                },
                "reserve_eth": {
                    "type": "number",
                    "description": (
                        f"Protected floor (default {_DEFAULT_GAS_RESERVE_ETH}). "
                        "Spendable budget = current ETH balance minus this."
                    ),
                },
            },
            "required": ["address"],
        },
    },
    {
        "name": "mixer_deposit",
        "description": (
            "Deposit into a ZK mixer pool (Tornado-Cash-style, multi-"
            "denomination). Pick `denomination_eth` from the deployed pool "
            "family — typical Sepolia setup: [0.1, 1, 10] ETH, one pool "
            "per denomination. The deposited amount MUST equal the pool's "
            "denomination exactly; call inspect_chain first if you're not "
            "sure which pools are live. Generates a fresh secret note, "
            "commits its hash on-chain, and sends DENOMINATION ETH from "
            "`from_address`. Returns a `deposit_note` string — this is "
            "the ONLY way to later withdraw the ETH, so it must be "
            "remembered and kept secret. The mixer breaks the on-chain "
            "link between depositor and withdrawer beyond the anonymity "
            "set of all deposits in THAT pool (each denomination has its "
            "own separate anonymity set — bigger pools = better mixing)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_address": {
                    "type": "string",
                    "description": (
                        "Wallet making the deposit. Must be in the "
                        "registry and hold >= denomination_eth plus gas."
                    ),
                },
                "denomination_eth": {
                    "type": "number",
                    "description": (
                        "Pool denomination in ETH. Must match a deployed "
                        "pool. Typical values 0.1, 1, or 10. Defaults to "
                        "1.0 for backward compatibility."
                    ),
                    "default": 1.0,
                },
            },
            "required": ["from_address"],
        },
    },
    {
        "name": "mixer_withdraw",
        "description": (
            "Withdraw DENOMINATION ETH from a ZK mixer pool using a "
            "`deposit_note` returned by an earlier mixer_deposit call. "
            "`denomination_eth` MUST match the pool the note was "
            "deposited into (mixer_deposit's output includes the exact "
            "value — pass it back). The ETH is paid to `recipient` (any "
            "address — a fresh unrelated address gives the best "
            "unlinkability). A Groth16 zero-knowledge proof is generated "
            "internally proving the note's commitment is in the mixer's "
            "Merkle tree, without revealing which deposit it was. Pass "
            "`gas_payer` to choose which registered wallet submits and "
            "pays gas — for unlinkability this should be unrelated to "
            "both the depositor and the recipient."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deposit_note": {
                    "type": "string",
                    "description": "The deposit note string from a prior mixer_deposit call.",
                },
                "recipient": {
                    "type": "string",
                    "description": (
                        "Address that receives the DENOMINATION ETH. Any "
                        "address; need not be in the registry."
                    ),
                },
                "gas_payer": {
                    "type": "string",
                    "description": (
                        "Optional. Registered wallet that submits the "
                        "withdraw tx and pays its gas. Defaults to the "
                        "first registered wallet. For unlinkability, use "
                        "a wallet unrelated to deposit and recipient."
                    ),
                },
                "denomination_eth": {
                    "type": "number",
                    "description": (
                        "Pool denomination the note belongs to. MUST "
                        "match what was passed to mixer_deposit. Defaults "
                        "to 1.0 for backward compat with pre-multi-denom "
                        "notes."
                    ),
                    "default": 1.0,
                },
            },
            "required": ["deposit_note", "recipient"],
        },
    },
    {
        "name": "mixer_batch_deposit",
        "description": (
            "Batch deposit: make N mixer_deposit calls in a single tool call. "
            "Deposits N × 1 ETH from `from_address` and returns the N "
            "corresponding deposit notes. Each deposit is independent and "
            "generates a fresh (nullifier, secret) pair, so each of the N "
            "notes withdraws exactly 1 ETH separately. `from_address` must "
            "hold at least N × 1 ETH plus gas. All N notes are returned in "
            f"the response — save ALL of them. Cap: {_MAX_MIXER_BATCH} "
            "deposits per call. Use instead of calling mixer_deposit in a "
            "loop when planning mixer-heavy campaigns (ransomware-cashout, "
            "DeFi-exploit) — collapses N Coordinator round-trips into one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_address": {
                    "type": "string",
                    "description": (
                        "Wallet making the deposits. Must be in the registry "
                        "and hold >= num_deposits × 1 ETH plus gas."
                    ),
                },
                "num_deposits": {
                    "type": "integer",
                    "description": (
                        f"How many separate 1-ETH deposits to make. Capped "
                        f"at {_MAX_MIXER_BATCH}."
                    ),
                },
            },
            "required": ["from_address", "num_deposits"],
        },
    },
    {
        "name": "mixer_batch_withdraw",
        "description": (
            "Batch withdraw: withdraw N notes from the mixer in a single "
            "tool call. Pass `deposit_notes` (list, length N) and either "
            "`recipients` as a list of length N (one recipient per note) OR "
            "a single string (all N notes withdraw to the same address — "
            "useful for consolidation). Optional `gas_payer` (registered "
            "wallet) submits all N withdrawal txs and pays their gas. Each "
            "withdrawal generates a fresh Groth16 proof (~10-30s per note), "
            "so a batch of N takes roughly N × those seconds. Returns per-"
            "note success/failure so partial success is preserved. Cap: "
            f"{_MAX_MIXER_BATCH} notes per call. Use when consolidating a "
            "mixer-heavy campaign into a single Coordinator round-trip."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deposit_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of deposit_note strings from prior mixer_deposit "
                        "or mixer_batch_deposit calls."
                    ),
                },
                "recipients": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": (
                        "Either a single recipient string (all withdrawals to "
                        "the same address) or a list of length equal to "
                        "deposit_notes (one recipient per note). Any address; "
                        "recipients need not be in the wallet registry."
                    ),
                },
                "gas_payer": {
                    "type": "string",
                    "description": (
                        "Optional. Registered wallet that submits all "
                        "withdrawal txs and pays their gas. Defaults to the "
                        "first registered wallet. For unlinkability, use a "
                        "wallet unrelated to the deposits and the recipients."
                    ),
                },
            },
            "required": ["deposit_notes", "recipients"],
        },
    },
    {
        "name": "inspect_chain",
        "description": (
            "Read-only audit of the current chain state. Returns a "
            "structured summary of registered wallet balances (ETH + USDT), "
            "a sample of the top wallets by balance, the deployed contract "
            "addresses, counts of recent events by kind (mixer deposits / "
            "withdrawals, swaps, USDT transfers) since `since_block`, and "
            "detection-relevant signals — e.g. how uniform the burner "
            "balances are (a low coefficient of variation is a feature "
            "signature a GCN detector can easily learn). Costs no gas "
            "and modifies no state. Use this between phase delegations "
            "to verify what's actually on-chain against what a sub-agent "
            "reported, and to spot uniformity patterns BEFORE the "
            "detector does."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "since_block": {
                    "type": "integer",
                    "description": (
                        "Earliest block to scan for event counts. Default 0 "
                        "(scan from genesis). For incremental audits between "
                        "phases, pass the block_number from your previous "
                        "inspect_chain call so the deltas reflect just the "
                        "last phase's activity."
                    ),
                },
                "max_wallets_sample": {
                    "type": "integer",
                    "description": (
                        "Cap on the wallets sample size (sorted by ETH "
                        "balance, descending). Default 8."
                    ),
                },
            },
            "required": [],
        },
    },
]


def _raw_tx(signed) -> bytes:
    """web3.py v6 (rawTransaction) vs v7 (raw_transaction) attr-name compat."""
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes attribute")
    return raw


# --- ZK mixer helpers (module-level — used by mixer_deposit/withdraw) ----


def _encode_note(nullifier: int, secret: int) -> str:
    """Serialize a (nullifier, secret) pair into a deposit-note string."""
    return f"{_NOTE_PREFIX}:{nullifier:064x}:{secret:064x}"


def _decode_note(note: str) -> tuple[int, int]:
    """Parse a deposit-note string back into (nullifier, secret).

    Raises ValueError with an agent-readable message on any malformation.
    """
    parts = note.strip().split(":")
    if len(parts) != 3 or parts[0] != _NOTE_PREFIX:
        raise ValueError(
            f"malformed deposit note (expected '{_NOTE_PREFIX}:<hex>:<hex>')"
        )
    try:
        return int(parts[1], 16), int(parts[2], 16)
    except ValueError:
        raise ValueError("deposit note hex components are not valid hex") from None


def _run_zk_helper(*args: str) -> str:
    """Run `node scripts/zk_helpers.js <args...>`, return stripped stdout.

    Raises RuntimeError if node is missing or the helper exits non-zero.
    """
    try:
        proc = subprocess.run(
            ["node", str(_ZK_HELPER_JS), *args],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "`node` not found on PATH — ZK mixer tools need Node.js"
        ) from None
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "no output"
        raise RuntimeError(f"zk_helpers.js {args[0]} failed: {detail}")
    return proc.stdout.strip()


def _find_snarkjs() -> str:
    """Locate the snarkjs binary, checking common install paths beyond PATH.

    npm's default `~/.npm-global/bin` is often not on PATH inside conda envs
    or subprocess-launched shells. Falls back to explicit path probing.
    """
    from shutil import which
    hit = which("snarkjs")
    if hit:
        return hit
    from pathlib import Path
    candidates = [
        Path.home() / ".npm-global" / "bin" / "snarkjs",
        Path.home() / ".nvm" / "versions" / "node" / "*" / "bin" / "snarkjs",
        Path("/usr/local/bin/snarkjs"),
        Path("/opt/homebrew/bin/snarkjs"),
    ]
    for c in candidates:
        if "*" in str(c):
            import glob
            hits = glob.glob(str(c))
            if hits:
                return hits[0]
        elif c.exists():
            return str(c)
    raise RuntimeError(
        "`snarkjs` not found. Install via `npm install -g snarkjs` and "
        "ensure ~/.npm-global/bin is on PATH, or set NPM prefix accordingly."
    )


def _run_snarkjs(*args: str) -> None:
    """Run `snarkjs <args...>`. Raises RuntimeError on missing binary or failure."""
    binary = _find_snarkjs()
    try:
        proc = subprocess.run([binary, *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(
            f"`snarkjs` binary vanished between lookup and exec: {binary}"
        ) from None
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "no output"
        raise RuntimeError(f"snarkjs {' '.join(args[:2])} failed: {detail}")


def _proof_to_solidity(proof: dict) -> tuple[list, list, list]:
    """Convert a snarkjs Groth16 proof.json into the (pA, pB, pC) calldata
    tuple the on-chain Groth16 verifier expects.

    pB's coordinate pairs are swapped — that's the snarkjs → Solidity
    calldata convention (the G2 point coordinates are stored in the
    opposite order on-chain).
    """
    pa = [int(proof["pi_a"][0]), int(proof["pi_a"][1])]
    pb = [
        [int(proof["pi_b"][0][1]), int(proof["pi_b"][0][0])],
        [int(proof["pi_b"][1][1]), int(proof["pi_b"][1][0])],
    ]
    pc = [int(proof["pi_c"][0]), int(proof["pi_c"][1])]
    return pa, pb, pc


class _PersistingWalletDict(dict):
    """dict subclass that appends (address, private_key) to disk on every
    insertion. Guarantees zero key-loss on process crash / power failure /
    kill signal — the file is fsync-safe append-only JSONL.

    Passing wallets_file=None makes it behave like a regular dict
    (used for Anvil ephemeral runs where key persistence is unnecessary).
    """

    def __init__(self, initial=None, wallets_file=None):
        super().__init__(initial or {})
        self._wallets_file = wallets_file
        # Snapshot the seed entries to disk immediately.
        if wallets_file is not None:
            for addr, key in list(self.items()):
                self._append(addr, key)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if self._wallets_file is not None:
            self._append(key, value)

    def _append(self, addr: str, key: str) -> None:
        try:
            import json as _json
            import time as _time
            from pathlib import Path as _Path
            p = _Path(self._wallets_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            # Append + fsync so a crash immediately after this line still
            # leaves the key on disk.
            with p.open("a") as f:
                f.write(_json.dumps({
                    "ts": _time.time(),
                    "address": addr,
                    "private_key": key,
                }) + "\n")
                f.flush()
                import os as _os
                _os.fsync(f.fileno())
        except Exception:   # noqa: BLE001 — never let persistence break dispatch
            pass


class ToolDispatcher:
    """Maps tool names to Python implementations and holds chain context.

    Args:
        w3: Web3 client connected to Anvil.
        usdt_contract: Deployed MockUSDT contract handle (or None for tests
            that only exercise schema introspection — USDT tools then return
            a clean error instead of crashing).
        wallets: dict mapping address -> private_key for wallets the attacker
            controls. Addresses are normalized to checksum form on insertion.
        pool_contract: Deployed MockUniswapV2Pool handle (or None). When None,
            the swap tools return a clean error.
        tornado_contract: Deployed MockTornado (ZK mixer) handle (or None).
            When None, the mixer tools return a clean error.
    """

    def __init__(
        self,
        w3: Web3,
        usdt_contract: Any,
        wallets: dict[str, str],
        pool_contract: Any = None,
        tornado_contract: Any = None,
        tornado_pools: dict | None = None,
        mixer_events_from_block: int = 0,
        logs_rpc_url: str | None = None,
        laundering_target_usd: float | None = None,
        notes_file: Any = None,
        wallets_file: Any = None,
        peel_budget_eth: float | None = None,
    ):
        self.w3 = w3
        self.usdt = usdt_contract
        self.pool = pool_contract
        # Multi-denomination mixer support. `tornado_pools` is the
        # authoritative dict {DENOMINATION_wei -> contract_handle} —
        # matches real Tornado Cash mainnet (0.1/1/10/100 ETH each a
        # separate pool). `tornado_contract` (singular) is kept for
        # backward compat: if only the singular is passed, we auto-
        # populate the dict with its on-chain DENOMINATION as the key.
        # `self.tornado` remains the "default" (1 ETH if present, else
        # the smallest available denom, else None) so existing code
        # paths keep working unchanged.
        self.tornado_pools: dict[int, Any] = {}
        if tornado_pools:
            for denom, ct in tornado_pools.items():
                self.tornado_pools[int(denom)] = ct
        if tornado_contract is not None and not tornado_pools:
            try:
                denom = int(tornado_contract.functions.DENOMINATION().call())
            except Exception:
                denom = 10**18   # legacy assumption
            self.tornado_pools[denom] = tornado_contract
        # Default pool for legacy code paths: 1 ETH if available, else
        # the smallest, else None.
        if self.tornado_pools:
            self.tornado = (
                self.tornado_pools.get(10**18)
                or self.tornado_pools[min(self.tornado_pools.keys())]
            )
        else:
            self.tornado = None
        # Path to a JSONL file where every mixer deposit note is appended
        # as {ts, from, tx_hash, leaf_index, commitment, note}. Safety net:
        # if the sub-agent's memory is lost (crash, halt, LLM context
        # eviction) or if a withdraw fails, the notes persist on disk so
        # a manual recovery script can still redeem the locked ETH later.
        # None = disabled (Anvil ephemeral runs where recovery doesn't
        # matter). Runners MUST set this for Sepolia.
        self.notes_file: Any = notes_file
        # Laundering target in USD — used to derive the max burner count.
        # Cap = max(30, min(250, 3 * ceil(usd / 999))), aligned with the
        # "moderate professional" laundering profile documented in AML
        # literature (Chainalysis 2023: ~$250-750 per intermediate wallet).
        # If None (legacy call), _burner_cap falls back to 100.
        self.laundering_target_usd: float | None = laundering_target_usd
        # Block from which _mixer_collect_leaves starts scanning Deposit
        # events. On live Sepolia this MUST be the tornado deploy block —
        # missing history rebuilds a stale local Merkle tree and withdraw
        # proofs fail on-chain `isKnownRoot()`.
        self.mixer_events_from_block = int(mixer_events_from_block)
        # Campaign-level cap on cumulative ETH locked in peel-chain sink
        # wallets. When set, `_peel_chain` refuses calls whose worst-case
        # projected loss would push `self._peel_locked_eth` past this
        # budget — guaranteeing the total sink lock stays under a policy
        # bound (typical: 5% of the campaign's laundering amount) even if
        # the LLM issues multiple peel_chain calls. `None` disables the
        # cap (legacy Anvil tests). Runners set this to 0.05 × amount.
        self._peel_budget_eth: float | None = peel_budget_eth
        self._peel_locked_eth: float = 0.0
        # Separate RPC for eth_getLogs. Alchemy free tier caps range at
        # 10 blocks — unusable for scanning ~10k blocks of mixer history.
        # publicnode.com allows 10k-block ranges free. If not provided we
        # fall back to `w3`; caller decides whether that's acceptable.
        if logs_rpc_url:
            # Retry-enabled session — a transient publicnode hiccup mid-
            # eth_getLogs would otherwise crash _mixer_collect_leaves.
            try:
                from requests import Session
                from requests.adapters import HTTPAdapter
                from urllib3.util.retry import Retry
                _retry = Retry(
                    total=5, backoff_factor=1.0,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=frozenset(["POST", "GET"]),
                    raise_on_status=False,
                )
                _sess = Session()
                _adapter = HTTPAdapter(max_retries=_retry)
                _sess.mount("http://", _adapter)
                _sess.mount("https://", _adapter)
                self._logs_w3 = Web3(Web3.HTTPProvider(logs_rpc_url, session=_sess))
            except Exception:   # noqa: BLE001 — fall back to unretried
                self._logs_w3 = Web3(Web3.HTTPProvider(logs_rpc_url))
        else:
            self._logs_w3 = w3
        # Pool of intermediate funder wallets for gas seeding — populated
        # lazily via bootstrap_funder_pool(). When populated, gas top-ups
        # pick a RANDOM funder instead of always coming from the deployer,
        # breaking the single-source co-funding heuristic that trivially
        # groups all campaign wallets in one graph hop.
        self._funder_pool: list[str] = []
        # Count of bulk-refill events (whole-pool refills from deployer).
        # Incremented in _pick_funder when the pool exhausts. Runners
        # surface this in meta.funder_pool.refill_events for reporting.
        self._funder_refill_events: int = 0
        # Write-through wallet registry: every insertion appends the new
        # (address, private_key) tuple to `wallets_file` on disk *before*
        # returning control. Guarantees that even if the process is killed,
        # crashes, or loses power in the middle of a run, every burner key
        # is on disk and its funds are sweepable via scripts/sweep_sepolia.py.
        # If wallets_file is None (Anvil ephemeral runs), behaves as a
        # regular dict. Root cause of the 2026-08-20 test disaster where
        # in-memory-only keys were lost when the process died.
        self._wallets_file: Any = wallets_file
        self.wallets: dict[str, str] = _PersistingWalletDict(
            {Web3.to_checksum_address(addr): key for addr, key in wallets.items()},
            wallets_file=wallets_file,
        )
        # The DEPLOYER is chain infrastructure (Anvil faucet / initial pool
        # liquidity source / test-network deployer key). It IS a signing
        # wallet the dispatcher can use for internal ops (_seed_gas source,
        # funder-pool bootstrap, funder refills, funder sweep destination)
        # but it is NEVER a valid attacker wallet for LLM-callable write
        # tools — the deployer holds 10_000 ETH from Anvil's genesis, so
        # letting the Coordinator route funds through it would inflate the
        # recovery metric by orders of magnitude. dispatch() enforces this.
        # First wallet is by convention the deployer (see run_campaign.py).
        self._deployer_addr: str | None = (
            next(iter(self.wallets)) if self.wallets else None
        )
        # Wallets the attacker explicitly registered as intended clean
        # exits (off-ramp destinations) via register_clean_exit. Each entry
        # is {address, exchange_platform, note?} and is consumed at end-of-
        # campaign by run_campaign to write ground-truth labels into
        # addresses.json. Distinct from `wallets` (the signing registry)
        # because the SAME address is in both — clean exits ARE wallets the
        # dispatcher can sign for, they just carry extra label metadata.
        self.registered_clean_exits: list[dict] = []

    @property
    def tool_definitions(self) -> list[dict]:
        """Tool schemas in Anthropic's `tools=` parameter format."""
        return list(_TOOL_SCHEMAS)

    def register_wallet(self, address: str, private_key: str) -> None:
        """Add a wallet (e.g. a freshly-generated burner) to the registry."""
        self.wallets[Web3.to_checksum_address(address)] = private_key

    def dispatch(self, tool_name: str, tool_input: dict) -> ToolResult:
        """Execute the named tool with the given input dict.

        Defensively filters tool_input to only the kwargs the target method
        accepts. LLMs occasionally hallucinate extra parameters (e.g.
        Sonnet 4.6 was observed passing `note` to `transfer_eth` on Sepolia
        2026-08-11); a hard TypeError would crash the whole campaign. The
        filter silently drops unknown kwargs so the tool proceeds with the
        LLM's other (valid) inputs.
        """
        # Guard: reject any LLM tool call that names the deployer as an
        # attacker wallet. The deployer is chain infrastructure holding
        # the Anvil 10_000 ETH genesis balance + initial MockUSDT mint —
        # if the Coordinator routes swaps or transfers through it, the
        # attacker effectively laundered chain-supply funds instead of
        # (or in addition to) Alice's stolen amount. Observed 2026-08-14
        # inflating recovery to ~200%. Applies to sender ("from_address")
        # and to counterparty roles ("to_address", "recipient",
        # "gas_payer") because either direction lets the LLM interact
        # with the infrastructure wallet.
        if self._deployer_addr:
            SENDER_KEYS = ("from_address", "gas_payer", "sender")
            RECIPIENT_KEYS = ("to_address", "recipient", "destination")
            for k in SENDER_KEYS + RECIPIENT_KEYS:
                v = tool_input.get(k)
                if isinstance(v, str):
                    try:
                        if Web3.to_checksum_address(v) == self._deployer_addr:
                            return ToolResult(error=(
                                f"Rejected: {k}={v} is the deployer "
                                f"(chain infrastructure — Anvil faucet + "
                                f"initial pool liquidity source, holds "
                                f"10_000 ETH from genesis). Do NOT route "
                                f"laundering through the deployer; it "
                                f"would launder chain-supply funds and "
                                f"inflate recovery metrics past 100%. "
                                f"Use Alice, attacker-generated burners, "
                                f"or registered clean exits instead."
                            ))
                    except (ValueError, TypeError):
                        pass   # invalid checksum falls through to the tool
        import inspect
        method = getattr(self, f"_{tool_name}", None)
        if method is not None:
            sig = inspect.signature(method)
            if not any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values()
            ):
                declared = set(sig.parameters.keys())
                tool_input = {
                    k: v for k, v in tool_input.items() if k in declared
                }

        if tool_name == "get_balance":
            return self._get_balance(**tool_input)
        if tool_name == "transfer_usdt":
            return self._transfer_usdt(**tool_input)
        if tool_name == "generate_burner_wallet":
            return self._generate_burner_wallet(**tool_input)
        if tool_name == "register_clean_exit":
            return self._register_clean_exit(**tool_input)
        if tool_name == "mint_usdt":
            return self._mint_usdt(**tool_input)
        if tool_name == "smurf_split":
            return self._smurf_split(**tool_input)
        if tool_name == "transfer_eth":
            return self._transfer_eth(**tool_input)
        if tool_name == "smurf_eth_split":
            return self._smurf_eth_split(**tool_input)
        if tool_name == "get_gas_budget":
            return self._get_gas_budget(**tool_input)
        if tool_name == "get_swap_quote":
            return self._get_swap_quote(**tool_input)
        if tool_name == "swap_eth_for_usdt":
            return self._swap_eth_for_usdt(**tool_input)
        if tool_name == "swap_usdt_for_eth":
            return self._swap_usdt_for_eth(**tool_input)
        if tool_name == "mixer_deposit":
            return self._mixer_deposit(**tool_input)
        if tool_name == "mixer_withdraw":
            return self._mixer_withdraw(**tool_input)
        if tool_name == "mixer_batch_deposit":
            return self._mixer_batch_deposit(**tool_input)
        if tool_name == "mixer_batch_withdraw":
            return self._mixer_batch_withdraw(**tool_input)
        if tool_name == "peel_chain":
            return self._peel_chain(**tool_input)
        if tool_name == "advance_blocks":
            return self._advance_blocks(**tool_input)
        if tool_name == "inspect_chain":
            return self._inspect_chain(**tool_input)
        return ToolResult(error=f"Unknown tool: {tool_name}")

    # --- Tool implementations -----------------------------------------------

    def _get_balance(self, address: str, asset: str) -> ToolResult:
        try:
            address = Web3.to_checksum_address(address)
        except ValueError:
            return ToolResult(error=f"Invalid address: {address}")

        if asset == "ETH":
            wei = self.w3.eth.get_balance(address)
            return ToolResult(output={"asset": "ETH", "balance": wei / 10**18})
        if asset == "USDT":
            if self.usdt is None:
                return ToolResult(error="USDT contract not set on dispatcher")
            base = self.usdt.functions.balanceOf(address).call()
            return ToolResult(output={"asset": "USDT", "balance": base / 10**6})
        return ToolResult(error=f"Unsupported asset: {asset!r}")

    def _transfer_usdt(
        self, from_address: str, to_address: str, amount_usdt: float
    ) -> ToolResult:
        if self.usdt is None:
            return ToolResult(error="USDT contract not set on dispatcher")

        try:
            from_address = Web3.to_checksum_address(from_address)
            to_address = Web3.to_checksum_address(to_address)
        except ValueError as e:
            return ToolResult(error=f"Invalid address: {e}")

        if from_address not in self.wallets:
            return ToolResult(
                error=f"No private key registered for sender {from_address}"
            )

        if amount_usdt <= 0:
            return ToolResult(error=f"Amount must be positive, got {amount_usdt}")

        amount_base = int(amount_usdt * 10**6)

        # Pre-transfer: ensure sender has gas dust. Prevents "insufficient
        # funds" reverts when a wallet has relayed many times and burned
        # through its initial 0.05 ETH seed.
        self._ensure_gas_dust(from_address)

        try:
            tx = self.usdt.functions.transfer(to_address, amount_base).build_transaction({
                "from": from_address,
                "nonce": self.w3.eth.get_transaction_count(from_address, "pending"),
                "gas": 200_000,
                "gasPrice": self.w3.eth.gas_price,
            })
            signed = self.w3.eth.account.sign_transaction(
                tx, private_key=self.wallets[from_address]
            )
            tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        except Exception as e:
            return ToolResult(error=f"Transfer raised: {e}")

        if receipt.status != 1:
            return ToolResult(
                error=f"Transfer reverted on-chain (tx_hash={tx_hash.hex()})"
            )

        # Post-transfer: ensure recipient has gas dust so any downstream
        # move of this USDT (relay, exit off-ramp, etc.) can pay for
        # itself. No-op if recipient is external / already funded.
        self._ensure_gas_dust(to_address)

        return ToolResult(output={
            "tx_hash": tx_hash.hex(),
            "from_address": from_address,
            "to_address": to_address,
            "amount_usdt": amount_usdt,
            "gas_used": receipt.gasUsed,
        })

    # --- New tools (PR 5.4) -------------------------------------------------

    def _burner_cap(self) -> int:
        """Max burner count for this campaign, derived from laundering scale.

        Formula: max(30, min(250, 3 * ceil(usd / 999))). Corresponds to the
        "moderate professional" laundering profile in Chainalysis 2023
        (~$250-750 per intermediate wallet), aggressive enough for realistic
        sophistication without allowing the runaway loops observed in
        seed 306 (Haiku, 236 burners for 1 ETH) and seeds 401/402 (Sonnet
        WSL-crashing loops). Absolute floor 30 protects small runs; cap 250
        aligns with Bybit-tier sophistication for large runs (up to 125 ETH
        scope per the thesis).
        """
        import math
        if self.laundering_target_usd is None:
            return 100   # legacy fallback for callers that don't pass the target
        return max(30, min(250, 3 * math.ceil(self.laundering_target_usd / 999)))

    def _current_burner_count(self) -> int:
        """Burners generated so far (excludes deployer, alice, funders, exits)."""
        return max(
            0,
            len(self.wallets)
            - 2                                     # deployer + alice
            - len(self._funder_pool)
            - len(self.registered_clean_exits),
        )

    def _generate_burner_wallet(self) -> ToolResult:
        """Create a fresh keypair, register it, auto-seed it with gas dust.

        The seed comes from the faucet wallet (first registered) so the new
        burner can immediately pay gas as a sender. Without this, USDT
        transferred to the burner would be stranded — burners with no ETH
        can't even submit transactions. Matches the real-world pattern where
        the operator drips gas dust into each disposable hop wallet.

        Hard-capped at _burner_cap() to prevent runaway generation loops
        (observed empirically in seed 306 Haiku: 188 empty generate calls
        in a single Layering sub-agent). Forces the Coordinator to STOP
        creating new wallets and USE the ones it already has.
        """
        cap = self._burner_cap()
        count = self._current_burner_count()
        if count >= cap:
            return ToolResult(error=(
                f"HARD LIMIT reached: {count} burners already generated in "
                f"this campaign (cap: {cap}, derived from laundering value "
                f"${self.laundering_target_usd or 0:,.0f} USD). Do NOT "
                f"generate more burners. Instead: (a) use existing burners "
                f"for the next layering hop, (b) consolidate USDT to a "
                f"burner you already control, or (c) route funds to the "
                f"clean exits you registered. Generating more burners "
                f"without a specific plan is a known failure mode that "
                f"inflates gas costs and chain state without improving "
                f"GNN evasion."
            ))
        acct = Account.create()
        address = acct.address  # already checksummed by eth_account
        # acct.key is a HexBytes; .hex() produces the 0x-prefixed string
        self.wallets[address] = acct.key.hex()

        # Seed from faucet so the burner can pay gas. If seeding fails
        # (no faucet, faucet broke, RPC hiccup) the burner is still
        # registered — caller gets a warning and zero gas_seed_eth so they
        # can react.
        try:
            self._seed_gas(address, _DEFAULT_GAS_RESERVE_ETH,
                           source=self._pick_funder())
            return ToolResult(output={
                "address": address,
                "gas_seed_eth": _DEFAULT_GAS_RESERVE_ETH,
                "burners_in_campaign": count + 1,
                "cap_remaining": cap - (count + 1),
            })
        except Exception as e:   # noqa: BLE001 — surface to LLM as a warning
            return ToolResult(output={
                "address": address,
                "gas_seed_eth": 0.0,
                "warning": f"Burner registered but gas seeding failed: {e}",
            })

    def _register_clean_exit(
        self, exchange_platform: str, note: str | None = None,
    ) -> ToolResult:
        """Create a fresh wallet and register it as an intended clean exit.

        Same gas-seeding behaviour as _generate_burner_wallet — the new exit
        wallet is auto-funded with 0.05 ETH so it can immediately receive
        and (in principle) move USDT. The difference is the extra metadata
        recorded in self.registered_clean_exits: this is what tells the
        post-campaign artifact writer which wallets count as labeled
        off-ramp destinations for detector ground truth.
        """
        if not isinstance(exchange_platform, str) or not exchange_platform.strip():
            return ToolResult(error="exchange_platform must be a non-empty string")
        platform = exchange_platform.strip()

        acct = Account.create()
        address = acct.address
        self.wallets[address] = acct.key.hex()

        entry: dict = {"address": address, "exchange_platform": platform}
        if note is not None:
            note_str = str(note).strip()
            if note_str:
                entry["note"] = note_str[:200]   # cap to keep artifacts tidy
        self.registered_clean_exits.append(entry)

        # Auto-seed gas dust — same as burners. If seeding fails the exit
        # is still registered and the agent gets a warning.
        try:
            self._seed_gas(address, _DEFAULT_GAS_RESERVE_ETH,
                           source=self._pick_funder())
            return ToolResult(output={
                "address": address,
                "exchange_platform": platform,
                "gas_seed_eth": _DEFAULT_GAS_RESERVE_ETH,
            })
        except Exception as e:   # noqa: BLE001
            return ToolResult(output={
                "address": address,
                "exchange_platform": platform,
                "gas_seed_eth": 0.0,
                "warning": f"Clean exit registered but gas seeding failed: {e}",
            })

    def bootstrap_funder_pool(
        self,
        num_funders: int | None = None,
        eth_per_funder: float = 1.0,
        *,
        amounts_eth: list[float] | None = None,
    ) -> None:
        """Create funder wallets, each funded from the deployer.

        Two invocation modes:
          - Legacy: pass `num_funders` and `eth_per_funder` — all funders
            get the same amount. Kept so older tests + runs stay
            reproducible.
          - Preferred: pass `amounts_eth` — one funder per element, each
            seeded with its own amount. Enables non-uniform (e.g. random-
            per-funder) balances that break the "all funders identical"
            detection signal.

        Once bootstrapped, all subsequent gas-seeding operations (initial
        wallet dust, runtime top-ups) pick a RANDOM funder from the pool
        instead of going straight to the deployer. Breaks the single-
        source co-funding heuristic that would otherwise let a GNN
        clustering detector group all campaign wallets in one hop.

        Idempotent — a second call is a no-op. Bootstrap failures on any
        individual funder are surfaced by falling back to fewer funders
        (the pool is whatever succeeded).

        Call this once at the top of a campaign, after the deployer has
        been registered and before any burners are generated. Reproducible
        with random.seed(seed) since _pick_funder uses the global random
        state.
        """
        if self._funder_pool:
            return
        if not self.wallets:
            raise RuntimeError("cannot bootstrap funder pool without deployer")
        if amounts_eth is None:
            if num_funders is None:
                raise ValueError(
                    "bootstrap_funder_pool: pass either amounts_eth or "
                    "num_funders (+ eth_per_funder)"
                )
            amounts_eth = [eth_per_funder] * num_funders
        for amount in amounts_eth:
            acct = Account.create()
            self.wallets[acct.address] = acct.key.hex()
            try:
                self._seed_gas(acct.address, amount)
                self._funder_pool.append(acct.address)
            except Exception:   # noqa: BLE001
                # Partial pool is still better than none; skip this funder.
                del self.wallets[acct.address]

    def _pick_funder(self) -> str | None:
        """Random funder from the pool. None if pool not bootstrapped.

        If the picked funder has fallen below a minimum liquidity floor
        (0.1 ETH — enough for ~2 seed txs on Sepolia), refill it from the
        deployer before returning. Prevents silent-fail cascades where a
        bankrupt funder returns from _pick_funder, _seed_gas via that
        funder throws (insufficient funds), and the caller's caught
        exception silently strands the wallet. Refill amount is
        deliberately small (0.5 ETH) so the co-funding signal stays
        distributed across funders instead of concentrating on one.
        """
        if not self._funder_pool:
            return None

        # Two-stage rotation (2026-08-14): prefer any funder still
        # holding >= min_liquidity ETH. If NONE are active, refill only
        # ONE funder (a random pick) from the deployer and return it.
        # Rationale: a real attacker rotates through cold wallets and
        # reloads them one at a time from cold storage — not a continuous
        # drip, and not a bulk pool reload. Detection-wise the deployer
        # is quiet during rotation windows and only fires a single edge
        # when a funder needs to come back online.
        min_liquidity_wei = int(0.01 * 10**18)   # 2× the 0.005 seed floor
        refill_eth = 0.025  # 5 seeds worth at 0.005 each — modest bump
        deployer = next(iter(self.wallets))

        try:
            active = [
                f for f in self._funder_pool
                if self.w3.eth.get_balance(f) >= min_liquidity_wei
            ]
        except Exception:   # noqa: BLE001
            active = list(self._funder_pool)   # if RPC fails, fall through

        if active:
            return random.choice(active)

        # Whole pool exhausted: refill just ONE (random) funder and
        # return it. The rest stay out of service until they too get
        # refilled in a later exhaustion event.
        funder = random.choice(self._funder_pool)
        if funder != deployer:
            try:
                self._seed_gas(funder, refill_eth, source=deployer)
                self._funder_refill_events += 1
            except Exception:   # noqa: BLE001 — best-effort refill
                pass
        return funder

    def _seed_gas(
        self, recipient: str, amount_eth: float, source: str | None = None,
    ) -> str:
        """Send `amount_eth` ETH from `source` (default: faucet) to `recipient`.

        If `source` is None, uses the first registered wallet (canonical
        deployer/faucet). Callers that want random-funder obfuscation pass
        the result of `_pick_funder()` (may be None if pool not
        bootstrapped — then falls through to deployer, preserving legacy
        behaviour).

        Uses EIP-1559 gas fields (maxFeePerGas / maxPriorityFeePerGas) with
        a 2× base_fee headroom. The old legacy `gasPrice` field silently
        failed on Sepolia when base_fee ticked up between fetch and submit,
        leaving clean_exits with 0 ETH — the 2026-08-13 stranded-wallet
        symptom that motivated the rescue feature in sweep_sepolia.py.
        """
        if not self.wallets:
            raise RuntimeError("no registered wallet to seed gas from")
        if amount_eth <= 0:
            raise RuntimeError(f"seed amount must be positive, got {amount_eth}")

        if source is None:
            source = next(iter(self.wallets))
        elif source not in self.wallets:
            raise RuntimeError(f"source wallet {source} not registered")
        faucet = source
        faucet_key = self.wallets[faucet]
        recipient = Web3.to_checksum_address(recipient)

        latest = self.w3.eth.get_block("latest")
        base_fee = latest.get("baseFeePerGas")
        if base_fee is None:
            # Non-EIP-1559 chain — fall back to legacy gasPrice.
            tx = {
                "from": faucet, "to": recipient,
                "value": int(amount_eth * 10**18),
                "nonce": self.w3.eth.get_transaction_count(faucet, "pending"),
                "gas": _ETH_TRANSFER_GAS,
                "gasPrice": self.w3.eth.gas_price,
                "chainId": self.w3.eth.chain_id,
            }
        else:
            priority = self.w3.to_wei(1, "gwei")
            max_fee = base_fee * 2 + priority
            tx = {
                "from": faucet, "to": recipient,
                "value": int(amount_eth * 10**18),
                "nonce": self.w3.eth.get_transaction_count(faucet, "pending"),
                "gas": _ETH_TRANSFER_GAS,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": priority,
                "chainId": self.w3.eth.chain_id,
            }
        signed = self.w3.eth.account.sign_transaction(tx, private_key=faucet_key)
        tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.status != 1:
            raise RuntimeError(f"gas seed tx reverted (tx_hash={tx_hash.hex()})")
        return tx_hash.hex()

    def _ensure_gas_dust(
        self, address: str, min_eth: float = _DEFAULT_GAS_RESERVE_ETH,
    ) -> None:
        """Top up `address` to `min_eth` ETH from the faucet if below floor.

        Called before/after tx-emitting operations to prevent wallets from
        running out of gas mid-campaign. Silent no-op when:
          - address not in our wallet registry (can't sign for it)
          - address IS the faucet (would be recursive)
          - balance already >= min_eth
          - top-up tx fails (best-effort; sweep_sepolia rescue is the
            second-line safety net)
        """
        try:
            address = Web3.to_checksum_address(address)
        except ValueError:
            return
        if address not in self.wallets or not self.wallets:
            return
        faucet = next(iter(self.wallets))
        if address == faucet:
            return
        min_wei = int(min_eth * 10**18)
        try:
            current_wei = self.w3.eth.get_balance(address)
        except Exception:   # noqa: BLE001
            return
        if current_wei >= min_wei:
            return
        top_up_eth = (min_wei - current_wei) / 10**18
        try:
            # Random funder from pool if bootstrapped; else fallback to
            # deployer. `source=None` means _seed_gas uses the deployer.
            self._seed_gas(address, top_up_eth, source=self._pick_funder())
        except Exception:   # noqa: BLE001 — best-effort
            pass

    def _forward_stranded_usdt(self, from_addr: str) -> dict:
        """After rescuing gas, complete the laundering path for a stranded wallet.

        Semantic: the whole point of moving USDT through a burner is to
        route it further toward a clean_exit off-ramp. If Sonnet ran out
        of iterations before finishing that route, the intermediate burner
        is stuck holding USDT that never reached its final destination.

        Fix: forward the balance to random registered clean_exits, in
        sub-$999 chunks (CTR threshold). Skips wallets that ARE clean_exits
        themselves (already at destination). No-op if no exits are
        registered (nothing to forward to).

        Returns: {"forwarded_usdt": float, "chunks": int, "destinations": list[str]}
        """
        empty = {"forwarded_usdt": 0.0, "chunks": 0, "destinations": []}
        if self.usdt is None or not self.registered_clean_exits:
            return empty
        exit_addrs = [e["address"] for e in self.registered_clean_exits]
        if from_addr in set(exit_addrs):
            return empty   # already at destination — leave it
        try:
            usdt_wei = self.usdt.functions.balanceOf(from_addr).call()
        except Exception:   # noqa: BLE001
            return empty
        if usdt_wei <= 0:
            return empty

        # $999 CTR cap in USDT base units (6 decimals). Use $980 as the
        # per-chunk soft cap to leave headroom for rounding.
        chunk_cap_wei = int(980 * 10**6)
        total_forwarded = 0
        destinations: list[str] = []
        chunks = 0
        remaining = usdt_wei

        # Cap iterations at 20 to prevent runaway if something goes wrong.
        for _ in range(20):
            if remaining <= 0:
                break
            # Pick a random exit; multiple chunks may go to the same exit
            # if the pool is small — that's fine, real launderers do too.
            dest = random.choice(exit_addrs)
            chunk_wei = min(remaining, chunk_cap_wei)
            chunk_usdt = chunk_wei / 10**6
            try:
                result = self._transfer_usdt(from_addr, dest, chunk_usdt)
            except Exception:   # noqa: BLE001
                break
            if result.error is not None:
                break
            total_forwarded += chunk_wei
            destinations.append(dest)
            chunks += 1
            remaining -= chunk_wei

        return {
            "forwarded_usdt": total_forwarded / 10**6,
            "chunks": chunks,
            "destinations": destinations,
        }

    def rescue_stranded_wallets(
        self, min_eth: float = _DEFAULT_GAS_RESERVE_ETH,
    ) -> dict:
        """Post-campaign safety net: top-up any wallet stranded with USDT.

        Called by the runner right before writing artifacts. Does two passes:

          1. Scan every wallet we hold a key for. Any wallet with USDT > 0
             but ETH below `usdt_gas_wei` (65k * gas_price × 1.2) is
             stranded — it holds value it cannot move.
          2. For each stranded wallet, top up from _pick_funder() (random
             funder from the pool, or the deployer if the pool is empty).
             Uses _ensure_gas_dust so it inherits the funder-obfuscation
             behaviour.
          3. Re-check the same set. Any wallet still stranded after top-up
             is a hard failure signal (funder pool exhausted, RPC issues,
             etc.) — surfaced in the return dict for the runner to log.

        Returns a metrics dict suitable for embedding in campaign.json:
          {
            "stranded_before": int,   # wallets that had USDT but no gas
            "rescued": int,            # of those, successfully topped up
            "stranded_after": int,     # still stranded after our rescue
            "stranded_addresses": list[str],   # the after-set
          }
        """
        empty = {
            "stranded_before": 0, "rescued": 0, "stranded_after": 0,
            "stranded_addresses": [], "forwarded": [],
        }
        if self.usdt is None:
            return empty

        # Gas-cost threshold for the ETH balance check.
        try:
            gas_price = self.w3.eth.gas_price
        except Exception:   # noqa: BLE001
            gas_price = self.w3.to_wei(3, "gwei")
        usdt_gas_wei = 65_000 * int(gas_price * 1.2)

        # Skip DUST-stranded wallets: if the USDT is worth less than the
        # gas it would take to rescue + forward it, rescue is negative-
        # value work. Floor at $5 USDT (5e6 base units) — a rescue costs
        # ~86k gas across two txs (top-up + forward). At 3 gwei on
        # Sepolia and the mock pool rate of ~6,255 USDT/ETH that's about
        # $1.60; the $5 floor gives a comfortable ~3x margin so we stay
        # net-positive even under gas spikes, without missing any real
        # laundering-scale amount.
        min_stranded_usdt_wei = 5_000_000

        # Pass 1: identify stranded set (worth-rescuing only).
        stranded_before: list[str] = []
        for addr in list(self.wallets.keys()):
            try:
                eth = self.w3.eth.get_balance(addr)
            except Exception:   # noqa: BLE001
                continue
            if eth >= usdt_gas_wei:
                continue
            try:
                usdt = self.usdt.functions.balanceOf(addr).call()
            except Exception:   # noqa: BLE001
                continue
            if usdt >= min_stranded_usdt_wei:
                stranded_before.append(addr)

        # Pass 2: top up each stranded wallet from a random funder.
        for addr in stranded_before:
            self._ensure_gas_dust(addr, min_eth=min_eth)

        # Pass 2.5: forward stranded USDT to random clean_exits so the
        # laundering path completes. Skips wallets that ARE clean_exits
        # (already at destination). Splits into sub-$999 chunks.
        forwarded_reports: list[dict] = []
        for addr in stranded_before:
            fw = self._forward_stranded_usdt(addr)
            if fw["forwarded_usdt"] > 0:
                forwarded_reports.append({"from": addr, **fw})

        # Pass 3: re-check. A wallet is still stranded if it holds
        # more than dust USDT and less than gas-worth of ETH. The
        # forwarding pass may have emptied its USDT — that also counts
        # as no longer stranded (nothing left to move).
        stranded_after: list[str] = []
        for addr in stranded_before:
            try:
                usdt = self.usdt.functions.balanceOf(addr).call()
                if usdt < min_stranded_usdt_wei:
                    continue   # forwarding worked, nothing left
                eth = self.w3.eth.get_balance(addr)
                if eth >= usdt_gas_wei:
                    continue   # rescue put gas in place
                stranded_after.append(addr)
            except Exception:   # noqa: BLE001
                stranded_after.append(addr)

        return {
            "stranded_before": len(stranded_before),
            "rescued": len(stranded_before) - len(stranded_after),
            "stranded_after": len(stranded_after),
            "stranded_addresses": stranded_after,
            "forwarded": forwarded_reports,
        }

    def sweep_funder_pool(self, destination: str | None = None) -> dict:
        """Return residual ETH from every funder wallet back to `destination`.

        Called at end-of-campaign so the funder pool is left at ~0 ETH,
        letting the runner compute the TRUE gas cost as
        (initial pool allocated) - (amount recovered by sweep). Anything
        that isn't recovered was consumed by chain gas or refills into
        burners the funder seeded. Matches Sepolia's manual sweep
        semantics (scripts/sweep_sepolia.py) so both chains behave the
        same.

        For each funder in the pool:
          1. Query current balance.
          2. If balance > gas cost of a plain ETH transfer, send
             (balance - gas cost) back to `destination`.
          3. Skip funders whose balance is below the gas floor (nothing
             worth sweeping — they're already effectively empty).

        `destination`: address to send the recovered ETH. Defaults to
        the first registered wallet (canonical deployer/faucet).

        Returns a dict suitable for embedding in campaign metadata:
          {
            "num_funders": int,       # funders in the pool at sweep time
            "swept": int,             # number of funders that returned ETH
            "skipped": int,           # funders below the gas floor
            "failed": list[str],      # funders whose sweep tx failed
            "total_returned_wei": int,
            "total_returned_eth": float,
            "per_funder": {addr: {"balance_before_wei": int,
                                  "returned_wei": int,
                                  "status": "swept" | "skipped" | "failed"}}
          }
        """
        if destination is None:
            destination = next(iter(self.wallets))
        destination = Web3.to_checksum_address(destination)

        try:
            gas_price = self.w3.eth.gas_price
        except Exception:   # noqa: BLE001
            gas_price = self.w3.to_wei(3, "gwei")
        gas_cost_wei = _ETH_TRANSFER_GAS * int(gas_price * 1.2)

        report: dict = {
            "num_funders": len(self._funder_pool),
            "swept": 0,
            "skipped": 0,
            "failed": [],
            "total_returned_wei": 0,
            "per_funder": {},
        }

        for funder in list(self._funder_pool):
            balance_wei = self.w3.eth.get_balance(funder)
            entry = {
                "balance_before_wei": balance_wei,
                "returned_wei": 0,
                "status": "skipped",
            }

            if balance_wei <= gas_cost_wei:
                report["skipped"] += 1
                report["per_funder"][funder] = entry
                continue

            send_amount_wei = balance_wei - gas_cost_wei
            try:
                tx = {
                    "from": funder,
                    "to": destination,
                    "value": send_amount_wei,
                    "nonce": self.w3.eth.get_transaction_count(funder, "pending"),
                    "gas": _ETH_TRANSFER_GAS,
                    "gasPrice": gas_price,
                    "chainId": self.w3.eth.chain_id,
                }
                signed = self.w3.eth.account.sign_transaction(
                    tx, private_key=self.wallets[funder]
                )
                tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                if receipt.status == 1:
                    entry["returned_wei"] = send_amount_wei
                    entry["status"] = "swept"
                    report["swept"] += 1
                    report["total_returned_wei"] += send_amount_wei
                else:
                    entry["status"] = "failed"
                    report["failed"].append(funder)
            except Exception:   # noqa: BLE001
                entry["status"] = "failed"
                report["failed"].append(funder)

            report["per_funder"][funder] = entry

        report["total_returned_eth"] = report["total_returned_wei"] / 10**18
        return report

    def _peel_chain(
        self, from_address: str, asset: str, initial_amount: float,
        num_hops: int = 6, peel_pct: float = 0.02,
        peel_jitter: float = 0.0, seed: int | None = None,
    ) -> ToolResult:
        """Execute a peel chain — canonical laundering technique.

        At each of `num_hops` steps, generates two fresh burner wallets:
        one 'sink' that receives a jittered fraction of the current amount
        (dormant peeled-off value) and one 'continuation' that receives
        the rest and becomes the sender for the next hop.

        With `peel_jitter > 0`, each per-hop peel is sampled uniformly
        from `[peel_pct*(1-jitter), peel_pct*(1+jitter)]`. jitter=0 keeps
        the legacy deterministic behavior (all peels equal to peel_pct).
        `seed` makes the jitter reproducible for testing; None uses the
        module's default RNG.

        A dispatcher-level `peel_budget_eth` cap (set at construction)
        rejects the call if the projected total sink loss would push the
        campaign's cumulative peel-locked ETH past the budget — this
        guarantees the campaign-wide constraint even if the LLM issues
        multiple peel_chain calls.
        """
        # Validation
        if asset not in ("ETH", "USDT"):
            return ToolResult(error=f"asset must be 'ETH' or 'USDT', got {asset!r}")
        if num_hops < 1 or num_hops > 100:
            return ToolResult(error=f"num_hops must be in [1, 100], got {num_hops}")
        if peel_pct < 0.01 or peel_pct > 0.20:
            return ToolResult(error=f"peel_pct must be in [0.01, 0.20], got {peel_pct}")
        if peel_jitter < 0.0 or peel_jitter > 1.0:
            return ToolResult(error=f"peel_jitter must be in [0, 1], got {peel_jitter}")
        try:
            from_address = Web3.to_checksum_address(from_address)
        except ValueError:
            return ToolResult(error=f"Invalid from_address: {from_address}")
        if from_address not in self.wallets:
            return ToolResult(error=f"from_address {from_address} not in wallet registry")

        # Campaign-level cap FIRST — before any chain reads. If the
        # projected sink loss would push cumulative peel-locked ETH past
        # the dispatcher's budget, refuse fast so the LLM gets a clear
        # actionable error before we even query the sender's balance. We
        # project using peel_pct * (1+jitter) as the per-hop upper bound
        # to be conservative (worst-case draw sequence).
        if self._peel_budget_eth is not None and asset == "ETH":
            worst_peel = peel_pct * (1.0 + peel_jitter)
            projected_loss_eth = initial_amount * (
                1.0 - (1.0 - worst_peel) ** num_hops
            )
            projected_total = self._peel_locked_eth + projected_loss_eth
            if projected_total > self._peel_budget_eth:
                return ToolResult(error=(
                    f"peel_chain would exceed campaign peel budget: "
                    f"already locked {self._peel_locked_eth:.4f} ETH in "
                    f"sinks; this call could add up to "
                    f"{projected_loss_eth:.4f} ETH (worst case), pushing "
                    f"total to {projected_total:.4f} ETH; cap is "
                    f"{self._peel_budget_eth:.4f} ETH (5% of campaign). "
                    f"Reduce initial_amount, num_hops, or peel_pct."
                ))

        # Verify sender has the initial amount
        if asset == "ETH":
            sender_balance = self.w3.eth.get_balance(from_address) / 10**18
        else:  # USDT
            if self.usdt is None:
                return ToolResult(error="USDT contract not set on dispatcher")
            sender_balance = self.usdt.functions.balanceOf(from_address).call() / 10**6
        if sender_balance < initial_amount:
            return ToolResult(error=(
                f"insufficient {asset}: sender has {sender_balance:.6f}, "
                f"needs {initial_amount:.6f}"
            ))

        # RNG for per-hop jitter — module-random by default; seedable for
        # tests. peel_jitter=0 reproduces the legacy deterministic path.
        jitter_rng = random.Random(seed) if seed is not None else random

        # Execute the chain
        hops: list[str] = []            # continuation wallets (main flow)
        peels: list[dict] = []          # peel-off wallets + amounts
        peel_pcts: list[float] = []     # actual jittered pct used per hop
        current_sender = from_address
        current_amount = initial_amount

        for hop_index in range(num_hops):
            # Generate two fresh burners for this hop
            cont_acct = Account.create()
            cont_addr = cont_acct.address
            self.wallets[cont_addr] = cont_acct.key.hex()
            peel_acct = Account.create()
            peel_addr = peel_acct.address
            self.wallets[peel_addr] = peel_acct.key.hex()

            # Seed both with gas dust (best-effort; a failure here just
            # means the burner cannot pay gas onward — for peel-sinks
            # that is acceptable since they are dormant by design).
            try:
                self._seed_gas(cont_addr, _DEFAULT_GAS_RESERVE_ETH,
                               source=self._pick_funder())
            except Exception:   # noqa: BLE001
                pass
            try:
                self._seed_gas(peel_addr, _DEFAULT_GAS_RESERVE_ETH,
                               source=self._pick_funder())
            except Exception:   # noqa: BLE001
                pass

            # Sample this hop's peel pct with optional jitter around the
            # target mean. peel_jitter=0 → deterministic (legacy path).
            if peel_jitter > 0.0:
                lo = peel_pct * (1.0 - peel_jitter)
                hi = peel_pct * (1.0 + peel_jitter)
                actual_peel_pct = jitter_rng.uniform(lo, hi)
            else:
                actual_peel_pct = peel_pct
            peel_pcts.append(actual_peel_pct)
            peel_amount = current_amount * actual_peel_pct
            cont_amount = current_amount - peel_amount

            # Send peel-off + continuation. Both fail-fast on error.
            # Local nonce tracking per hop: two txs from the same sender
            # in tight succession will race on `get_transaction_count`
            # (even with "pending", Alchemy's eventual consistency across
            # backend nodes can return the same value twice). Read once,
            # increment locally.
            if asset == "ETH":
                sender_key = self.wallets[current_sender]
                local_nonce = self.w3.eth.get_transaction_count(
                    current_sender, "pending"
                )
                for target_addr, amt in ((peel_addr, peel_amount), (cont_addr, cont_amount)):
                    tx = {
                        "from": current_sender,
                        "to": target_addr,
                        "value": int(amt * 10**18),
                        "nonce": local_nonce,
                        "gas": _ETH_TRANSFER_GAS,
                        "gasPrice": self.w3.eth.gas_price,
                        "chainId": self.w3.eth.chain_id,
                    }
                    signed = self.w3.eth.account.sign_transaction(tx, private_key=sender_key)
                    tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
                    receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                    local_nonce += 1
                    if receipt.status != 1:
                        return ToolResult(error=(
                            f"peel chain hop {hop_index} reverted (tx {tx_hash.hex()})"
                        ))
            else:  # USDT
                sender_key = self.wallets[current_sender]
                local_nonce = self.w3.eth.get_transaction_count(
                    current_sender, "pending"
                )
                for target_addr, amt in ((peel_addr, peel_amount), (cont_addr, cont_amount)):
                    base = int(amt * 10**6)
                    tx = self.usdt.functions.transfer(target_addr, base).build_transaction({
                        "from": current_sender,
                        "nonce": local_nonce,
                        "gas": 100_000,
                        "gasPrice": self.w3.eth.gas_price,
                        "chainId": self.w3.eth.chain_id,
                    })
                    signed = self.w3.eth.account.sign_transaction(tx, private_key=sender_key)
                    tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
                    receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                    local_nonce += 1
                    if receipt.status != 1:
                        return ToolResult(error=(
                            f"peel chain hop {hop_index} USDT transfer reverted "
                            f"(tx {tx_hash.hex()})"
                        ))

            hops.append(cont_addr)
            peels.append({"address": peel_addr, "amount": round(peel_amount, 6)})
            current_sender = cont_addr
            current_amount = cont_amount

        total_peeled = sum(p["amount"] for p in peels)
        # Track campaign-level peel locking (ETH only — the budget is
        # denominated in ETH; USDT peels aren't currently budgeted).
        if asset == "ETH":
            self._peel_locked_eth += total_peeled

        return ToolResult(output={
            "asset": asset,
            "initial_amount": initial_amount,
            "num_hops": num_hops,
            "peel_pct_target": peel_pct,
            "peel_jitter": peel_jitter,
            "peel_pcts_actual": [round(p, 4) for p in peel_pcts],
            "tail_wallet": current_sender,
            "tail_amount": round(current_amount, 6),
            "hop_wallets": hops,
            "peel_wallets": peels,
            "total_peeled": round(total_peeled, 6),
            "campaign_peel_locked_eth": (
                round(self._peel_locked_eth, 6) if asset == "ETH" else None
            ),
            "campaign_peel_budget_eth": self._peel_budget_eth,
        })

    def _advance_blocks(self, num_blocks: int) -> ToolResult:
        """Advance the chain with a randomised timing delay.

        On Anvil the per-block interval is drawn from a weighted APT
        distribution (60% quick hop 12s-5min per block, 25% cooling-off
        5min-1h, 15% deep dormancy 1h-12h). Chain-time reflects real
        launderer OPSEC patterns; wall-clock stays instant.

        On live chains (Sepolia etc.) the delay is a random wall-clock
        sleep in [10s, 360s] regardless of num_blocks — bounded so a
        campaign never blocks on any single call.
        """
        # Chain-id lookup may fail if the dispatcher was built with a
        # provider-less Web3() (test fixtures); default to strict Anvil
        # semantics so validation errors surface as clear ToolResult
        # errors instead of raw web3 exceptions.
        try:
            chain_id = int(self.w3.eth.chain_id)
        except Exception:  # noqa: BLE001
            chain_id = 31337
        is_anvil = chain_id == 31337
        max_blocks = 1_000_000 if is_anvil else 30
        min_blocks = 100 if is_anvil else 5
        if num_blocks < min_blocks or num_blocks > max_blocks:
            return ToolResult(error=(
                f"num_blocks must be in [{min_blocks}, {max_blocks}] "
                f"on this chain (chain_id={chain_id}), got {num_blocks}"
            ))
        block_before = self.w3.eth.block_number
        try:
            if is_anvil:
                # Weighted bucket over per-block interval (seconds).
                # Mirrors observed APT laundering cadence.
                bucket = random.choices(
                    population=[(12, 300), (300, 3600), (3600, 43200)],
                    weights=[0.60, 0.25, 0.15],
                    k=1,
                )[0]
                interval_s = random.randint(bucket[0], bucket[1])
                self.w3.provider.make_request(
                    "anvil_mine",
                    [hex(num_blocks), hex(interval_s)],
                )
                wall_clock_s = 0
            else:
                # Live chain: block progression is set by the network,
                # not by us. Only choose a bounded random wall-clock
                # jitter to break deterministic timing signatures.
                # Rango reducido 2026-08-24 (post seed 504): era
                # [10, 360] pero producía campañas Sepolia de 2-3h con
                # muchos advance_blocks encadenados (seed 504 tardó
                # 104 min). 10-60s da variabilidad suficiente para
                # evasión de timing correlation sin extender la
                # wall-clock más allá de lo razonable.
                import time as _time
                interval_s = 12  # real ethereum
                wall_clock_s = random.uniform(10, 60)
                _time.sleep(wall_clock_s)
        except Exception as e:   # noqa: BLE001
            return ToolResult(error=f"advance_blocks failed: {e}")
        block_after = self.w3.eth.block_number
        chain_seconds = num_blocks * interval_s if is_anvil else (block_after - block_before) * 12
        return ToolResult(output={
            "block_before": block_before,
            "block_after": block_after,
            "blocks_advanced": block_after - block_before,
            "interval_seconds_per_block": interval_s,
            "chain_seconds_elapsed": chain_seconds,
            "chain_days_elapsed": round(chain_seconds / 86400, 2),
            "wall_clock_seconds": round(wall_clock_s, 1),
        })

    def _mint_usdt(self, to_address: str, amount_usdt: float) -> ToolResult:
        if self.usdt is None:
            return ToolResult(error="USDT contract not set on dispatcher")
        if not self.wallets:
            return ToolResult(error="No registered wallet available to pay mint gas")

        try:
            to_address = Web3.to_checksum_address(to_address)
        except ValueError as e:
            return ToolResult(error=f"Invalid address: {e}")

        if amount_usdt <= 0:
            return ToolResult(error=f"Amount must be positive, got {amount_usdt}")

        gas_payer = next(iter(self.wallets))
        gas_key = self.wallets[gas_payer]
        amount_base = int(amount_usdt * 10**6)

        try:
            tx = self.usdt.functions.mint(to_address, amount_base).build_transaction({
                "from": gas_payer,
                "nonce": self.w3.eth.get_transaction_count(gas_payer, "pending"),
                "gas": 200_000,
                "gasPrice": self.w3.eth.gas_price,
            })
            signed = self.w3.eth.account.sign_transaction(tx, private_key=gas_key)
            tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        except Exception as e:
            return ToolResult(error=f"Mint raised: {e}")

        if receipt.status != 1:
            return ToolResult(error=f"Mint reverted on-chain (tx_hash={tx_hash.hex()})")

        new_balance = self.usdt.functions.balanceOf(to_address).call() / 10**6
        return ToolResult(output={
            "tx_hash": tx_hash.hex(),
            "to_address": to_address,
            "amount_usdt": amount_usdt,
            "new_balance_usdt": new_balance,
            "gas_used": receipt.gasUsed,
        })

    @staticmethod
    def _random_split_base_units(
        total_base: int, max_per_base: int, count: int, seed: int | None,
    ) -> list[int]:
        """Generate `count` integer amounts in [0, max_per_base] summing to `total_base`.

        Algorithm: iterate the wallets, sampling each amount uniformly within
        a feasibility window that guarantees the remaining wallets can still
        absorb the remaining total without exceeding max_per_base. The last
        wallet gets the residual (which is provably <= max_per_base by the
        capacity check at the top).
        """
        if max_per_base * count < total_base:
            raise ValueError(
                f"Capacity {max_per_base * count} base units < total {total_base}"
            )
        rng = random.Random(seed)
        amounts: list[int] = []
        remaining = total_base
        for i in range(count - 1):
            wallets_left = count - i - 1
            min_keep = max(0, remaining - wallets_left * max_per_base)
            max_take = min(max_per_base, remaining)
            amount = rng.randint(min_keep, max_take)
            amounts.append(amount)
            remaining -= amount
        amounts.append(remaining)
        # Sanity (cheap) — capacity check above guarantees these
        assert amounts[-1] <= max_per_base, "BUG: residual exceeds max_per_wallet"
        assert sum(amounts) == total_base, "BUG: amounts do not sum to total"
        return amounts

    def _smurf_split(
        self,
        from_address: str,
        total_usdt: float,
        num_wallets: int,
        max_per_wallet: float,
        seed: int | None = None,
    ) -> ToolResult:
        if self.usdt is None:
            return ToolResult(error="USDT contract not set on dispatcher")

        # Input validation (return ToolResult, never raise)
        try:
            from_address = Web3.to_checksum_address(from_address)
        except ValueError as e:
            return ToolResult(error=f"Invalid sender: {e}")
        if from_address not in self.wallets:
            return ToolResult(error=f"No private key registered for sender {from_address}")
        if num_wallets <= 0:
            return ToolResult(error=f"num_wallets must be positive, got {num_wallets}")
        if num_wallets > _MAX_BURNERS_PER_SMURF:
            return ToolResult(error=(
                f"num_wallets={num_wallets} exceeds cap of "
                f"{_MAX_BURNERS_PER_SMURF} (set to prevent runaway gas/runtime)"
            ))
        if total_usdt <= 0:
            return ToolResult(error=f"total_usdt must be positive, got {total_usdt}")
        if max_per_wallet <= 0:
            return ToolResult(error=f"max_per_wallet must be positive, got {max_per_wallet}")

        # Convert to integer base units (6 decimals) for exact arithmetic
        total_base = int(total_usdt * 10**6)
        max_per_base = int(max_per_wallet * 10**6)

        if max_per_base * num_wallets < total_base:
            return ToolResult(error=(
                f"Capacity exceeded: {num_wallets} wallets × {max_per_wallet} "
                f"USDT max = {num_wallets * max_per_wallet} USDT < "
                f"{total_usdt} USDT requested"
            ))

        sender_balance_base = self.usdt.functions.balanceOf(from_address).call()
        if sender_balance_base < total_base:
            return ToolResult(error=(
                f"Insufficient USDT: {from_address} holds "
                f"{sender_balance_base / 10**6} USDT, needs {total_usdt}"
            ))

        # Plan: random integer amounts summing to total_base
        try:
            amounts_base = self._random_split_base_units(
                total_base, max_per_base, num_wallets, seed,
            )
        except ValueError as e:
            return ToolResult(error=str(e))

        # Generate all burner wallets up-front (cheap, off-chain)
        burner_addresses: list[str] = []
        for _ in range(num_wallets):
            acct = Account.create()
            self.wallets[acct.address] = acct.key.hex()
            burner_addresses.append(acct.address)

        # Seed each burner with the default gas dust so the burners can
        # immediately be used as senders downstream. Failures here are
        # surfaced in the summary; we don't abort the whole smurf because
        # one seed tx hiccupped — partial seeding is better than nothing.
        seed_failures = 0
        for burner in burner_addresses:
            try:
                self._seed_gas(burner, _DEFAULT_GAS_RESERVE_ETH,
                               source=self._pick_funder())
            except Exception:   # noqa: BLE001 — count and continue
                seed_failures += 1

        # Execute transfers sequentially (Anvil mines on demand; ~5ms per tx)
        sender_key = self.wallets[from_address]
        nonce = self.w3.eth.get_transaction_count(from_address, "pending")
        gas_price = self.w3.eth.gas_price

        total_gas_used = 0
        successful = 0
        failures: list[dict] = []
        for i, (burner, amount_base) in enumerate(zip(burner_addresses, amounts_base)):
            if amount_base == 0:
                # Skip zero-amount transfers — no state change, no gas wasted
                successful += 1   # count as success: nothing was supposed to happen
                continue
            try:
                tx = self.usdt.functions.transfer(burner, amount_base).build_transaction({
                    "from": from_address,
                    "nonce": nonce,
                    "gas": 100_000,
                    "gasPrice": gas_price,
                })
                signed = self.w3.eth.account.sign_transaction(tx, private_key=sender_key)
                tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                if receipt.status == 1:
                    total_gas_used += receipt.gasUsed
                    successful += 1
                else:
                    failures.append({
                        "index": i, "burner": burner,
                        "amount_usdt": amount_base / 10**6, "error": "reverted",
                    })
                nonce += 1
            except Exception as e:   # noqa: BLE001 — surface to LLM as a tool error
                failures.append({
                    "index": i, "burner": burner,
                    "amount_usdt": amount_base / 10**6, "error": str(e),
                })

        # Bounded summary — never return all N entries (would blow up context)
        sample = [
            {"address": addr, "amount_usdt": amt / 10**6}
            for addr, amt in list(zip(burner_addresses, amounts_base))[:5]
        ]
        output: dict[str, Any] = {
            "wallets_created": num_wallets,
            "successful_transfers": successful,
            "failed_transfers": len(failures),
            "total_distributed_usdt": sum(amounts_base) / 10**6,
            "total_gas_used": total_gas_used,
            "total_gas_eth": total_gas_used * gas_price / 10**18,
            "from_address_remaining_usdt": (
                self.usdt.functions.balanceOf(from_address).call() / 10**6
            ),
            "burner_gas_seed_eth": _DEFAULT_GAS_RESERVE_ETH,
            "burner_gas_seed_failures": seed_failures,
            "sample_recipients": sample,
        }
        if failures:
            output["first_failures"] = failures[:3]
        return ToolResult(output=output)

    # --- ETH-side tools (PR 6.2.1: gas-aware multi-hop) ---------------------

    def _transfer_eth(
        self,
        from_address: str,
        to_address: str,
        amount_eth: float,
        reserve_eth: float = _DEFAULT_GAS_RESERVE_ETH,
    ) -> ToolResult:
        """Move ETH wallet→wallet, respecting a protected gas-reserve floor.

        Required for any ETH-denominated multi-hop laundering. Also the
        natural Integration consolidation primitive (consolidate ETH that
        came out of the mixer without round-tripping through USDT).
        """
        try:
            from_address = Web3.to_checksum_address(from_address)
            to_address = Web3.to_checksum_address(to_address)
        except ValueError as e:
            return ToolResult(error=f"Invalid address: {e}")
        if from_address not in self.wallets:
            return ToolResult(error=f"No private key registered for sender {from_address}")
        if amount_eth <= 0:
            return ToolResult(error=f"amount_eth must be positive, got {amount_eth}")
        if reserve_eth < 0:
            return ToolResult(error=f"reserve_eth cannot be negative, got {reserve_eth}")

        gas_price = self.w3.eth.gas_price
        gas_cost_wei = _ETH_TRANSFER_GAS * gas_price
        wei_amount = int(amount_eth * 10**18)
        reserve_wei = int(reserve_eth * 10**18)
        eth_balance_wei = self.w3.eth.get_balance(from_address)

        if eth_balance_wei - wei_amount - gas_cost_wei < reserve_wei:
            return ToolResult(error=(
                f"Transfer would breach gas reserve: wallet holds "
                f"{eth_balance_wei / 10**18:.6f} ETH, transfer needs "
                f"{amount_eth} + {gas_cost_wei / 10**18:.6f} gas, "
                f"reserve_eth={reserve_eth}. Pass reserve_eth=0 to drain at "
                "end-of-campaign."
            ))

        try:
            tx = {
                "from": from_address,
                "to": to_address,
                "value": wei_amount,
                "nonce": self.w3.eth.get_transaction_count(from_address, "pending"),
                "gas": _ETH_TRANSFER_GAS,
                "gasPrice": gas_price,
                "chainId": self.w3.eth.chain_id,
            }
            signed = self.w3.eth.account.sign_transaction(
                tx, private_key=self.wallets[from_address],
            )
            tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        except Exception as e:   # noqa: BLE001 — surface to LLM
            return ToolResult(error=f"Transfer raised: {e}")

        if receipt.status != 1:
            return ToolResult(error=f"Transfer reverted (tx_hash={tx_hash.hex()})")

        return ToolResult(output={
            "tx_hash": tx_hash.hex(),
            "from_address": from_address,
            "to_address": to_address,
            "amount_eth": amount_eth,
            "gas_used": receipt.gasUsed,
            "sender_remaining_eth": (
                self.w3.eth.get_balance(from_address) / 10**18
            ),
        })

    def _smurf_eth_split(
        self,
        from_address: str,
        total_eth: float,
        num_wallets: int,
        max_per_wallet_usdt: float = 999.0,
        seed: int | None = None,
    ) -> ToolResult:
        """ETH-denominated structuring with USD-equivalent per-wallet cap.

        Converts `max_per_wallet_usdt` to an ETH cap using the Uniswap
        pool's current spot price (so the cap and downstream swap rates
        are self-consistent). Generates `num_wallets` fresh burners, seeds
        each with gas dust, distributes `total_eth` across them in random
        amounts strictly under the cap. Returns a bounded summary.
        """
        if self.pool is None:
            return ToolResult(error=(
                "smurf_eth_split needs the Uniswap pool to convert the USD "
                "cap to ETH (no pool set on dispatcher)"
            ))
        try:
            from_address = Web3.to_checksum_address(from_address)
        except ValueError as e:
            return ToolResult(error=f"Invalid sender: {e}")
        if from_address not in self.wallets:
            return ToolResult(error=f"No private key registered for sender {from_address}")
        if num_wallets <= 0:
            return ToolResult(error=f"num_wallets must be positive, got {num_wallets}")
        if num_wallets > _MAX_BURNERS_PER_SMURF:
            return ToolResult(error=(
                f"num_wallets={num_wallets} exceeds cap of "
                f"{_MAX_BURNERS_PER_SMURF}"
            ))
        if total_eth <= 0:
            return ToolResult(error=f"total_eth must be positive, got {total_eth}")
        if max_per_wallet_usdt <= 0:
            return ToolResult(error=(
                f"max_per_wallet_usdt must be positive, got {max_per_wallet_usdt}"
            ))

        # Spot price from pool reserves: USDT per ETH.
        try:
            eth_reserve, usdt_reserve = self.pool.functions.getReserves().call()
        except Exception as e:
            return ToolResult(error=f"Failed to read pool reserves: {e}")
        if eth_reserve == 0 or usdt_reserve == 0:
            return ToolResult(error="Pool is empty (not bootstrapped)")
        spot_usdt_per_eth = (usdt_reserve / 10**6) / (eth_reserve / 10**18)

        # USD cap → ETH cap, then convert to wei integers for exact splitting.
        max_per_wallet_eth = max_per_wallet_usdt / spot_usdt_per_eth
        total_wei = int(total_eth * 10**18)
        max_per_wei = int(max_per_wallet_eth * 10**18)

        if max_per_wei * num_wallets < total_wei:
            return ToolResult(error=(
                f"Capacity exceeded: {num_wallets} wallets × "
                f"{max_per_wallet_eth:.6f} ETH cap = "
                f"{num_wallets * max_per_wallet_eth:.6f} ETH < "
                f"{total_eth} ETH requested. Either raise num_wallets, "
                "raise max_per_wallet_usdt, or lower total_eth."
            ))

        # Sender must have total_eth + headroom for num_wallets transfers.
        gas_price = self.w3.eth.gas_price
        gas_cost_wei = num_wallets * _ETH_TRANSFER_GAS * gas_price
        sender_balance_wei = self.w3.eth.get_balance(from_address)
        if sender_balance_wei < total_wei + gas_cost_wei:
            return ToolResult(error=(
                f"Insufficient ETH: {from_address} holds "
                f"{sender_balance_wei / 10**18:.6f} ETH, needs "
                f"{total_eth} + {gas_cost_wei / 10**18:.6f} gas"
            ))

        # Plan random amounts in [0, max_per_wei] summing to total_wei.
        try:
            amounts_wei = self._random_split_base_units(
                total_wei, max_per_wei, num_wallets, seed,
            )
        except ValueError as e:
            return ToolResult(error=str(e))

        # Generate burners and seed each with gas dust from the faucet
        # (separate from the laundered amount being sent from_address).
        burner_addresses: list[str] = []
        for _ in range(num_wallets):
            acct = Account.create()
            self.wallets[acct.address] = acct.key.hex()
            burner_addresses.append(acct.address)
        seed_failures = 0
        for burner in burner_addresses:
            try:
                self._seed_gas(burner, _DEFAULT_GAS_RESERVE_ETH,
                               source=self._pick_funder())
            except Exception:   # noqa: BLE001 — count and continue
                seed_failures += 1

        # Execute the laundering transfers from `from_address`.
        sender_key = self.wallets[from_address]
        nonce = self.w3.eth.get_transaction_count(from_address, "pending")
        total_gas_used = 0
        successful = 0
        failures: list[dict] = []
        for i, (burner, amount_wei) in enumerate(zip(burner_addresses, amounts_wei)):
            if amount_wei == 0:
                successful += 1   # skip-zero counts as success
                continue
            try:
                tx = {
                    "from": from_address,
                    "to": burner,
                    "value": amount_wei,
                    "nonce": nonce,
                    "gas": _ETH_TRANSFER_GAS,
                    "gasPrice": gas_price,
                    "chainId": self.w3.eth.chain_id,
                }
                signed = self.w3.eth.account.sign_transaction(tx, private_key=sender_key)
                tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                if receipt.status == 1:
                    total_gas_used += receipt.gasUsed
                    successful += 1
                else:
                    failures.append({
                        "index": i, "burner": burner,
                        "amount_eth": amount_wei / 10**18, "error": "reverted",
                    })
                nonce += 1
            except Exception as e:   # noqa: BLE001
                failures.append({
                    "index": i, "burner": burner,
                    "amount_eth": amount_wei / 10**18, "error": str(e),
                })

        sample = [
            {"address": addr, "amount_eth": amt / 10**18}
            for addr, amt in list(zip(burner_addresses, amounts_wei))[:5]
        ]
        output: dict[str, Any] = {
            "wallets_created": num_wallets,
            "successful_transfers": successful,
            "failed_transfers": len(failures),
            "total_distributed_eth": sum(amounts_wei) / 10**18,
            "max_per_wallet_usdt": max_per_wallet_usdt,
            "max_per_wallet_eth": max_per_wallet_eth,
            "spot_price_usdt_per_eth": spot_usdt_per_eth,
            "total_gas_used": total_gas_used,
            "total_gas_eth": total_gas_used * gas_price / 10**18,
            "burner_gas_seed_eth": _DEFAULT_GAS_RESERVE_ETH,
            "burner_gas_seed_failures": seed_failures,
            "from_address_remaining_eth": (
                self.w3.eth.get_balance(from_address) / 10**18
            ),
            "sample_recipients": sample,
        }
        if failures:
            output["first_failures"] = failures[:3]
        return ToolResult(output=output)

    def _get_gas_budget(
        self, address: str, reserve_eth: float = _DEFAULT_GAS_RESERVE_ETH,
    ) -> ToolResult:
        """Read-only: what's spendable above the gas-reserve floor."""
        try:
            address = Web3.to_checksum_address(address)
        except ValueError as e:
            return ToolResult(error=f"Invalid address: {e}")
        if reserve_eth < 0:
            return ToolResult(error=f"reserve_eth cannot be negative, got {reserve_eth}")

        eth_balance_wei = self.w3.eth.get_balance(address)
        eth_balance = eth_balance_wei / 10**18
        spendable = max(0.0, eth_balance - reserve_eth)
        gas_price = self.w3.eth.gas_price
        # Heuristic per-tx cost: a USDT transfer ~100k gas — middle of
        # the realistic tx-cost range for this campaign.
        per_tx_wei = 100_000 * gas_price
        per_tx_eth = per_tx_wei / 10**18
        txs_remaining = int(spendable * 10**18 / per_tx_wei) if per_tx_wei > 0 else 0
        return ToolResult(output={
            "address": address,
            "eth_balance": eth_balance,
            "reserve_eth": reserve_eth,
            "spendable_eth": spendable,
            "gas_price_gwei": gas_price / 10**9,
            "est_cost_per_tx_eth": per_tx_eth,
            "est_txs_remaining": txs_remaining,
        })

    # --- Swap tools (PR 5.5) ------------------------------------------------

    def _get_swap_quote(self, from_asset: str, amount: float) -> ToolResult:
        if self.pool is None:
            return ToolResult(error="Uniswap pool contract not set on dispatcher")
        if amount <= 0:
            return ToolResult(error=f"amount must be positive, got {amount}")

        try:
            eth_reserve, usdt_reserve = self.pool.functions.getReserves().call()
        except Exception as e:
            return ToolResult(error=f"Failed to read pool reserves: {e}")

        if eth_reserve == 0 or usdt_reserve == 0:
            return ToolResult(error="Pool is empty (not bootstrapped)")

        # Spot price: USDT per ETH, no fee, no slippage
        spot_usdt_per_eth = (usdt_reserve / 10**6) / (eth_reserve / 10**18)

        if from_asset == "ETH":
            amount_in = int(amount * 10**18)
            try:
                out_base = self.pool.functions.getAmountOut(
                    amount_in, eth_reserve, usdt_reserve,
                ).call()
            except Exception as e:
                return ToolResult(error=f"Quote failed: {e}")
            out_usdt = out_base / 10**6
            # Effective rate after fee + slippage
            effective_rate = out_usdt / amount if amount > 0 else 0
            slippage_pct = (1 - effective_rate / spot_usdt_per_eth) * 100
            return ToolResult(output={
                "from_asset": "ETH",
                "amount_in": amount,
                "expected_out_usdt": out_usdt,
                "spot_price_usdt_per_eth": spot_usdt_per_eth,
                "effective_price_usdt_per_eth": effective_rate,
                "total_cost_pct": slippage_pct,   # includes 0.3% fee + price impact
            })

        if from_asset == "USDT":
            amount_in = int(amount * 10**6)
            try:
                out_base = self.pool.functions.getAmountOut(
                    amount_in, usdt_reserve, eth_reserve,
                ).call()
            except Exception as e:
                return ToolResult(error=f"Quote failed: {e}")
            out_eth = out_base / 10**18
            spot_eth_per_usdt = 1 / spot_usdt_per_eth
            effective_rate = out_eth / amount if amount > 0 else 0
            slippage_pct = (1 - effective_rate / spot_eth_per_usdt) * 100
            return ToolResult(output={
                "from_asset": "USDT",
                "amount_in": amount,
                "expected_out_eth": out_eth,
                "spot_price_usdt_per_eth": spot_usdt_per_eth,
                "effective_price_eth_per_usdt": effective_rate,
                "total_cost_pct": slippage_pct,
            })

        return ToolResult(error=f"Unsupported from_asset: {from_asset!r}")

    def _swap_eth_for_usdt(
        self,
        from_address: str,
        eth_amount: float,
        min_usdt_out: float = 0,
        reserve_eth: float = _DEFAULT_GAS_RESERVE_ETH,
    ) -> ToolResult:
        if self.pool is None:
            return ToolResult(error="Uniswap pool contract not set on dispatcher")
        try:
            from_address = Web3.to_checksum_address(from_address)
        except ValueError as e:
            return ToolResult(error=f"Invalid sender: {e}")
        if from_address not in self.wallets:
            return ToolResult(error=f"No private key registered for {from_address}")
        if eth_amount <= 0:
            return ToolResult(error=f"eth_amount must be positive, got {eth_amount}")
        if min_usdt_out < 0:
            return ToolResult(error=f"min_usdt_out cannot be negative, got {min_usdt_out}")
        if reserve_eth < 0:
            return ToolResult(error=f"reserve_eth cannot be negative, got {reserve_eth}")

        # Gas-reserve guard: refuse if eth_amount + gas would drop the
        # sender below reserve. The check is conservative — we budget a
        # generous gas headroom so the wallet has slack for variability.
        gas_price = self.w3.eth.gas_price
        gas_headroom_wei = _GAS_HEADROOM_TX * gas_price
        eth_balance_wei = self.w3.eth.get_balance(from_address)
        wei_in = int(eth_amount * 10**18)
        reserve_wei = int(reserve_eth * 10**18)
        if eth_balance_wei - wei_in - gas_headroom_wei < reserve_wei:
            return ToolResult(error=(
                f"Swap would breach gas reserve: wallet holds "
                f"{eth_balance_wei / 10**18:.6f} ETH, swap needs "
                f"{eth_amount} + ~{gas_headroom_wei / 10**18:.4f} gas, "
                f"reserve_eth={reserve_eth}. Pass reserve_eth=0 to drain."
            ))

        min_out_base = int(min_usdt_out * 10**6)

        usdt_before = self.usdt.functions.balanceOf(from_address).call()

        try:
            tx = self.pool.functions.swapETHForUSDT(min_out_base).build_transaction({
                "from": from_address,
                "nonce": self.w3.eth.get_transaction_count(from_address, "pending"),
                "gas": 200_000,
                "gasPrice": gas_price,
                "value": wei_in,
            })
            signed = self.w3.eth.account.sign_transaction(
                tx, private_key=self.wallets[from_address],
            )
            tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        except Exception as e:
            return ToolResult(error=f"Swap raised: {e}")

        if receipt.status != 1:
            return ToolResult(
                error=f"Swap reverted on-chain (tx_hash={tx_hash.hex()}) — "
                      f"likely slippage protection (min_usdt_out too high)"
            )

        usdt_after = self.usdt.functions.balanceOf(from_address).call()
        usdt_received = (usdt_after - usdt_before) / 10**6
        return ToolResult(output={
            "tx_hash": tx_hash.hex(),
            "from_address": from_address,
            "eth_paid": eth_amount,
            "usdt_received": usdt_received,
            "gas_used": receipt.gasUsed,
        })

    def _swap_usdt_for_eth(
        self,
        from_address: str,
        usdt_amount: float,
        min_eth_out: float = 0,
        reserve_eth: float = _DEFAULT_GAS_RESERVE_ETH,
    ) -> ToolResult:
        if self.pool is None:
            return ToolResult(error="Uniswap pool contract not set on dispatcher")
        try:
            from_address = Web3.to_checksum_address(from_address)
        except ValueError as e:
            return ToolResult(error=f"Invalid sender: {e}")
        if from_address not in self.wallets:
            return ToolResult(error=f"No private key registered for {from_address}")
        if usdt_amount <= 0:
            return ToolResult(error=f"usdt_amount must be positive, got {usdt_amount}")
        if min_eth_out < 0:
            return ToolResult(error=f"min_eth_out cannot be negative, got {min_eth_out}")
        if reserve_eth < 0:
            return ToolResult(error=f"reserve_eth cannot be negative, got {reserve_eth}")

        amount_base = int(usdt_amount * 10**6)
        min_out_wei = int(min_eth_out * 10**18)
        sender_key = self.wallets[from_address]
        gas_price = self.w3.eth.gas_price

        # Gas-reserve guard: even though the swap NETS ETH, the approve+swap
        # txs must be paid for first — the wallet needs reserve_eth + 2×gas
        # of ETH on hand to submit them. The receive bumps balance after.
        eth_balance_wei = self.w3.eth.get_balance(from_address)
        gas_headroom_wei = 2 * _GAS_HEADROOM_TX * gas_price
        reserve_wei = int(reserve_eth * 10**18)
        if eth_balance_wei - gas_headroom_wei < reserve_wei:
            return ToolResult(error=(
                f"Cannot pay swap gas without breaching reserve: wallet "
                f"holds {eth_balance_wei / 10**18:.6f} ETH, approve+swap "
                f"need ~{gas_headroom_wei / 10**18:.4f} ETH gas, "
                f"reserve_eth={reserve_eth}. Seed more ETH first."
            ))

        eth_before = eth_balance_wei

        # Two transactions: approve, then swap. Build sequential nonces.
        try:
            nonce = self.w3.eth.get_transaction_count(from_address, "pending")
            # 1. approve
            approve_tx = self.usdt.functions.approve(self.pool.address, amount_base).build_transaction({
                "from": from_address,
                "nonce": nonce,
                "gas": 100_000,
                "gasPrice": gas_price,
            })
            approve_signed = self.w3.eth.account.sign_transaction(
                approve_tx, private_key=sender_key,
            )
            approve_hash = self.w3.eth.send_raw_transaction(_raw_tx(approve_signed))
            approve_receipt = self.w3.eth.wait_for_transaction_receipt(approve_hash)
            if approve_receipt.status != 1:
                return ToolResult(
                    error=f"Approve reverted (tx_hash={approve_hash.hex()})"
                )

            # 2. swap
            swap_tx = self.pool.functions.swapUSDTForETH(amount_base, min_out_wei).build_transaction({
                "from": from_address,
                "nonce": nonce + 1,
                "gas": 250_000,
                "gasPrice": gas_price,
            })
            swap_signed = self.w3.eth.account.sign_transaction(
                swap_tx, private_key=sender_key,
            )
            swap_hash = self.w3.eth.send_raw_transaction(_raw_tx(swap_signed))
            swap_receipt = self.w3.eth.wait_for_transaction_receipt(swap_hash)
        except Exception as e:
            return ToolResult(error=f"Swap raised: {e}")

        if swap_receipt.status != 1:
            return ToolResult(
                error=f"Swap reverted on-chain (tx_hash={swap_hash.hex()}) — "
                      f"likely slippage protection (min_eth_out too high)"
            )

        eth_after = self.w3.eth.get_balance(from_address)
        # eth_after - eth_before = eth_received - gas_paid
        # We can pull eth_received from on-chain by computing gas separately
        total_gas_wei = (approve_receipt.gasUsed + swap_receipt.gasUsed) * gas_price
        eth_received_wei = (eth_after - eth_before) + total_gas_wei
        return ToolResult(output={
            "approve_tx_hash": approve_hash.hex(),
            "swap_tx_hash": swap_hash.hex(),
            "from_address": from_address,
            "usdt_paid": usdt_amount,
            "eth_received": eth_received_wei / 10**18,
            "gas_used": approve_receipt.gasUsed + swap_receipt.gasUsed,
        })

    # --- ZK mixer tools (PR 5.6) --------------------------------------------

    def _resolve_mixer_pool(self, denomination_eth: float = 1.0):
        """Look up the pool contract for a requested denomination.

        Returns (pool_contract, denomination_wei) or (None, error_msg).
        """
        if not self.tornado_pools:
            return None, "Tornado mixer contract(s) not set on dispatcher"
        denom_wei = int(round(float(denomination_eth) * 10**18))
        pool = self.tornado_pools.get(denom_wei)
        if pool is None:
            available = sorted(
                d / 10**18 for d in self.tornado_pools.keys()
            )
            return None, (
                f"Denomination {denomination_eth} ETH has no deployed pool. "
                f"Available pools: {available} ETH. Pick the largest that "
                f"fits your working amount, or split across multiple deposits."
            )
        return pool, denom_wei

    def _mixer_deposit(
        self, from_address: str, denomination_eth: float = 1.0,
    ) -> ToolResult:
        """Deposit DENOMINATION ETH into a ZK Tornado mixer pool.

        `denomination_eth` selects which pool (Tornado-style multi-
        denomination). Default 1 ETH for backward compatibility with
        callers that predate the multi-pool refactor. Available pools
        depend on deployment — see dispatcher.tornado_pools keys.
        """
        pool, denom_wei_or_err = self._resolve_mixer_pool(denomination_eth)
        if pool is None:
            return ToolResult(error=denom_wei_or_err)
        denom_wei = denom_wei_or_err

        try:
            from_address = Web3.to_checksum_address(from_address)
        except ValueError as e:
            return ToolResult(error=f"Invalid sender: {e}")
        if from_address not in self.wallets:
            return ToolResult(error=f"No private key registered for {from_address}")

        balance = self.w3.eth.get_balance(from_address)
        if balance < denom_wei:
            held = balance / 10**18
            denom_eth = denom_wei / 10**18
            available = sorted(d / 10**18 for d in self.tornado_pools.keys())
            smaller = [d for d in available if d <= held - 0.005]
            smaller_hint = (
                f"USE the {max(smaller):g} ETH pool instead "
                f"(denomination_eth={max(smaller):g})"
                if smaller else "no smaller pool fits either"
            )
            return ToolResult(error=(
                f"Insufficient ETH: {from_address} holds {held:.4f} ETH — "
                f"the {denom_eth:g} ETH pool needs exactly {denom_eth:g} ETH "
                f"plus ~0.005 ETH gas. Alternatives with {held:.4f} ETH: "
                f"(a) {smaller_hint}; (b) peel_chain(asset='ETH', "
                f"initial_amount={max(0.0, held-0.005):.4f}, num_hops=15-25, "
                f"peel_pct=0.05) — used in ~70% of real crypto heists per "
                f"TRM Labs; (c) smurf_eth_split across 5-15 fresh burners; "
                f"(d) swap_eth_for_usdt then smurf_split the USDT. "
                f"Do NOT top-up this wallet to reach {denom_eth:g} ETH — "
                f"that creates a co-funding signature the detector catches."
            ))

        # Fresh deposit note: random (nullifier, secret) in the bn254 field.
        # 31 random bytes fits comfortably under the 254-bit field prime.
        nullifier = int.from_bytes(secrets.token_bytes(31), "big")
        secret = int.from_bytes(secrets.token_bytes(31), "big")

        try:
            commitment_int = int(_run_zk_helper("mimc2", str(nullifier), str(secret)))
        except RuntimeError as e:
            return ToolResult(error=f"Commitment hashing failed: {e}")
        commitment_bytes = commitment_int.to_bytes(32, "big")

        try:
            tx = pool.functions.deposit(commitment_bytes).build_transaction({
                "from": from_address,
                "nonce": self.w3.eth.get_transaction_count(from_address, "pending"),
                "gas": 3_000_000,   # MiMC insert re-hashes the full tree path
                "gasPrice": self.w3.eth.gas_price,
                "value": denom_wei,
            })
            signed = self.w3.eth.account.sign_transaction(
                tx, private_key=self.wallets[from_address],
            )
            tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        except Exception as e:
            return ToolResult(error=f"Deposit raised: {e}")

        if receipt.status != 1:
            return ToolResult(
                error=f"Deposit reverted on-chain (tx_hash={tx_hash.hex()})"
            )

        # Leaf index is a nice-to-have (handy for the agent's bookkeeping);
        # if event parsing fails for any reason, ship the result without it.
        leaf_index = None
        try:
            events = pool.events.Deposit().process_receipt(
                receipt, errors=DISCARD,
            )
            if events:
                leaf_index = events[0]["args"]["leafIndex"]
        except Exception:   # noqa: BLE001 — leaf_index is optional
            pass

        note = _encode_note(nullifier, secret)
        # Safety-net persistence (if enabled by runner). Written IMMEDIATELY
        # after receipt so a subsequent LLM eviction / crash / halt cannot
        # lose the note. Each line is a self-contained JSON record that a
        # separate `scripts/mixer_recover.py` can read to redeem locked ETH.
        if self.notes_file is not None:
            try:
                import json as _json
                import time as _time
                from pathlib import Path as _Path
                _p = _Path(self.notes_file)
                _p.parent.mkdir(parents=True, exist_ok=True)
                with _p.open("a") as _f:
                    _f.write(_json.dumps({
                        "ts": _time.time(),
                        "tx_hash": tx_hash.hex(),
                        "from_address": from_address,
                        "leaf_index": leaf_index,
                        "commitment": "0x" + commitment_bytes.hex(),
                        "note": note,
                    }) + "\n")
            except Exception:   # noqa: BLE001 — never let persistence break a deposit
                pass

        return ToolResult(output={
            "tx_hash": tx_hash.hex(),
            "from_address": from_address,
            "deposit_note": note,
            "commitment": "0x" + commitment_bytes.hex(),
            "leaf_index": leaf_index,
            "amount_eth": denom_wei / 10**18,
            "denomination_eth": denom_wei / 10**18,
            "pool_address": pool.address,
            "gas_used": receipt.gasUsed,
            "warning": (
                f"SAVE the deposit_note — it is the only way to withdraw, "
                f"and anyone holding it can withdraw {denom_wei / 10**18} "
                f"ETH from pool {pool.address} to any recipient. The note "
                f"is denomination-bound: pass denomination_eth="
                f"{denom_wei / 10**18} to mixer_withdraw when redeeming."
            ),
        })

    def _mixer_collect_leaves(
        self, target_commitment: int, pool=None,
    ) -> tuple[list[int], int | None]:
        """Scan the mixer's Deposit events; rebuild the ordered leaf set.

        Returns (leaves ordered by on-chain index, index of target_commitment
        within that list). target_index is None if the commitment isn't
        deposited. Raises RuntimeError on unrecoverable scan failure.

        Robustness strategy (v3, post 2026-08-20 test disaster):
        1. Pin BOTH the leaf count AND the log scan to the SAME block on
           the SAME provider (self._logs_w3). This eliminates the
           multi-provider race where Alchemy's nextIndex() and publicnode's
           eth_getLogs disagreed because they were at different block
           heights.
        2. Progressive retry: wide chunks first (9000), then narrow (500),
           then very narrow (100) if events are still missing. Silent
           page-truncation by RPC providers under load is the known
           failure mode.
        3. Between retries, sleep briefly (exponential backoff) so the RPC
           node can catch up if it's lagging behind the chain head.
        """
        # Which pool to scan? Default to the primary (self.tornado); the
        # caller can pass a specific pool for multi-denom lookups.
        target_pool = pool if pool is not None else self.tornado
        # Same provider for both nextIndex and eth_getLogs so their
        # snapshots are consistent by construction.
        tornado_logs = self._logs_w3.eth.contract(
            address=target_pool.address, abi=target_pool.abi,
        )
        deposit_event = tornado_logs.events.Deposit()

        # Pin to a specific block number: whatever `_logs_w3` reports as
        # its head right now. We'll query nextIndex() AT this block and
        # only scan events up to it — guarantees atomic snapshot.
        try:
            pinned_block = int(self._logs_w3.eth.block_number)
        except Exception as e:   # noqa: BLE001
            raise RuntimeError(f"Failed to read logs provider head: {e}") from e

        try:
            expected_next_index = int(
                tornado_logs.functions.nextIndex().call(
                    block_identifier=pinned_block,
                )
            )
        except Exception as e:   # noqa: BLE001
            raise RuntimeError(
                f"Failed to read mixer nextIndex() @ block {pinned_block}: {e}"
            ) from e
        if expected_next_index == 0:
            return [], None

        def _fetch(from_block: int, to_block: int) -> list:
            try:
                try:
                    return deposit_event.get_logs(
                        from_block=from_block, to_block=to_block,
                    )
                except TypeError:   # web3.py v6 uses fromBlock/toBlock
                    return deposit_event.get_logs(
                        fromBlock=from_block, toBlock=to_block,
                    )
            except Exception as e:   # noqa: BLE001
                raise RuntimeError(
                    f"failed to scan mixer deposit events "
                    f"(range {from_block}-{to_block}): {e}"
                ) from e

        def _scan(from_block: int, to_block: int, chunk: int,
                  sink: dict[int, int]) -> None:
            cursor = from_block
            while cursor <= to_block:
                stop = min(cursor + chunk - 1, to_block)
                for log in _fetch(cursor, stop):
                    idx = log["args"]["leafIndex"]
                    sink[idx] = int.from_bytes(
                        bytes(log["args"]["commitment"]), "big",
                    )
                cursor = stop + 1

        # Progressive retry: 9000 → 500 → 100 block chunks. Each attempt
        # scans the full range fresh (no cursor state to corrupt), then
        # merges with previous attempts. If still missing after all three,
        # the RPC is fundamentally dropping events for this contract.
        import time as _t
        by_index: dict[int, int] = {}
        for attempt, chunk in enumerate([9000, 500, 100]):
            retry: dict[int, int] = {}
            try:
                _scan(self.mixer_events_from_block, pinned_block, chunk, retry)
            except RuntimeError:
                # One chunk failed. Continue with what we have; the merge
                # below preserves any partial progress from prior attempts.
                if attempt == 2:
                    raise
                _t.sleep(2 ** attempt)   # 1s, 2s backoff
                continue
            # Merge retry ∪ by_index (retry wins for keys in retry)
            merged = dict(by_index)
            merged.update(retry)
            by_index = merged
            missing = [i for i in range(expected_next_index) if i not in by_index]
            if not missing:
                break   # ✓ all leaves accounted for
            _t.sleep(2 ** attempt)

        # Final sanity check: every leaf up to expected_next_index must be
        # present. If not, fall back to Etherscan API (no retention window
        # or rate-limits on log queries — 1000 logs/page, free tier).
        # Root cause (2026-08-24): publicnode has a rolling data-retention
        # window (~13k blocks); Alchemy free tier caps eth_getLogs at 10
        # blocks; 1RPC at 50. Old leaves (deposit block < retention window)
        # are completely absent from eth_getLogs on public providers.
        still_missing = [
            i for i in range(expected_next_index) if i not in by_index
        ]
        if still_missing:
            _etherscan_key = os.environ.get("ETHERSCAN_API_KEY")
            if _etherscan_key:
                try:
                    import requests as _rq
                    DEPOSIT_TOPIC = ("0x9cf4fd51b1f9ca63da33b474c6efe62e"
                                     "39eb8978697ba78fd64750558dfd29a3")
                    chain_id = int(self._logs_w3.eth.chain_id)
                    url = "https://api.etherscan.io/v2/api"
                    r = _rq.get(url, timeout=30, params={
                        "chainid": chain_id, "module": "logs",
                        "action": "getLogs",
                        "address": self.tornado.address,
                        "fromBlock": self.mixer_events_from_block,
                        "toBlock": pinned_block,
                        "topic0": DEPOSIT_TOPIC,
                        "apikey": _etherscan_key,
                        "page": 1, "offset": 1000,
                    })
                    body = r.json() if r.status_code == 200 else {}
                    for lg in body.get("result", []):
                        try:
                            leaf_idx = int(lg["data"][2:66], 16)
                            commit_hex = lg["topics"][1]
                            by_index[leaf_idx] = int(commit_hex, 16)
                        except (ValueError, KeyError, IndexError):
                            continue
                except Exception:   # noqa: BLE001
                    pass   # fall through to the RuntimeError below
                still_missing = [
                    i for i in range(expected_next_index)
                    if i not in by_index
                ]
        if still_missing:
            preview = still_missing[:5] + (["..."] if len(still_missing) > 5 else [])
            raise RuntimeError(
                f"eth_getLogs + Etherscan fallback both failed to find all "
                f"leaves: contract has {expected_next_index} @ block "
                f"{pinned_block} but only fetched {len(by_index)}. "
                f"Missing indices: {preview}. Set ETHERSCAN_API_KEY if not "
                f"set, or use a paid RPC (Alchemy PAYG / Infura / QuickNode)."
            )

        leaves = [by_index[i] for i in range(expected_next_index)]
        target_index = next(
            (i for i, c in enumerate(leaves) if c == target_commitment), None,
        )
        return leaves, target_index

    def _mixer_withdraw(
        self,
        deposit_note: str,
        recipient: str,
        gas_payer: str | None = None,
        denomination_eth: float = 1.0,
    ) -> ToolResult:
        """Withdraw DENOMINATION ETH from a mixer pool via Groth16 proof.

        `denomination_eth` MUST match the pool the note was deposited to
        (mixer_deposit's output includes `denomination_eth` — pass the
        same value back). Default 1 ETH for back-compat with legacy notes.
        """
        pool, denom_wei_or_err = self._resolve_mixer_pool(denomination_eth)
        if pool is None:
            return ToolResult(error=denom_wei_or_err)

        try:
            nullifier, secret = _decode_note(deposit_note)
        except ValueError as e:
            return ToolResult(error=str(e))

        try:
            recipient = Web3.to_checksum_address(recipient)
        except ValueError as e:
            return ToolResult(error=f"Invalid recipient: {e}")

        # Who submits (and pays gas for) the withdraw tx. For unlinkability
        # this should be unrelated to the depositor and recipient — the agent
        # can pass gas_payer explicitly; otherwise the first registered wallet
        # pays.
        if gas_payer is not None:
            try:
                gas_payer = Web3.to_checksum_address(gas_payer)
            except ValueError as e:
                return ToolResult(error=f"Invalid gas_payer: {e}")
            if gas_payer not in self.wallets:
                return ToolResult(
                    error=f"No private key registered for gas_payer {gas_payer}"
                )
        else:
            if not self.wallets:
                return ToolResult(
                    error="No registered wallet available to pay withdraw gas"
                )
            gas_payer = next(iter(self.wallets))

        # Reconstruct commitment + nullifierHash from the note.
        try:
            commitment_int = int(_run_zk_helper("mimc2", str(nullifier), str(secret)))
            nullifier_hash_int = int(_run_zk_helper("mimc", str(nullifier)))
        except RuntimeError as e:
            return ToolResult(error=f"Note hashing failed: {e}")
        nullifier_hash_bytes = nullifier_hash_int.to_bytes(32, "big")

        # Already spent?
        try:
            if pool.functions.nullifierHashes(nullifier_hash_bytes).call():
                return ToolResult(
                    error="This note has already been withdrawn (nullifier spent)"
                )
        except Exception as e:
            return ToolResult(error=f"Failed to read mixer nullifier state: {e}")

        # Rebuild the Merkle tree from on-chain Deposit events. Retry up
        # to 3 times if the reconstructed root isn't in the on-chain
        # history — the failure mode is a pagination/RPC race where some
        # Deposit events are missed by the initial scan, producing an
        # off-by-N leaf set whose root the contract doesn't recognise.
        # Re-scanning with a slight delay picks up the missing events.
        # Root cause of the 2026-08-18 Sepolia seed 500 withdraw failure
        # that locked 9 ETH in the mixer (see chapter 5 §5.6.5).
        import time as _t
        leaves = None
        leaf_index = None
        prepared = None
        root_bytes = None
        last_error = None
        for attempt in range(3):
            try:
                leaves, leaf_index = self._mixer_collect_leaves(
                    commitment_int, pool=pool,
                )
            except RuntimeError as e:
                last_error = str(e)
                _t.sleep(2 ** attempt)  # 1s, 2s, 4s backoff
                continue
            if leaf_index is None:
                # Deposit event not yet visible to the RPC — wait and rescan
                # (the deposit tx may have confirmed 1-2 blocks ago and
                # eth_getLogs on some providers lags briefly).
                last_error = (
                    "Commitment not found in the mixer — this note was never "
                    "deposited (or was deposited to a different mixer instance)"
                )
                _t.sleep(2 ** attempt)
                continue
            # Build Merkle path for candidate root
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    leaves_file = tmp_path / "leaves.json"
                    leaves_file.write_text(json.dumps([str(x) for x in leaves]))
                    prepared = json.loads(_run_zk_helper(
                        "merkle-path", str(_MERKLE_DEPTH), str(leaf_index),
                        str(leaves_file),
                    ))
                root_int = int(prepared["root"])
                root_bytes = root_int.to_bytes(32, "big")
            except RuntimeError as e:
                last_error = f"Merkle path computation failed: {e}"
                _t.sleep(2 ** attempt)
                continue
            # Verify root is known on-chain. Fast path: our locally-computed
            # root equals the current on-chain root (getLastRoot) → tree is
            # fully in sync, no concurrent deposits landed since our scan.
            # Slow path: our root is not the current one, but the contract
            # keeps a ring buffer of the last ROOT_HISTORY_SIZE roots
            # (isKnownRoot), so an older root from before a concurrent
            # deposit still verifies.
            try:
                last_root_on_chain = pool.functions.getLastRoot().call()
                if root_bytes == last_root_on_chain:
                    break   # ✓ fast path — perfect match with contract head
                if pool.functions.isKnownRoot(root_bytes).call():
                    break   # ✓ slow path — root within the last ROOT_HISTORY_SIZE
                last_error = (
                    f"Reconstructed Merkle root not known on-chain "
                    f"(attempt {attempt + 1}/3): local root from "
                    f"{len(leaves)} leaves does not match any of the last "
                    f"ROOT_HISTORY_SIZE contract roots. This means either "
                    f"(a) eth_getLogs is still missing events despite the "
                    f"targeted rescan (raise the issue with the RPC "
                    f"provider) or (b) more than ROOT_HISTORY_SIZE (=30) "
                    f"concurrent deposits landed between our snapshot and "
                    f"the withdraw attempt, pushing our root off the ring "
                    f"buffer (rare on Sepolia; retry immediately)."
                )
                _t.sleep(2 ** attempt)
                continue
            except Exception as e:
                last_error = f"Failed to verify Merkle root: {e}"
                _t.sleep(2 ** attempt)
                continue
        else:
            # All 3 attempts exhausted without a recognised root
            return ToolResult(error=(
                f"Off-chain leaf set out of sync with the contract after 3 "
                f"retries. Last error: {last_error}. This is the Sepolia RPC "
                f"race condition documented in chapter 5 §5.6.5; if the "
                f"deposit note is preserved (e.g. via ToolDispatcher.notes_file), "
                f"a manual recovery via scripts/mixer_recover.py can retry "
                f"the withdrawal later when the tree is quiescent."
            ))

        # `prepared` and `root_bytes` are populated by the successful
        # iteration of the retry loop above. Now build the Groth16 proof.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            circuit_input = {
                "root": prepared["root"],
                "nullifierHash": str(nullifier_hash_int),
                "recipient": str(int(recipient, 16)),
                "fee": "0",
                "refund": "0",
                "nullifier": str(nullifier),
                "secret": str(secret),
                "pathElements": prepared["pathElements"],
                "pathIndices": prepared["pathIndices"],
            }
            input_file = tmp_path / "input.json"
            witness_file = tmp_path / "witness.wtns"
            proof_file = tmp_path / "proof.json"
            public_file = tmp_path / "public.json"
            input_file.write_text(json.dumps(circuit_input))

            try:
                _run_snarkjs(
                    "wtns", "calculate",
                    str(_ZK_WASM), str(input_file), str(witness_file),
                )
                _run_snarkjs(
                    "groth16", "prove",
                    str(_ZK_ZKEY), str(witness_file),
                    str(proof_file), str(public_file),
                )
            except RuntimeError as e:
                return ToolResult(error=f"Groth16 proof generation failed: {e}")

            proof = json.loads(proof_file.read_text())

        pa, pb, pc = _proof_to_solidity(proof)

        # Submit the withdraw tx.
        try:
            tx = pool.functions.withdraw(
                pa, pb, pc, root_bytes, nullifier_hash_bytes, recipient, 0, 0,
            ).build_transaction({
                "from": gas_payer,
                "nonce": self.w3.eth.get_transaction_count(gas_payer, "pending"),
                "gas": 2_000_000,
                "gasPrice": self.w3.eth.gas_price,
            })
            signed = self.w3.eth.account.sign_transaction(
                tx, private_key=self.wallets[gas_payer],
            )
            tx_hash = self.w3.eth.send_raw_transaction(_raw_tx(signed))
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        except Exception as e:
            return ToolResult(error=f"Withdraw raised: {e}")

        if receipt.status != 1:
            return ToolResult(
                error=f"Withdraw reverted on-chain (tx_hash={tx_hash.hex()})"
            )

        return ToolResult(output={
            "tx_hash": tx_hash.hex(),
            "recipient": recipient,
            "amount_eth": _MIXER_DENOMINATION_WEI / 10**18,
            "gas_payer": gas_payer,
            "nullifier_hash": "0x" + nullifier_hash_bytes.hex(),
            "anonymity_set_size": len(leaves),
            "gas_used": receipt.gasUsed,
        })

    # --- Batched ZK mixer tools (batched pattern to save context tokens) ---
    # Same tradeoff as smurf_split: the Coordinator picks the parameters, the
    # dispatcher does the N sequential ops. Avoids N round-trips of tool_use
    # / tool_result blocks (each ~2KB of context growth) that would burn
    # tokens and hit max_iterations on any batch >20.

    def _mixer_batch_deposit(
        self, from_address: str, num_deposits: int,
    ) -> ToolResult:
        """Batch deposit: N × 1 ETH into the mixer, return N notes."""
        if self.tornado is None:
            return ToolResult(error="Tornado mixer contract not set on dispatcher")

        try:
            from_address = Web3.to_checksum_address(from_address)
        except ValueError as e:
            return ToolResult(error=f"Invalid sender: {e}")
        if from_address not in self.wallets:
            return ToolResult(error=f"No private key registered for {from_address}")
        if num_deposits <= 0:
            return ToolResult(
                error=f"num_deposits must be positive, got {num_deposits}"
            )
        if num_deposits > _MAX_MIXER_BATCH:
            return ToolResult(error=(
                f"num_deposits={num_deposits} exceeds cap of "
                f"{_MAX_MIXER_BATCH} (set to prevent runaway gas/runtime)"
            ))

        # Balance pre-check: N × 1 ETH plus generous gas margin. Each deposit
        # uses ~3M gas (MiMC hashes the full Merkle path); with a headroom
        # factor of 1.2× we don't strand the sender mid-batch.
        balance = self.w3.eth.get_balance(from_address)
        gas_price = self.w3.eth.gas_price
        required_deposit_wei = num_deposits * _MIXER_DENOMINATION_WEI
        estimated_gas_wei = int(num_deposits * 3_000_000 * gas_price * 1.2)
        if balance < required_deposit_wei + estimated_gas_wei:
            return ToolResult(error=(
                f"Insufficient ETH: {from_address} holds "
                f"{balance / 10**18:.4f} ETH, batch of {num_deposits} "
                f"deposits requires {required_deposit_wei / 10**18} ETH "
                f"plus ~{estimated_gas_wei / 10**18:.4f} ETH gas"
            ))

        notes: list[str] = []
        tx_hashes: list[str] = []
        leaf_indices: list[int | None] = []
        commitments: list[str] = []
        total_gas = 0
        failures: list[dict] = []

        # Reuse the single-deposit implementation for consistency. Stop on
        # first failure — the mixer state is deterministic per-tx, so a
        # failure typically means insufficient balance or contract issue
        # that won't self-heal for the next tx in the batch.
        for i in range(num_deposits):
            result = self._mixer_deposit(from_address)
            if result.error:
                failures.append({"index": i, "error": result.error})
                break
            out = result.output
            notes.append(out["deposit_note"])
            tx_hashes.append(out["tx_hash"])
            leaf_indices.append(out.get("leaf_index"))
            commitments.append(out.get("commitment", ""))
            total_gas += out.get("gas_used", 0)

        return ToolResult(output={
            "from_address": from_address,
            "num_requested": num_deposits,
            "num_successful": len(notes),
            # ALL notes returned — agent NEEDS every one to withdraw. This
            # is the payload the batch exists to produce.
            "deposit_notes": notes,
            "leaf_indices": leaf_indices,
            # Sample tx_hashes + commitments to avoid context blowup on
            # large batches — full receipts are on-chain if the agent needs
            # them later (via inspect_chain).
            "tx_hashes_sample": (
                tx_hashes[:5] + (["..."] if len(tx_hashes) > 5 else [])
            ),
            "commitments_sample": (
                commitments[:5] + (["..."] if len(commitments) > 5 else [])
            ),
            "total_gas_used": total_gas,
            "total_eth_deposited": (
                len(notes) * _MIXER_DENOMINATION_WEI / 10**18
            ),
            "failures": failures,
            "warning": (
                "SAVE ALL deposit_notes — each is the ONLY way to withdraw "
                "its corresponding 1 ETH. Anyone holding a note controls "
                "its ETH."
            ),
        })

    def _mixer_batch_withdraw(
        self,
        deposit_notes: list[str],
        recipients: list[str] | str,
        gas_payer: str | None = None,
    ) -> ToolResult:
        """Batch withdraw: N notes from the mixer, per-note success/failure."""
        if self.tornado is None:
            return ToolResult(error="Tornado mixer contract not set on dispatcher")
        if not isinstance(deposit_notes, list) or not deposit_notes:
            return ToolResult(
                error="deposit_notes must be a non-empty list"
            )
        if len(deposit_notes) > _MAX_MIXER_BATCH:
            return ToolResult(error=(
                f"len(deposit_notes)={len(deposit_notes)} exceeds cap of "
                f"{_MAX_MIXER_BATCH}"
            ))

        # Normalize recipients: str → broadcast to all notes; list → 1:1.
        if isinstance(recipients, str):
            recipients_list = [recipients] * len(deposit_notes)
        elif isinstance(recipients, list):
            if len(recipients) != len(deposit_notes):
                return ToolResult(error=(
                    f"len(recipients)={len(recipients)} must equal "
                    f"len(deposit_notes)={len(deposit_notes)}, or pass a "
                    "single string to broadcast one recipient across notes"
                ))
            recipients_list = recipients
        else:
            return ToolResult(
                error="recipients must be str or list[str]"
            )

        successes: list[dict] = []
        failures: list[dict] = []
        total_gas = 0

        # Reuse the single-withdraw path per note. Each call re-scans mixer
        # events + generates a fresh Groth16 proof (~10-30s per note); this
        # is the dominant cost and cannot be trivially batched because
        # proofs are per-nullifier. Continue on failure — a single bad
        # note (already-spent, malformed) shouldn't kill the whole batch.
        for i, (note, recipient) in enumerate(
            zip(deposit_notes, recipients_list)
        ):
            result = self._mixer_withdraw(note, recipient, gas_payer)
            if result.error:
                failures.append({
                    "index": i,
                    "recipient": recipient,
                    "error": result.error,
                })
                continue
            out = result.output
            successes.append({
                "index": i,
                "tx_hash": out["tx_hash"],
                "recipient": out["recipient"],
            })
            total_gas += out.get("gas_used", 0)

        return ToolResult(output={
            "num_requested": len(deposit_notes),
            "num_successful": len(successes),
            "num_failed": len(failures),
            # Sample successes to bound context; full tx_hashes recoverable
            # on-chain if needed.
            "successes_sample": (
                successes[:5] + (["..."] if len(successes) > 5 else [])
            ),
            # Failures kept in full — they're the actionable information
            # the agent needs to decide what to do next (retry, skip, etc.)
            "failures": failures,
            "total_gas_used": total_gas,
            "total_eth_withdrawn": (
                len(successes) * _MIXER_DENOMINATION_WEI / 10**18
            ),
            "anonymity_note": (
                "Each withdrawal used a fresh Groth16 proof — on-chain "
                "observers cannot link individual withdrawals to specific "
                "deposits beyond the mixer's anonymity set."
            ),
        })

    # --- Coordinator reflection tool (PR 42: smarter attacker, A) -----------

    def _inspect_chain(
        self, since_block: int = 0, max_wallets_sample: int = 8,
    ) -> ToolResult:
        """Read-only chain audit. See schema for what's returned.

        Coordinator-level reflection tool. The sub-agents return SELF-
        REPORTS via finish_task; this tool gives the Coordinator
        ground-truth chain state to verify those reports against.

        Defensive on every external call (events vary across web3.py
        versions; some contracts may not be deployed in a given
        campaign). Always returns a ToolResult — never raises.
        """
        import statistics

        try:
            current_block = self.w3.eth.block_number
        except Exception as e:   # noqa: BLE001
            return ToolResult(error=f"Failed to read block number: {e}")

        wallet_addrs = list(self.wallets.keys())
        eth_balances: list[float] = []
        usdt_balances: list[float] = []
        for addr in wallet_addrs:
            try:
                eth_balances.append(self.w3.eth.get_balance(addr) / 10**18)
            except Exception:   # noqa: BLE001
                eth_balances.append(0.0)
            if self.usdt is not None:
                try:
                    usdt_balances.append(
                        self.usdt.functions.balanceOf(addr).call() / 10**6
                    )
                except Exception:   # noqa: BLE001
                    usdt_balances.append(0.0)
            else:
                usdt_balances.append(0.0)

        # Sample top-N wallets by ETH (most "interesting" for triage).
        order = sorted(range(len(wallet_addrs)), key=lambda i: -eth_balances[i])
        sample_size = min(max(max_wallets_sample, 0), len(wallet_addrs))
        wallets_sample = [
            {
                "address": wallet_addrs[i],
                "eth": round(eth_balances[i], 4),
                "usdt": round(usdt_balances[i], 2),
            }
            for i in order[:sample_size]
        ]

        # Event counts since `since_block`.
        event_counts = {
            "mixer_deposits": 0,
            "mixer_withdrawals": 0,
            "swaps": 0,
            "usdt_transfers": 0,
        }

        def _get_logs_compat(event_factory, from_block):
            """web3.py v6 used fromBlock=, v7 uses from_block=."""
            try:
                return list(event_factory().get_logs(from_block=from_block))
            except TypeError:
                return list(event_factory().get_logs(fromBlock=from_block))

        if self.tornado is not None:
            try:
                event_counts["mixer_deposits"] = len(
                    _get_logs_compat(self.tornado.events.Deposit, since_block),
                )
            except Exception:   # noqa: BLE001
                pass
            # Withdrawal event name varies across implementations.
            for attr in ("Withdrawal", "Withdraw"):
                if hasattr(self.tornado.events, attr):
                    try:
                        event_counts["mixer_withdrawals"] = len(
                            _get_logs_compat(
                                getattr(self.tornado.events, attr),
                                since_block,
                            ),
                        )
                    except Exception:   # noqa: BLE001
                        pass
                    break
        if self.usdt is not None:
            try:
                event_counts["usdt_transfers"] = len(
                    _get_logs_compat(self.usdt.events.Transfer, since_block),
                )
            except Exception:   # noqa: BLE001
                pass
        if self.pool is not None:
            for attr in ("Swap", "Swapped"):
                if hasattr(self.pool.events, attr):
                    try:
                        event_counts["swaps"] = len(
                            _get_logs_compat(
                                getattr(self.pool.events, attr),
                                since_block,
                            ),
                        )
                    except Exception:   # noqa: BLE001
                        pass
                    break

        # Detection-relevant signals computed over wallets that are
        # active (ETH > the 0.05 reserve floor by a comfortable margin).
        active_eth = [b for b in eth_balances if b > 0.06]
        signals: dict[str, Any] = {
            "num_registered_wallets": len(wallet_addrs),
            "num_active_wallets": len(active_eth),
            "mixer_used": event_counts["mixer_deposits"] > 0,
        }
        if len(active_eth) >= 3:
            mean_eth = statistics.fmean(active_eth)
            stdev_eth = statistics.stdev(active_eth)
            cv = stdev_eth / mean_eth if mean_eth > 0 else 0.0
            signals["burner_eth_coefficient_of_variation"] = round(cv, 4)
            if cv < 0.1:
                signals["uniformity_warning"] = (
                    "Active wallet ETH balances are highly uniform "
                    "(coefficient of variation < 0.1). This is the kind of "
                    "feature signature a GCN-style detector trivially "
                    "learns. Consider varying transaction sizes and routes."
                )

        return ToolResult(output={
            "block_number": current_block,
            "since_block": since_block,
            "num_registered_wallets": len(wallet_addrs),
            "total_eth_in_wallets": round(sum(eth_balances), 4),
            "total_usdt_in_wallets": round(sum(usdt_balances), 2),
            "wallets_sample": wallets_sample,
            "contracts": {
                "usdt": self.usdt.address if self.usdt is not None else None,
                "pool": self.pool.address if self.pool is not None else None,
                "tornado": self.tornado.address if self.tornado is not None else None,
            },
            "events_since_block": event_counts,
            "detection_signals": signals,
        })
