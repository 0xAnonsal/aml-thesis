"""Chain action tools for the AML attacker agents.

The agent layer (LLMClient + tool_use blocks) calls these via ToolDispatcher.
Each tool wraps an on-chain action — read or write — on the Anvil simulator.
The dispatcher owns the chain context (Web3 client, deployed contracts,
attacker-controlled wallet keys) so the agent never sees crypto plumbing —
it just calls `dispatch(name, input)` and gets a structured ToolResult back.

Tools shipped:
    get_balance              — read ETH or USDT balance for an address
    transfer_usdt            — write: single USDT transfer
    generate_burner_wallet   — fresh keypair, auto-registered in registry
    mint_usdt                — permissionless mint (research convenience)
    smurf_split              — large-scale structuring: distribute USDT
                               across N random burner wallets in one call
                               (avoids burning LLM tokens orchestrating
                               thousands of individual transfers)
    swap_eth_for_usdt        — Uniswap-style ETH -> USDT swap (caller pays
                               ETH + gas, receives USDT)
    swap_usdt_for_eth        — Uniswap-style USDT -> ETH swap (approve +
                               swap done atomically inside the dispatcher)
    get_swap_quote           — view-only quote: expected output for a given
                               input given the pool's current reserves
    mixer_deposit            — deposit 1 ETH into the ZK Tornado mixer;
                               returns a secret deposit note
    mixer_withdraw           — withdraw 1 ETH from the mixer to any
                               recipient using a deposit note + a Groth16
                               proof generated internally via snarkjs
"""
from __future__ import annotations

import json
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
            "future transactions on the wallet's behalf. Returns the new "
            "address. New burners start with 0 ETH and 0 USDT — fund them "
            "via transfer_usdt or mint_usdt before they can do anything."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
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
            "reverts if the actual output would be less). Defaults to 0 "
            "(accept any output)."
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
            "to enforce slippage protection."
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
        "name": "mixer_deposit",
        "description": (
            "Deposit exactly 1 ETH into the ZK mixer (Tornado-Cash-style). "
            "Generates a fresh secret deposit note, commits its hash on-chain, "
            "and sends 1 ETH from `from_address` (which must be in the wallet "
            "registry and hold at least 1 ETH plus gas). Returns a "
            "`deposit_note` string — this is the ONLY way to later withdraw "
            "the ETH, so it must be remembered and kept secret. The mixer "
            "breaks the on-chain link between the depositing wallet and "
            "whatever address later withdraws: a withdrawal cannot be tied to "
            "this deposit beyond the anonymity set of all deposits. The "
            "denomination is fixed at 1 ETH — to mix a different amount, make "
            "multiple deposits."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_address": {
                    "type": "string",
                    "description": (
                        "Wallet making the deposit. Must be in the registry "
                        "and hold >= 1 ETH plus gas."
                    ),
                },
            },
            "required": ["from_address"],
        },
    },
    {
        "name": "mixer_withdraw",
        "description": (
            "Withdraw 1 ETH from the ZK mixer using a `deposit_note` returned "
            "by an earlier mixer_deposit call. The 1 ETH is paid to "
            "`recipient` (any address — does NOT need to be in the registry, "
            "and a fresh unrelated address gives the best unlinkability). A "
            "Groth16 zero-knowledge proof is generated internally proving the "
            "note's commitment is in the mixer's Merkle tree, without "
            "revealing which deposit it was. Pass `gas_payer` to choose which "
            "registered wallet submits and pays gas for the withdrawal tx — "
            "for unlinkability this should be a wallet unrelated to both the "
            "depositor and the recipient; if omitted, the first registered "
            "wallet pays. Fails cleanly if the note is malformed, was never "
            "deposited, or was already withdrawn. Proof generation takes a "
            "few seconds."
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
                        "Address that receives the 1 ETH. Any address; need "
                        "not be in the registry."
                    ),
                },
                "gas_payer": {
                    "type": "string",
                    "description": (
                        "Optional. Registered wallet that submits the withdraw "
                        "tx and pays its gas. Defaults to the first registered "
                        "wallet. For unlinkability, use a wallet unrelated to "
                        "the deposit and the recipient."
                    ),
                },
            },
            "required": ["deposit_note", "recipient"],
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


def _run_snarkjs(*args: str) -> None:
    """Run `snarkjs <args...>`. Raises RuntimeError on missing binary or failure."""
    try:
        proc = subprocess.run(["snarkjs", *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(
            "`snarkjs` not found on PATH — ZK mixer tools need snarkjs"
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
    ):
        self.w3 = w3
        self.usdt = usdt_contract
        self.pool = pool_contract
        self.tornado = tornado_contract
        self.wallets: dict[str, str] = {
            Web3.to_checksum_address(addr): key for addr, key in wallets.items()
        }

    @property
    def tool_definitions(self) -> list[dict]:
        """Tool schemas in Anthropic's `tools=` parameter format."""
        return list(_TOOL_SCHEMAS)

    def register_wallet(self, address: str, private_key: str) -> None:
        """Add a wallet (e.g. a freshly-generated burner) to the registry."""
        self.wallets[Web3.to_checksum_address(address)] = private_key

    def dispatch(self, tool_name: str, tool_input: dict) -> ToolResult:
        """Execute the named tool with the given input dict."""
        if tool_name == "get_balance":
            return self._get_balance(**tool_input)
        if tool_name == "transfer_usdt":
            return self._transfer_usdt(**tool_input)
        if tool_name == "generate_burner_wallet":
            return self._generate_burner_wallet(**tool_input)
        if tool_name == "mint_usdt":
            return self._mint_usdt(**tool_input)
        if tool_name == "smurf_split":
            return self._smurf_split(**tool_input)
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

        try:
            tx = self.usdt.functions.transfer(to_address, amount_base).build_transaction({
                "from": from_address,
                "nonce": self.w3.eth.get_transaction_count(from_address),
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

        return ToolResult(output={
            "tx_hash": tx_hash.hex(),
            "from_address": from_address,
            "to_address": to_address,
            "amount_usdt": amount_usdt,
            "gas_used": receipt.gasUsed,
        })

    # --- New tools (PR 5.4) -------------------------------------------------

    def _generate_burner_wallet(self) -> ToolResult:
        """Create a fresh keypair, register in the wallets dict, return the address."""
        acct = Account.create()
        address = acct.address  # already checksummed by eth_account
        # acct.key is a HexBytes; .hex() produces the 0x-prefixed string
        self.wallets[address] = acct.key.hex()
        return ToolResult(output={"address": address})

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
                "nonce": self.w3.eth.get_transaction_count(gas_payer),
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

        # Execute transfers sequentially (Anvil mines on demand; ~5ms per tx)
        sender_key = self.wallets[from_address]
        nonce = self.w3.eth.get_transaction_count(from_address)
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
            "sample_recipients": sample,
        }
        if failures:
            output["first_failures"] = failures[:3]
        return ToolResult(output=output)

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

        wei_in = int(eth_amount * 10**18)
        min_out_base = int(min_usdt_out * 10**6)

        usdt_before = self.usdt.functions.balanceOf(from_address).call()

        try:
            tx = self.pool.functions.swapETHForUSDT(min_out_base).build_transaction({
                "from": from_address,
                "nonce": self.w3.eth.get_transaction_count(from_address),
                "gas": 200_000,
                "gasPrice": self.w3.eth.gas_price,
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

        amount_base = int(usdt_amount * 10**6)
        min_out_wei = int(min_eth_out * 10**18)
        sender_key = self.wallets[from_address]
        gas_price = self.w3.eth.gas_price

        eth_before = self.w3.eth.get_balance(from_address)

        # Two transactions: approve, then swap. Build sequential nonces.
        try:
            nonce = self.w3.eth.get_transaction_count(from_address)
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

    def _mixer_deposit(self, from_address: str) -> ToolResult:
        """Deposit 1 ETH into the ZK Tornado mixer; return a secret note."""
        if self.tornado is None:
            return ToolResult(error="Tornado mixer contract not set on dispatcher")
        try:
            from_address = Web3.to_checksum_address(from_address)
        except ValueError as e:
            return ToolResult(error=f"Invalid sender: {e}")
        if from_address not in self.wallets:
            return ToolResult(error=f"No private key registered for {from_address}")

        balance = self.w3.eth.get_balance(from_address)
        if balance < _MIXER_DENOMINATION_WEI:
            return ToolResult(error=(
                f"Insufficient ETH: {from_address} holds {balance / 10**18} ETH, "
                f"mixer deposit requires 1 ETH plus gas"
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
            tx = self.tornado.functions.deposit(commitment_bytes).build_transaction({
                "from": from_address,
                "nonce": self.w3.eth.get_transaction_count(from_address),
                "gas": 3_000_000,   # MiMC insert re-hashes the full tree path
                "gasPrice": self.w3.eth.gas_price,
                "value": _MIXER_DENOMINATION_WEI,
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
        # `deposit` emits both LeafInserted (from MerkleTreeWithHistory) and
        # Deposit; errors=DISCARD silently skips the non-matching LeafInserted
        # log instead of logging a MismatchedABI warning for it.
        leaf_index = None
        try:
            events = self.tornado.events.Deposit().process_receipt(
                receipt, errors=DISCARD,
            )
            if events:
                leaf_index = events[0]["args"]["leafIndex"]
        except Exception:   # noqa: BLE001 — leaf_index is optional
            pass

        return ToolResult(output={
            "tx_hash": tx_hash.hex(),
            "from_address": from_address,
            "deposit_note": _encode_note(nullifier, secret),
            "commitment": "0x" + commitment_bytes.hex(),
            "leaf_index": leaf_index,
            "amount_eth": _MIXER_DENOMINATION_WEI / 10**18,
            "gas_used": receipt.gasUsed,
            "warning": (
                "SAVE the deposit_note — it is the only way to withdraw, and "
                "anyone holding it can withdraw the 1 ETH to any recipient."
            ),
        })

    def _mixer_collect_leaves(
        self, target_commitment: int,
    ) -> tuple[list[int], int | None]:
        """Scan the mixer's Deposit events; rebuild the ordered leaf set.

        Returns (leaves ordered by on-chain index, index of target_commitment
        within that list). The index is None if the commitment was never
        deposited. Raises RuntimeError if the event scan itself fails.
        """
        deposit_event = self.tornado.events.Deposit()
        try:
            try:
                logs = deposit_event.get_logs(from_block=0)
            except TypeError:   # web3.py v6 uses fromBlock
                logs = deposit_event.get_logs(fromBlock=0)
        except Exception as e:   # noqa: BLE001 — surfaced as a tool error
            raise RuntimeError(f"failed to scan mixer deposit events: {e}") from e

        by_index: dict[int, int] = {}
        for log in logs:
            idx = log["args"]["leafIndex"]
            by_index[idx] = int.from_bytes(bytes(log["args"]["commitment"]), "big")

        if not by_index:
            return [], None

        leaves = [by_index.get(i, 0) for i in range(max(by_index) + 1)]
        target_index = next(
            (i for i, c in enumerate(leaves) if c == target_commitment), None,
        )
        return leaves, target_index

    def _mixer_withdraw(
        self,
        deposit_note: str,
        recipient: str,
        gas_payer: str | None = None,
    ) -> ToolResult:
        """Withdraw 1 ETH from the mixer to `recipient` via a Groth16 proof."""
        if self.tornado is None:
            return ToolResult(error="Tornado mixer contract not set on dispatcher")

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
            if self.tornado.functions.nullifierHashes(nullifier_hash_bytes).call():
                return ToolResult(
                    error="This note has already been withdrawn (nullifier spent)"
                )
        except Exception as e:
            return ToolResult(error=f"Failed to read mixer nullifier state: {e}")

        # Rebuild the Merkle tree from on-chain Deposit events.
        try:
            leaves, leaf_index = self._mixer_collect_leaves(commitment_int)
        except RuntimeError as e:
            return ToolResult(error=str(e))
        if leaf_index is None:
            return ToolResult(error=(
                "Commitment not found in the mixer — this note was never "
                "deposited (or was deposited to a different mixer instance)"
            ))

        # Generate the Groth16 proof in an isolated temp dir.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            try:
                leaves_file = tmp_path / "leaves.json"
                leaves_file.write_text(json.dumps([str(x) for x in leaves]))
                prepared = json.loads(_run_zk_helper(
                    "merkle-path", str(_MERKLE_DEPTH), str(leaf_index),
                    str(leaves_file),
                ))
            except RuntimeError as e:
                return ToolResult(error=f"Merkle path computation failed: {e}")

            root_int = int(prepared["root"])
            root_bytes = root_int.to_bytes(32, "big")

            # Defensive: the root we reconstructed must be one the contract
            # still has in its bounded history.
            try:
                if not self.tornado.functions.isKnownRoot(root_bytes).call():
                    return ToolResult(error=(
                        "Reconstructed Merkle root is not known on-chain — the "
                        "off-chain leaf set is out of sync with the contract"
                    ))
            except Exception as e:
                return ToolResult(error=f"Failed to verify Merkle root: {e}")

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
            tx = self.tornado.functions.withdraw(
                pa, pb, pc, root_bytes, nullifier_hash_bytes, recipient, 0, 0,
            ).build_transaction({
                "from": gas_payer,
                "nonce": self.w3.eth.get_transaction_count(gas_payer),
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
