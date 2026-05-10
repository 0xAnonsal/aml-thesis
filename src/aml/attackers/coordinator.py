"""Single-agent multi-turn tool-use loop.

The Coordinator is the simplest LLM-driven agent in the project: given a
system prompt and a user goal, it runs the standard Anthropic tool_use
loop until the model returns `end_turn` (or hits `max_iterations`).

Each iteration:
  1. Call LLMClient with current messages + tool definitions
  2. If response has tool_use blocks: dispatch them via ToolDispatcher,
     append the tool_results as a user-role message, loop
  3. If `stop_reason == "end_turn"`: done

Returns CampaignResult: final assistant text + cost + iteration count
+ tool calls made + the full messages list (for debugging).

Week 5 uses one Coordinator agent for the whole campaign; Week 6 will
split this into Coordinator + Placement + Layering + Integration sub-
agents (FATF roles), each with its own focused tool subset.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .llm_client import LLMClient
from .tools import ToolDispatcher


@dataclass
class CampaignResult:
    """The result of running an agent loop."""
    final_text: str
    iterations: int
    tool_calls: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    messages: list[dict] = field(default_factory=list)  # full conversation history for debugging
    stopped_reason: str = ""    # 'end_turn' | 'max_iterations' | 'max_tokens' | 'refusal' | ...

    @property
    def successful(self) -> bool:
        return self.stopped_reason == "end_turn"


class Coordinator:
    """LLM agent that drives a tool_use loop against a ToolDispatcher.

    Args:
        client: LLMClient (one per agent or per campaign — its `usage`
            tracker reflects this agent's spend).
        dispatcher: ToolDispatcher holding chain context + tool definitions.
        model: alias ('haiku'/'sonnet'/'opus') or full model ID. Default
            'haiku' for cost-controlled dev iteration.
        max_iterations: hard cap on the agent loop. Prevents runaway tool
            chains if the model keeps requesting tools indefinitely.
    """

    def __init__(
        self,
        client: LLMClient,
        dispatcher: ToolDispatcher,
        *,
        model: str = "haiku",
        max_iterations: int = 10,
    ):
        self.client = client
        self.dispatcher = dispatcher
        self.model = model
        self.max_iterations = max_iterations

    def run(
        self,
        user_prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> CampaignResult:
        """Run the agentic loop until the LLM stops calling tools.

        Returns when one of:
          - LLM responds with `stop_reason == "end_turn"` (success)
          - LLM responds with another stop reason (refusal, max_tokens, etc.)
          - max_iterations is reached
        """
        messages: list[dict] = [{"role": "user", "content": user_prompt}]
        tools = self.dispatcher.tool_definitions
        tool_calls: list[dict] = []
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

            # Append the assistant response (full content blocks — tool_use
            # blocks must be preserved verbatim for the API to match the
            # corresponding tool_result on the next turn).
            messages.append({"role": "assistant", "content": response.raw.content})

            # Capture text from this turn (overwrites; final iteration's text wins).
            text_blocks = [b.text for b in response.raw.content if b.type == "text"]
            if text_blocks:
                final_text = "\n".join(text_blocks)

            stop = response.stop_reason

            if stop == "end_turn":
                stopped = "end_turn"
                break

            if stop != "tool_use":
                # refusal, max_tokens, etc. — record and exit
                stopped = stop or "unknown"
                break

            # Execute every tool_use block this turn and collect tool_results.
            tool_use_blocks = [b for b in response.raw.content if b.type == "tool_use"]
            tool_results = []
            for block in tool_use_blocks:
                # block.input is a SDK-side dict-like; normalize to a plain dict
                # so the dispatcher receives the same shape as in unit tests.
                tool_input = dict(block.input) if block.input else {}
                result = self.dispatcher.dispatch(block.name, tool_input)
                tool_calls.append({
                    "name": block.name,
                    "input": tool_input,
                    "is_error": result.is_error,
                    "output": result.output if not result.is_error else None,
                    "error": result.error,
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result.to_content(),
                    "is_error": result.is_error,
                })

            # Append tool results as a user-role message (Anthropic convention).
            messages.append({"role": "user", "content": tool_results})

        return CampaignResult(
            final_text=final_text,
            iterations=iteration,
            tool_calls=tool_calls,
            cost_usd=cost,
            messages=messages,
            stopped_reason=stopped,
        )
