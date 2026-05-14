"""Tool-scoped single-loop worker agent (the FATF sub-agent).

A SubAgent runs the standard Anthropic tool_use loop against a shared
ToolDispatcher, but restricted to a named subset of the chain tools. It is
the worker unit of the multi-agent attacker: the Coordinator (coordinator.py,
rewritten in Week 6.2) delegates a sub-objective to a SubAgent, which executes
it and reports back a structured summary via the built-in `finish_task` tool.

Why scoped tools: each FATF role (Placement / Layering / Integration) sees
only the tools relevant to its phase. A smaller tool surface plus a focused
system prompt produces better decisions and fewer tokens than one agent
juggling all 10 chain tools across three phases at once.

`finish_task` is a control-flow tool owned by the SubAgent loop — it never
reaches the ToolDispatcher. Calling it ends the loop with a structured result
(status / summary / key_facts) the Coordinator can act on without parsing a
free-text transcript. This structured hand-off is the context-isolation win:
the Coordinator works from a compact report, not the worker's full message
history.

All sub-agents in a campaign share ONE ToolDispatcher, so chain state — burner
wallets, balances, mixer deposits — flows between phases automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .llm_client import LLMClient
from .tools import ToolDispatcher


# Control-flow tool every SubAgent gets, on top of its scoped chain tools.
# Intercepted by the SubAgent loop — never dispatched to the chain.
_FINISH_TASK_SCHEMA = {
    "name": "finish_task",
    "description": (
        "Call this exactly once, as your final action, to end the task and "
        "report back to the Coordinator. Do NOT call any other tool in the "
        "same turn as finish_task. Give an honest status: 'success' if the "
        "objective was fully met, 'partial' if some of it was done, 'failed' "
        "if none of it could be done. The summary and key_facts are the ONLY "
        "things the Coordinator sees — it does NOT see your individual tool "
        "calls — so include every wallet address, amount, and deposit note "
        "the Coordinator or the next phase will need."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["success", "partial", "failed"],
                "description": "Honest outcome of the delegated objective.",
            },
            "summary": {
                "type": "string",
                "description": (
                    "Plain-language recap of what you did and the outcome "
                    "(1-4 sentences)."
                ),
            },
            "key_facts": {
                "type": "object",
                "description": (
                    "Structured outcomes the Coordinator / next phase needs: "
                    "wallet addresses, amounts moved, mixer deposit notes, etc. "
                    "Use clear, descriptive keys. Omit only if there is "
                    "genuinely nothing to pass on."
                ),
            },
        },
        "required": ["status", "summary"],
    },
}


@dataclass
class SubAgentResult:
    """Structured result of a SubAgent run — what the Coordinator acts on.

    `status` is the sub-agent's self-reported outcome via finish_task
    ('success' / 'partial' / 'failed'), or a loop-level fallback when the
    sub-agent ended without calling finish_task ('incomplete' for
    end_turn / max_iterations, 'error' for a refusal or other stop reason).
    """
    status: str
    summary: str
    key_facts: dict = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)
    iterations: int = 0
    cost_usd: float = 0.0
    messages: list[dict] = field(default_factory=list)  # full transcript, for debugging
    stopped_reason: str = ""   # 'finish_task' | 'end_turn' | 'max_iterations' | ...

    @property
    def ok(self) -> bool:
        """True if the objective was at least partially accomplished."""
        return self.status in ("success", "partial")

    def to_delegation_report(self) -> dict:
        """The compact dict handed back to the Coordinator as a tool_result.

        Deliberately excludes `messages` and per-call tool detail — the
        Coordinator works from the summary + key_facts, not the transcript.
        That isolation is the whole point of the sub-agent-as-tool design.
        """
        return {
            "status": self.status,
            "summary": self.summary,
            "key_facts": self.key_facts,
            "tool_calls": len(self.tool_calls),
            "iterations": self.iterations,
            "cost_usd": round(self.cost_usd, 6),
        }


class SubAgent:
    """A tool-scoped LLM worker that runs a tool_use loop and reports back.

    Args:
        client: LLMClient. Sharing one client across the Coordinator and all
            sub-agents makes `client.usage` track the whole campaign; pass a
            dedicated client per agent for per-agent accounting.
        dispatcher: shared ToolDispatcher (chain context + wallet registry).
            Shared across all sub-agents so state flows between phases.
        tool_names: the chain tools this sub-agent may use. Must be a subset
            of the dispatcher's tools. `finish_task` is added automatically.
        model: alias ('haiku'/'sonnet'/'opus') or full model ID. Default
            'sonnet' — the FATF role workers run on Sonnet; pass 'haiku' for
            cost-controlled dev iteration.
        name: human label for logs / results (e.g. 'placement').
        max_iterations: hard cap on the agent loop.
    """

    def __init__(
        self,
        client: LLMClient,
        dispatcher: ToolDispatcher,
        tool_names: list[str],
        *,
        model: str = "sonnet",
        name: str = "sub_agent",
        max_iterations: int = 12,
    ):
        self.client = client
        self.dispatcher = dispatcher
        self.name = name
        self.model = model
        self.max_iterations = max_iterations

        available = {d["name"] for d in dispatcher.tool_definitions}
        unknown = set(tool_names) - available
        if unknown:
            raise ValueError(
                f"SubAgent {name!r}: unknown tool(s) {sorted(unknown)}; "
                f"dispatcher offers {sorted(available)}"
            )
        self.tool_names = list(tool_names)
        # Scoped chain-tool schemas + the control-flow finish_task tool.
        self._tools = [
            d for d in dispatcher.tool_definitions if d["name"] in self.tool_names
        ] + [_FINISH_TASK_SCHEMA]

    @property
    def tool_definitions(self) -> list[dict]:
        """The tool schemas this sub-agent exposes to the LLM: its scoped
        chain-tool subset plus the control-flow `finish_task` tool."""
        return list(self._tools)

    def run(
        self,
        objective: str,
        *,
        context: str = "",
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> SubAgentResult:
        """Run the scoped tool_use loop until finish_task / end_turn / the cap.

        Args:
            objective: what this sub-agent should accomplish (plain language).
            context: optional state from the Coordinator — wallets, prior
                results, constraints. Appended to the objective as a labelled
                block so the model can tell the two apart.
            system: the sub-agent's role system prompt (FATF role instructions).
            max_tokens: per-call response cap.
        """
        user_content = objective if not context else (
            f"{objective}\n\n--- Context from the Coordinator ---\n{context}"
        )
        messages: list[dict] = [{"role": "user", "content": user_content}]
        tool_calls: list[dict] = []
        cost = 0.0
        final_text = ""
        iteration = 0
        stopped = "max_iterations"
        result: SubAgentResult | None = None

        for iteration in range(1, self.max_iterations + 1):
            response = self.client.complete(
                messages=messages,
                system=system,
                model=self.model,
                max_tokens=max_tokens,
                tools=self._tools,
            )
            cost += response.cost_usd
            messages.append({"role": "assistant", "content": response.raw.content})

            text_blocks = [b.text for b in response.raw.content if b.type == "text"]
            if text_blocks:
                final_text = "\n".join(text_blocks)

            stop = response.stop_reason
            if stop == "end_turn":
                # The model ended without calling finish_task. Tolerated, but
                # the result is unstructured — flag it via stopped_reason.
                stopped = "end_turn"
                break
            if stop != "tool_use":
                # refusal, max_tokens, pause_turn, etc. — record and exit.
                stopped = stop or "unknown"
                break

            tool_use_blocks = [b for b in response.raw.content if b.type == "tool_use"]

            # finish_task is terminal and intercepted here — it never reaches
            # the dispatcher. We break immediately, so there is no need to
            # emit tool_results for any other blocks in this turn (we never
            # make another API call).
            finish_block = next(
                (b for b in tool_use_blocks if b.name == "finish_task"), None,
            )
            if finish_block is not None:
                fin = dict(finish_block.input) if finish_block.input else {}
                stopped = "finish_task"
                result = SubAgentResult(
                    status=fin.get("status", "success"),
                    summary=fin.get("summary") or final_text,
                    key_facts=fin.get("key_facts") or {},
                    tool_calls=tool_calls,
                    iterations=iteration,
                    cost_usd=cost,
                    messages=messages,
                    stopped_reason=stopped,
                )
                break

            tool_results = []
            for block in tool_use_blocks:
                tool_input = dict(block.input) if block.input else {}
                if block.name not in self.tool_names:
                    # Defense in depth — the LLM was only given its scoped
                    # tools, but if it hallucinates a tool name, return a
                    # clean error rather than dispatching it.
                    content = (
                        f"Error: tool {block.name!r} is not available to the "
                        f"{self.name!r} agent"
                    )
                    is_error = True
                    output = None
                else:
                    dispatch_result = self.dispatcher.dispatch(block.name, tool_input)
                    content = dispatch_result.to_content()
                    is_error = dispatch_result.is_error
                    output = dispatch_result.output if not is_error else None

                tool_calls.append({
                    "name": block.name,
                    "input": tool_input,
                    "is_error": is_error,
                    "output": output,
                    "error": content if is_error else None,
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                })

            messages.append({"role": "user", "content": tool_results})

        if result is None:
            # Loop ended via end_turn / max_iterations / refusal — no
            # finish_task was called. Build a best-effort result from the
            # final assistant text so the Coordinator still gets something.
            status = "incomplete" if stopped in ("max_iterations", "end_turn") else "error"
            result = SubAgentResult(
                status=status,
                summary=final_text or f"Sub-agent {self.name!r} stopped: {stopped}",
                key_facts={},
                tool_calls=tool_calls,
                iterations=iteration,
                cost_usd=cost,
                messages=messages,
                stopped_reason=stopped,
            )
        return result
