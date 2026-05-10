"""Anthropic SDK wrapper for the AML attacker agents.

This is the foundation for every LLM-driven agent in the project — Coordinator
(Opus), Placement / Layering / Integration sub-agents (Sonnet), Haiku for
dev iterations.

Provides:
- Model alias selection (haiku / sonnet / opus → current model IDs)
- Prompt caching on system prompts (5-minute ephemeral by default — agents
  reuse long FATF-typology system prompts across many calls per campaign,
  this is where the cost savings live)
- Cumulative USD cost tracking across calls (input + output + cache write
  + cache read pricing applied per Anthropic's 2026-04-29 catalog)
- Clean response shape (CallResult: text + usage + cost + raw SDK message)

Tool use will land in a follow-up PR; the `complete()` signature already
accepts `tools` and `tool_choice` so adding it is a no-op for callers.

Pricing (per 1M tokens, 2026-04-29):
  opus    claude-opus-4-7    $5.00 in / $25.00 out
  sonnet  claude-sonnet-4-6  $3.00 in / $15.00 out
  haiku   claude-haiku-4-5   $1.00 in / $5.00  out

Cache multipliers (vs base input cost):
  write (5-min ephemeral): 1.25×
  read  (any TTL):         0.10×

Minimum cacheable system-prompt size (otherwise cache_control is silently a
no-op — no error, just cache_creation_tokens=0):
  opus    4096 tokens
  haiku   4096 tokens
  sonnet  2048 tokens
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import anthropic


# Alias → (model_id, input $/M, output $/M)
_MODELS: dict[str, tuple[str, float, float]] = {
    "opus":   ("claude-opus-4-7",   5.00, 25.00),
    "sonnet": ("claude-sonnet-4-6", 3.00, 15.00),
    "haiku":  ("claude-haiku-4-5",  1.00,  5.00),
}

# Reverse lookup: model_id → input $/M, output $/M (so pass-through IDs price too)
_PRICING_BY_MODEL_ID: dict[str, tuple[float, float]] = {
    model_id: (input_p, output_p) for (model_id, input_p, output_p) in _MODELS.values()
}

_CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute ephemeral
_CACHE_READ_MULTIPLIER  = 0.10  # all TTLs


@dataclass
class CallResult:
    """One LLM call's text + usage + cost.

    `cost_usd` already includes cache write + cache read pricing; just sum
    `cost_usd` across calls for total spend (or use UsageSummary on the client).
    """
    text: str
    model: str
    input_tokens: int          # uncached input
    output_tokens: int
    cache_read_tokens: int     # served from cache at 0.10× input cost
    cache_creation_tokens: int # written to cache at 1.25× input cost
    cost_usd: float
    request_id: str | None
    raw: anthropic.types.Message   # full SDK response for tool_use, thinking blocks, etc.
    stop_reason: str | None = None # 'end_turn' | 'tool_use' | 'max_tokens' | 'refusal' | ...


@dataclass
class UsageSummary:
    """Cumulative usage across every call on a client."""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"calls={self.calls}  in={self.input_tokens:,}  out={self.output_tokens:,}  "
            f"cache_read={self.cache_read_tokens:,}  cache_write={self.cache_creation_tokens:,}  "
            f"cost=${self.cost_usd:.4f}"
        )


class LLMClient:
    """Wrapper around anthropic.Anthropic for the AML agent suite.

    Use one instance per agent (or per campaign) so `client.usage` reflects that
    agent's spend. The underlying SDK client is created in __init__ and reused;
    the SDK handles HTTP retries (429, 5xx) automatically with exponential
    backoff.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        max_retries: int = 2,
        timeout: float = 600.0,
    ):
        # api_key=None → SDK reads ANTHROPIC_API_KEY env var
        self._client = anthropic.Anthropic(
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
        )
        self.usage = UsageSummary()

    @staticmethod
    def resolve_model(alias_or_id: str) -> str:
        """Map 'haiku'/'sonnet'/'opus' to a current model ID; pass-through otherwise."""
        if alias_or_id in _MODELS:
            return _MODELS[alias_or_id][0]
        return alias_or_id

    @staticmethod
    def _pricing(model_id: str) -> tuple[float, float]:
        """Return (input $/M, output $/M) for a model ID, or (0, 0) for unknown."""
        return _PRICING_BY_MODEL_ID.get(model_id, (0.0, 0.0))

    def complete(
        self,
        prompt: str | None = None,
        *,
        messages: list[dict] | None = None,
        system: str | None = None,
        model: str = "haiku",
        max_tokens: int = 4096,
        cache_system: bool = True,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        thinking: dict | None = None,
        extra: dict | None = None,
    ) -> CallResult:
        """Send a single Messages API call and return parsed text + usage + cost.

        Args:
            prompt: single user-turn content. Sugar for
                ``messages=[{"role": "user", "content": prompt}]``. Mutually
                exclusive with ``messages``.
            messages: full conversation history (list of role/content dicts).
                Use this for multi-turn agentic loops (see Coordinator). Mutually
                exclusive with ``prompt``.
            system: optional system prompt. Cached by default if `cache_system`.
            model: 'haiku' / 'sonnet' / 'opus', or a full model ID for pinning.
            max_tokens: response token cap. Default 4096 (safe for Haiku/Sonnet
                without forcing streaming; bump for long Coordinator plans).
            cache_system: if True and `system` is set, mark the system block with
                `cache_control={"type": "ephemeral"}` (5-minute TTL). Cache only
                fires if `system` ≥ the model's minimum cacheable size; below
                that the marker is silently ignored.
            tools: tool definitions for tool use. Pass `dispatcher.tool_definitions`.
            tool_choice: tool choice override. Pass-through.
            thinking: thinking config. For Opus 4.7 use `{"type": "adaptive"}`.
                Default off (matches Opus 4.7's default behavior).
            extra: any additional kwargs forwarded to messages.create — escape
                hatch for features we haven't surfaced yet.

        Raises:
            ValueError: if neither `prompt` nor `messages` is provided, or if both are.
        """
        if prompt is None and messages is None:
            raise ValueError("LLMClient.complete: provide either `prompt` or `messages`")
        if prompt is not None and messages is not None:
            raise ValueError("LLMClient.complete: pass `prompt` OR `messages`, not both")

        msg_list = messages if messages is not None else [{"role": "user", "content": prompt}]

        model_id = self.resolve_model(model)

        kwargs: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": msg_list,
        }

        if system is not None:
            if cache_system:
                # Explicit cache_control on a system text block. The top-level
                # `cache_control` kwarg on messages.create() would do the same
                # automatically, but this form makes the cache target obvious
                # in code review and debugging.
                kwargs["system"] = [{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
                kwargs["system"] = system

        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if thinking is not None:
            kwargs["thinking"] = thinking
        if extra:
            kwargs.update(extra)

        response = self._client.messages.create(**kwargs)

        # Concatenate text blocks; tool_use / thinking blocks live in `raw`.
        text = "".join(block.text for block in response.content if block.type == "text")

        usage = response.usage
        in_tok       = usage.input_tokens
        out_tok      = usage.output_tokens
        cache_read   = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0

        input_per_m, output_per_m = self._pricing(model_id)
        cost = (
            in_tok       * input_per_m  / 1_000_000
            + out_tok      * output_per_m / 1_000_000
            + cache_create * input_per_m  * _CACHE_WRITE_MULTIPLIER / 1_000_000
            + cache_read   * input_per_m  * _CACHE_READ_MULTIPLIER  / 1_000_000
        )

        # Update cumulative tracker
        self.usage.calls += 1
        self.usage.input_tokens += in_tok
        self.usage.output_tokens += out_tok
        self.usage.cache_read_tokens += cache_read
        self.usage.cache_creation_tokens += cache_create
        self.usage.cost_usd += cost
        per_model = self.usage.by_model.setdefault(model_id, {
            "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd": 0.0,
        })
        per_model["calls"] += 1
        per_model["input_tokens"] += in_tok
        per_model["output_tokens"] += out_tok
        per_model["cache_read_tokens"] += cache_read
        per_model["cache_creation_tokens"] += cache_create
        per_model["cost_usd"] += cost

        return CallResult(
            text=text,
            model=model_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_create,
            cost_usd=cost,
            request_id=getattr(response, "_request_id", None),
            raw=response,
            stop_reason=getattr(response, "stop_reason", None),
        )
