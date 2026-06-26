"""Structural tests for the campaign runner CLI.

These cover scenario-registry health, args parsing, and the JSON-coercion
helper. No live API calls, no Anvil — the actual run-campaign-end-to-end
path is exercised by tests/test_coordinator.py (which uses the same
scenario prompt + chain setup). Adding another live run here would just
double the live cost; structural coverage is enough.
"""
from __future__ import annotations

import json
from io import StringIO

import pytest

from aml.attackers import scenarios as scen_mod
from aml.attackers.run_campaign import build_arg_parser, main
from aml.attackers.scenarios import (
    DEFI_EXPLOIT, RANSOMWARE_CASHOUT, SCENARIOS, STABLECOIN_SCAM, Scenario,
)
from aml.chains.trace import decode_event, jsonable


# --- scenario registry health ---


def test_every_scenario_has_required_fields():
    """Each entry in SCENARIOS is a Scenario with all fields populated."""
    assert len(SCENARIOS) >= 1
    for name, s in SCENARIOS.items():
        assert isinstance(s, Scenario)
        assert s.name == name, f"key {name!r} doesn't match Scenario.name {s.name!r}"
        assert s.asset in ("eth", "usdt")
        assert s.default_amount > 0
        assert len(s.description) > 20
        assert isinstance(s.needs_pool, bool)
        assert isinstance(s.needs_tornado, bool)
        # Exits are dynamic now (created by the Integration sub-agent via
        # register_clean_exit), so the scenario only needs alice + amount
        # placeholders. No more {clean_exits} block.
        assert "{alice}" in s.user_prompt_template
        assert "{amount}" in s.user_prompt_template
        assert "{clean_exits}" not in s.user_prompt_template


def test_defi_exploit_prompt_formats_with_alice_and_amount():
    """Format produces a clean prompt with no leftover placeholders."""
    formatted = DEFI_EXPLOIT.format_prompt(alice="0x" + "1" * 40, amount=3.0)
    assert "0x1111111111111111111111111111111111111111" in formatted
    assert "3.0 ETH" in formatted
    # No unfilled placeholders left over
    assert "{alice}" not in formatted
    assert "{clean_exits}" not in formatted
    assert "{amount}" not in formatted
    # Prompt instructs the Coordinator to decide exit count + platforms
    assert "register_clean_exit" in formatted
    assert "Binance" in formatted or "Coinbase" in formatted


def test_defi_exploit_prompt_tells_coordinator_to_decide_exit_count():
    """The dynamic-exits change requires the prompt to explain the heuristic."""
    formatted = DEFI_EXPLOIT.format_prompt(alice="0x" + "1" * 40, amount=3.0)
    # Heuristic mentions the CTR-cap-driven minimum AND platform spread
    assert "999" in formatted
    assert "platform" in formatted.lower()


def test_defi_exploit_scenario_needs_mixer_and_pool():
    """The DeFi-exploit scenario asks for both the mixer and the swap pool."""
    assert DEFI_EXPLOIT.needs_tornado is True
    assert DEFI_EXPLOIT.needs_pool is True
    assert DEFI_EXPLOIT.asset == "eth"


def test_stablecoin_scam_scenario_has_no_mixer():
    """Stablecoin-scam is USDT-only — no Tornado deployed."""
    assert STABLECOIN_SCAM.asset == "usdt"
    assert STABLECOIN_SCAM.needs_tornado is False
    # Pool stays available so the agent can asset-cycle if it chooses
    assert STABLECOIN_SCAM.needs_pool is True
    assert STABLECOIN_SCAM.default_amount > 999  # must exceed CTR cap


def test_stablecoin_scam_prompt_mentions_no_mixer():
    """Prompt must instruct the agent to fall back without mixer."""
    formatted = STABLECOIN_SCAM.format_prompt(
        alice="0x" + "1" * 40, amount=8000.0,
    )
    assert "no" in formatted.lower() and "mixer" in formatted.lower()
    assert "register_clean_exit" in formatted
    assert "999" in formatted
    assert "smurf" in formatted.lower() or "structur" in formatted.lower()


def test_ransomware_cashout_scenario_is_eth_heavy_mixer():
    """Ransomware scenario uses the mixer heavily; larger amount than defi-exploit."""
    assert RANSOMWARE_CASHOUT.asset == "eth"
    assert RANSOMWARE_CASHOUT.needs_tornado is True
    assert RANSOMWARE_CASHOUT.needs_pool is True
    # Larger scale than defi-exploit drives more mixer cycles / exits
    assert RANSOMWARE_CASHOUT.default_amount > DEFI_EXPLOIT.default_amount


def test_ransomware_cashout_prompt_emphasises_mixer():
    """Prompt must highlight heavy mixer use as the defining tactic."""
    formatted = RANSOMWARE_CASHOUT.format_prompt(
        alice="0x" + "1" * 40, amount=5.0,
    )
    assert "mixer" in formatted.lower()
    assert "register_clean_exit" in formatted
    assert "999" in formatted
    assert "ransom" in formatted.lower()


def test_three_scenarios_registered():
    """SCENARIOS dict has all three FATF typologies."""
    assert set(SCENARIOS.keys()) == {
        "defi-exploit", "stablecoin-scam", "ransomware-cashout",
    }


# --- CLI args parsing ---


def test_arg_parser_requires_scenario_or_list(capsys):
    """Running without --scenario and without --list-scenarios is an error."""
    code = main([])
    assert code == 2
    err = capsys.readouterr().err
    assert "scenario" in err.lower()


def test_arg_parser_rejects_unknown_scenario():
    """Unknown --scenario value fails argparse with SystemExit (exit 2)."""
    with pytest.raises(SystemExit) as exc_info:
        build_arg_parser().parse_args(["--scenario", "no-such-scenario"])
    assert exc_info.value.code == 2


def test_list_scenarios_prints_all_and_exits_zero(capsys):
    """`--list-scenarios` lists every registered scenario and returns 0."""
    code = main(["--list-scenarios"])
    assert code == 0
    out = capsys.readouterr().out
    for name, s in SCENARIOS.items():
        assert name in out
        # First few words of the description should appear in the output
        assert s.description.split(".")[0][:30] in out


def test_arg_parser_defaults_make_sense():
    """The default flags produce a sane runtime config (cheap Haiku run)."""
    args = build_arg_parser().parse_args(["--scenario", "defi-exploit"])
    assert args.scenario == "defi-exploit"
    assert args.model == "haiku"
    assert args.out == "runs"
    assert args.max_iterations == 12
    assert args.sub_agent_max_iterations == 20
    assert args.max_tokens == 2048
    assert args.amount is None   # falls back to scenario.default_amount
    assert args.seed is None     # falls back to random
    # --num-clean-exits removed: exits are now dynamic, created by the
    # Integration sub-agent via register_clean_exit at runtime.
    assert not hasattr(args, "num_clean_exits")
    assert args.list_scenarios is False


# --- JSON coercion helper ---


def test_jsonable_handles_bytes_and_big_ints():
    """`jsonable` makes web3 / bytes / huge-int values JSON-serializable."""
    big = 2**100
    payload = {
        "bytes_val": b"\x01\x02\x03",
        "bytearray_val": bytearray(b"\xaa\xbb"),
        "small_int": 42,
        "big_int": big,
        "string": "hello",
        "nested": {"inner_bytes": b"\xff", "list_of_bigs": [big, 7]},
        "tuple": (b"\x00", 1, "x"),
    }
    safe = jsonable(payload)
    # Round-trips through json
    text = json.dumps(safe)
    back = json.loads(text)
    assert back["bytes_val"] == "0x010203"
    assert back["bytearray_val"] == "0xaabb"
    assert back["small_int"] == 42
    assert back["big_int"] == str(big)
    assert back["string"] == "hello"
    assert back["nested"]["inner_bytes"] == "0xff"
    assert back["nested"]["list_of_bigs"] == [str(big), 7]
    assert back["tuple"] == ["0x00", 1, "x"]


def test_decode_event_returns_none_for_unknown_contract():
    """`decode_event` against an empty known_contracts dict returns None."""
    class FakeLog:
        address = "0x" + "f" * 40
    assert decode_event(FakeLog(), known_contracts={}) is None
    # None contract entries (e.g. pool not deployed) are skipped, not crashed on
    assert decode_event(FakeLog(), known_contracts={"pool": None}) is None
