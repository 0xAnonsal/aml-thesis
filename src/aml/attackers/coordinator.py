"""Multi-agent orchestrator — the FATF Coordinator.

Week 6 replaces the single-loop agent with a two-level structure:

    Coordinator (this file)        — strategist; decomposes a laundering
                                     objective into FATF phases and delegates
    ├─ Placement   SubAgent        — FATF stage 1: position illicit funds
    ├─ Layering    SubAgent        — FATF stage 2: obfuscate the trail
    └─ Integration SubAgent        — FATF stage 3: consolidate "clean" funds

The Coordinator has exactly three tools — delegate_to_placement /
delegate_to_layering / delegate_to_integration — and NO chain tools. When it
calls one, the orchestrator spins up the matching tool-scoped SubAgent (see
sub_agent.py), runs it, and feeds the sub-agent's structured report back as
the tool_result. The Coordinator never sees a sub-agent's transcript — only
its {status, summary, key_facts}.

This is the "sub-agent-as-tool" design: delegation is a runtime decision the
Coordinator makes and reacts to, not a fixed pipeline. That runtime decision
loop — re-delegating when a phase comes back partial, carrying key_facts
forward into the next phase's context — is what makes "multi-agent" a real
claim rather than a relabelled script.

All sub-agents share the Coordinator's single ToolDispatcher, so chain state
(burners, balances, mixer deposits) flows between phases automatically; the
Coordinator's job is to pass the *knowledge* of that state forward via each
delegation's `context`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .llm_client import LLMClient
from .prompts import (
    COORDINATOR_SYSTEM,
    INTEGRATION_SYSTEM,
    LAYERING_SYSTEM,
    PLACEMENT_SYSTEM,
)
from .sub_agent import SubAgent, SubAgentResult
from .tools import ToolDispatcher


# FATF role → chain tools that role's SubAgent may use. Every sub-agent also
# gets finish_task automatically (added by SubAgent itself). Scopes deliberately
# overlap on the basic primitives (get_balance, get_gas_budget, transfer_*) —
# every phase legitimately needs them. The differentiating tools per role are
# the obfuscation/structuring ones: only Placement gets mint_usdt + the initial
# smurf_eth_split; only Layering gets the mixer; only Integration gets the
# off-ramp consolidation primitives (but no mint, no mixer — Integration's job
# is consolidating layered funds, not creating or further obfuscating them).
_PLACEMENT_TOOLS = [
    "get_balance", "get_gas_budget",
    "generate_burner_wallet",
    "mint_usdt", "transfer_usdt",
    "transfer_eth", "smurf_eth_split",
]
_LAYERING_TOOLS = [
    "get_balance", "get_gas_budget",
    "generate_burner_wallet",
    "transfer_eth", "transfer_usdt",
    "smurf_split", "smurf_eth_split",
    "get_swap_quote", "swap_eth_for_usdt", "swap_usdt_for_eth",
    "mixer_deposit", "mixer_withdraw",
]
_INTEGRATION_TOOLS = [
    "get_balance", "get_gas_budget",
    "transfer_eth", "transfer_usdt",
    "get_swap_quote", "swap_eth_for_usdt", "swap_usdt_for_eth",
]

_ROLE_TOOLS = {
    "placement": _PLACEMENT_TOOLS,
    "layering": _LAYERING_TOOLS,
    "integration": _INTEGRATION_TOOLS,
}
_ROLE_SYSTEM = {
    "placement": PLACEMENT_SYSTEM,
    "layering": LAYERING_SYSTEM,
    "integration": INTEGRATION_SYSTEM,
}
_ROLE_FROM_TOOL = {f"delegate_to_{role}": role for role in _ROLE_TOOLS}

_DELEGATE_DESCRIPTIONS = {
    "placement": (
        "Delegate a Placement sub-objective to the Placement specialist "
        "agent. Placement is FATF stage 1: getting illicit funds into the "
        "system and establishing the initial wallet positioning. The "
        "Placement agent can generate burner wallets, mint USDT, transfer "
        "USDT, and read balances."
    ),
    "layering": (
        "Delegate a Layering sub-objective to the Layering specialist agent. "
        "Layering is FATF stage 2 — the core obfuscation phase: breaking the "
        "link between source and destination. The Layering agent can "
        "structure funds across many burner wallets (smurf_split), convert "
        "between USDT and ETH (swaps), and run ETH through the ZK mixer "
        "(mixer_deposit / mixer_withdraw)."
    ),
    "integration": (
        "Delegate an Integration sub-objective to the Integration specialist "
        "agent. Integration is FATF stage 3: bringing the layered funds back "
        "together into apparently-legitimate consolidated holdings. The "
        "Integration agent can transfer USDT and convert between USDT and ETH."
    ),
}


def _delegate_schema(role: str) -> dict:
    """Anthropic-format schema for one delegate_to_<role> tool."""
    return {
        "name": f"delegate_to_{role}",
        "description": _DELEGATE_DESCRIPTIONS[role],
        "input_schema": {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": (
                        "What this sub-agent should accomplish, in plain "
                        "language. Be specific and bounded — one phase's "
                        "worth of work."
                    ),
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Every piece of state the sub-agent needs to do the "
                        "job: wallet addresses, amounts, mixer deposit notes, "
                        "results carried forward from earlier phases. The "
                        "sub-agent sees ONLY this string — not this "
                        "conversation, not other sub-agents' work — so it "
                        "must be self-contained."
                    ),
                },
            },
            "required": ["objective", "context"],
        },
    }


@dataclass
class CampaignResult:
    """The result of a multi-agent laundering campaign run."""
    final_text: str
    iterations: int
    # one entry per delegate call: {"role", "objective", "status"}
    delegations: list[dict] = field(default_factory=list)
    sub_agent_runs: list[SubAgentResult] = field(default_factory=list)
    cost_usd: float = 0.0   # Coordinator tokens + every sub-agent's cost
    messages: list[dict] = field(default_factory=list)  # Coordinator transcript
    stopped_reason: str = ""   # 'end_turn' | 'max_iterations' | 'max_tokens' | ...

    @property
    def successful(self) -> bool:
        return self.stopped_reason == "end_turn"

    @property
    def total_tool_calls(self) -> int:
        """Chain tool calls across all sub-agents (the Coordinator makes none)."""
        return sum(len(r.tool_calls) for r in self.sub_agent_runs)


class Coordinator:
    """Two-level FATF orchestrator: delegates phases to scoped sub-agents.

    Args:
        client: LLMClient. Shared with the sub-agents, so `client.usage`
            tracks the whole campaign's spend.
        dispatcher: ToolDispatcher with the campaign's chain context. Shared
            with every sub-agent — that's how chain state flows between phases.
        model: Coordinator model alias/ID. Default 'opus' — the strategist
            runs on the strongest model; pass 'haiku' for cheap dev iteration.
        sub_agent_model: model for the three FATF sub-agents. Default 'sonnet'.
        max_iterations: hard cap on the Coordinator's own delegate loop.
        sub_agent_max_iterations: hard cap passed to each SubAgent's loop.
    """

    def __init__(
        self,
        client: LLMClient,
        dispatcher: ToolDispatcher,
        *,
        model: str = "opus",
        sub_agent_model: str = "sonnet",
        max_iterations: int = 12,
        sub_agent_max_iterations: int = 15,
    ):
        self.client = client
        self.dispatcher = dispatcher
        self.model = model
        self.max_iterations = max_iterations

        # One tool-scoped SubAgent per FATF role, all sharing the dispatcher.
        self.sub_agents: dict[str, SubAgent] = {
            role: SubAgent(
                client, dispatcher, _ROLE_TOOLS[role],
                model=sub_agent_model, name=role,
                max_iterations=sub_agent_max_iterations,
            )
            for role in _ROLE_TOOLS
        }
        self._delegate_tools = [_delegate_schema(role) for role in _ROLE_TOOLS]

    @property
    def delegate_tool_definitions(self) -> list[dict]:
        """The Coordinator's tools — exactly the three delegate_to_* tools."""
        return list(self._delegate_tools)

    def run(
        self,
        user_prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        sub_agent_max_tokens: int = 4096,
    ) -> CampaignResult:
        """Run the orchestration loop until the Coordinator stops delegating.

        Returns when the Coordinator LLM responds with `end_turn` (campaign
        complete), another stop reason (refusal, max_tokens, ...), or the
        max_iterations cap is hit.
        """
        if system is None:
            system = COORDINATOR_SYSTEM

        messages: list[dict] = [{"role": "user", "content": user_prompt}]
        tools = self._delegate_tools
        delegations: list[dict] = []
        sub_agent_runs: list[SubAgentResult] = []
        cost = 0.0
        stopped = "max_iterations"
        final_text = ""
        iteration = 0

        for iteration in range(1, self.max_iterations + 1):
            response = self.client.complete(
                messages=messages,
                system=system,
                model=self.model,
                max_tokens=max_tokens,
                tools=tools,
            )
            cost += response.cost_usd
            messages.append({"role": "assistant", "content": response.raw.content})

            text_blocks = [b.text for b in response.raw.content if b.type == "text"]
            if text_blocks:
                final_text = "\n".join(text_blocks)

            stop = response.stop_reason
            if stop == "end_turn":
                stopped = "end_turn"
                break
            if stop != "tool_use":
                # refusal, max_tokens, pause_turn, etc. — record and exit.
                stopped = stop or "unknown"
                break

            tool_use_blocks = [b for b in response.raw.content if b.type == "tool_use"]
            tool_results = []
            for block in tool_use_blocks:
                tool_input = dict(block.input) if block.input else {}
                role = _ROLE_FROM_TOOL.get(block.name)

                if role is None:
                    content = f"Error: unknown tool {block.name!r}"
                    is_error = True
                else:
                    objective = tool_input.get("objective", "")
                    context = tool_input.get("context", "")
                    sub_result = self.sub_agents[role].run(
                        objective,
                        context=context,
                        system=_ROLE_SYSTEM[role],
                        max_tokens=sub_agent_max_tokens,
                    )
                    cost += sub_result.cost_usd
                    sub_agent_runs.append(sub_result)
                    delegations.append({
                        "role": role,
                        "objective": objective,
                        "status": sub_result.status,
                    })
                    content = json.dumps(sub_result.to_delegation_report())
                    # Flag failed / incomplete delegations as tool errors so
                    # the Coordinator notices and can react; the report's
                    # `status` field carries the same signal in the body.
                    is_error = not sub_result.ok

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                })

            messages.append({"role": "user", "content": tool_results})

        return CampaignResult(
            final_text=final_text,
            iterations=iteration,
            delegations=delegations,
            sub_agent_runs=sub_agent_runs,
            cost_usd=cost,
            messages=messages,
            stopped_reason=stopped,
        )
