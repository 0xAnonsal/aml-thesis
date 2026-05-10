"""Chain action tools for the AML attacker agents.

The agent layer (LLMClient + tool_use blocks) calls these via ToolDispatcher.
Each tool wraps an on-chain action — read or write — on the Anvil simulator.
The dispatcher owns the chain context (Web3 client, deployed contracts,
attacker-controlled wallet keys) so the agent never sees crypto plumbing —
it just calls `dispatch(name, input)` and gets a structured ToolResult back.

Two tools land in this PR (one read pattern, one write pattern):
    get_balance      — read ETH or USDT balance for an address
    transfer_usdt    — write: transfer USDT from one wallet to another

Future tools (separate PRs):
    swap_eth_for_usdt, swap_usdt_for_eth   — Uniswap-mock interactions
    mint_usdt                              — bootstrap funds for burners
    mixer_deposit, mixer_withdraw          — ZK Tornado primitives
    generate_burner_wallet                 — fresh keypair, auto-registered
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from web3 import Web3


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
            "6-decimal base units the contract uses internally."
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
