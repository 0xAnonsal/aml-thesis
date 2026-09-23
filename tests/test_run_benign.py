"""Structural tests for the benign trace generator.

No live runs in CI — exercising the generator end-to-end needs Anvil
and is slow. The fast tests cover: weighted-choice math, arg parsing,
the activity-weights table is well-formed, and the args validation
paths. The end-to-end smoke is documented in the PR test plan and run
manually.
"""
from __future__ import annotations

import random

import pytest

from aml.detectors.run_benign import (
    _ACTIVITY_WEIGHTS,
    _weighted_choice,
    build_arg_parser,
    main,
)


# --- activity-weights health ----------------------------------------------


def test_activity_weights_well_formed():
    """Every activity key is a string; every weight is a positive float."""
    assert len(_ACTIVITY_WEIGHTS) >= 4, "want at least 4 activity types"
    for kind, weight in _ACTIVITY_WEIGHTS.items():
        assert isinstance(kind, str) and kind
        assert isinstance(weight, (int, float))
        assert weight > 0


def test_activity_weights_sum_close_to_one():
    """Weights are relative (random.choices normalises them), but they should
    stay in the neighbourhood of 1.0 so the mix reads as a distribution.
    P1-57 re-calibrated them to mainnet-like ratios without renormalising
    (sum = 0.9355), which is why the tolerance is loose."""
    total = sum(_ACTIVITY_WEIGHTS.values())
    assert 0.85 <= total <= 1.15, f"weights sum to {total}, want ~1.0"


def test_activity_weights_include_required_kinds():
    """The detector needs to see transfers, swaps, mints, and new-user joins."""
    for required in (
        "transfer_usdt", "transfer_eth", "swap_eth_for_usdt",
        "swap_usdt_for_eth", "mint_usdt", "new_user",
    ):
        assert required in _ACTIVITY_WEIGHTS, f"missing activity kind {required}"


def test_weighted_choice_respects_weights():
    """Skewed weights → the heavy key dominates a large sample."""
    weights = {"a": 0.9, "b": 0.05, "c": 0.05}
    rng = random.Random(42)
    samples = [_weighted_choice(rng, weights) for _ in range(5000)]
    freq = {k: samples.count(k) / len(samples) for k in weights}
    # `a` should be heavy, others should be roughly their weights
    assert 0.85 < freq["a"] < 0.95
    assert 0.02 < freq["b"] < 0.08
    assert 0.02 < freq["c"] < 0.08


def test_weighted_choice_deterministic_with_seed():
    """Same seed + same weights → identical sample sequence."""
    weights = {"x": 0.5, "y": 0.5}
    rng1 = random.Random(7)
    rng2 = random.Random(7)
    seq1 = [_weighted_choice(rng1, weights) for _ in range(50)]
    seq2 = [_weighted_choice(rng2, weights) for _ in range(50)]
    assert seq1 == seq2


# --- CLI args parsing ------------------------------------------------------


def test_arg_parser_defaults_make_sense():
    """Default args produce a sane cheap run."""
    args = build_arg_parser().parse_args([])
    assert args.num_users == 20
    assert args.num_txs == 100
    assert args.seed is None        # random by default
    assert args.out == "runs"
    assert args.with_pool is True   # swaps on by default
    args2 = build_arg_parser().parse_args(["--no-pool"])
    assert args2.with_pool is False


def test_main_rejects_under_2_users(capsys):
    """--num-users < 2 fails fast with a clear error and exit 2."""
    code = main(["--num-users", "1"])
    assert code == 2
    err = capsys.readouterr().err
    assert "num-users" in err.lower() or "users" in err.lower()


def test_main_rejects_under_1_tx(capsys):
    """--num-txs < 1 fails fast with a clear error and exit 2."""
    code = main(["--num-txs", "0"])
    assert code == 2
    err = capsys.readouterr().err
    assert "num-txs" in err.lower() or "txs" in err.lower()


def test_seed_arg_accepts_int():
    """--seed parses as int (not string)."""
    args = build_arg_parser().parse_args(["--seed", "12345"])
    assert args.seed == 12345
    assert isinstance(args.seed, int)


def test_num_users_and_txs_parse_as_ints():
    """Numeric args coerce to int."""
    args = build_arg_parser().parse_args(["--num-users", "5", "--num-txs", "30"])
    assert args.num_users == 5
    assert args.num_txs == 30
    assert isinstance(args.num_users, int)
    assert isinstance(args.num_txs, int)
