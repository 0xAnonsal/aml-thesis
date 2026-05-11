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

Future tools (separate PRs):
    swap_eth_for_usdt, swap_usdt_for_eth   — Uniswap-mock interactions
    mixer_deposit, mixer_withdraw          — ZK Tornado primitives
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any

from eth_account import Account
from web3 import Web3


# Hard cap on smurf_split's wallet count to prevent runaway gas / runtime
# from a misbehaving agent. 5000 burners ≈ 100 seconds on Anvil + ~0.25 ETH
# in gas — both fine; anything 10× that risks operator pain.
_MAX_BURNERS_PER_SMURF = 5000


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
]


def _raw_tx(signed) -> bytes:
    """web3.py v6 (rawTransaction) vs v7 (raw_transaction) attr-name compat."""
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes attribute")
    return raw


class ToolDispatcher:
    """Maps tool names to Python implementations and holds chain context.

    Args:
        w3: Web3 client connected to Anvil.
        usdt_contract: Deployed MockUSDT contract handle (or None for tests
            that only exercise schema introspection).
        wallets: dict mapping address -> private_key for wallets the attacker
            controls. Addresses are normalized to checksum form on insertion.
    """

    def __init__(
        self,
        w3: Web3,
        usdt_contract: Any,
        wallets: dict[str, str],
    ):
        self.w3 = w3
        self.usdt = usdt_contract
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
