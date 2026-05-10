"""Smoke tests for the LLMClient wrapper.

Live-API tests skip if ANTHROPIC_API_KEY isn't set. They use Haiku 4.5
(cheapest model — $1/M input, $5/M output) so the full test run costs
under $0.001 even with prompt caching disabled.
"""
from __future__ import annotations

import os

import pytest

from aml.attackers import CallResult, LLMClient, UsageSummary


needs_api_key = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; cannot make live API calls",
)


def test_resolve_model_aliases():
    """Aliases map to current model IDs; full IDs pass through."""
    assert LLMClient.resolve_model("haiku") == "claude-haiku-4-5"
    assert LLMClient.resolve_model("sonnet") == "claude-sonnet-4-6"
    assert LLMClient.resolve_model("opus") == "claude-opus-4-7"
    # Pass-through for explicit model IDs
    assert LLMClient.resolve_model("claude-haiku-4-5") == "claude-haiku-4-5"
    assert LLMClient.resolve_model("claude-opus-4-6") == "claude-opus-4-6"


def test_usage_summary_str_is_human_readable():
    """UsageSummary's __str__ is a one-line debug summary."""
    summary = UsageSummary(
        calls=3, input_tokens=1234, output_tokens=567,
        cache_read_tokens=8000, cache_creation_tokens=4000, cost_usd=0.0123,
    )
    s = str(summary)
    assert "calls=3" in s
    assert "in=1,234" in s
    assert "out=567" in s
    assert "cache_read=8,000" in s
    assert "cache_write=4,000" in s
    assert "cost=$0.0123" in s


@needs_api_key
def test_completes_with_haiku():
    """Live: Haiku echoes back a single token. Costs ~$0.0001."""
    client = LLMClient()
    result = client.complete(
        "Reply with exactly the word: PONG",
        model="haiku",
        max_tokens=16,
    )
    assert isinstance(result, CallResult)
    assert "PONG" in result.text.upper()
    assert result.model == "claude-haiku-4-5"
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.cost_usd > 0
    # Trivial prompt → no cache write
    assert result.cache_creation_tokens == 0


@needs_api_key
def test_usage_accumulates_across_calls():
    """Two Haiku calls accumulate into client.usage."""
    client = LLMClient()
    client.complete("Say A", model="haiku", max_tokens=8)
    client.complete("Say B", model="haiku", max_tokens=8)

    assert client.usage.calls == 2
    assert client.usage.input_tokens > 0
    assert client.usage.output_tokens > 0
    assert client.usage.cost_usd > 0
    assert "claude-haiku-4-5" in client.usage.by_model
    assert client.usage.by_model["claude-haiku-4-5"]["calls"] == 2


@needs_api_key
def test_system_prompt_passes_through():
    """A system prompt steers the response. Costs ~$0.0001."""
    client = LLMClient()
    result = client.complete(
        "What is 2+2?",
        system="You are a calculator that responds with only digits, no words.",
        model="haiku",
        max_tokens=8,
        cache_system=False,   # too short to actually cache anyway
    )
    assert "4" in result.text
    # System tokens contribute to input_tokens
    assert result.input_tokens >= 10
