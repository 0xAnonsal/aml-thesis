"""Benign-baseline trace generator — emits normal-looking on-chain
activity in the same artifact format as aml.attackers.run_campaign,
labelled as the negative class for detector training.

Usage:
    python -m aml.detectors.run_benign \\
        --num-users 20 --num-txs 100 --seed 42 --out runs/benign/

What it generates: N synthetic users, each seeded with ETH (from the
faucet/deployer) and minted USDT, then M random activities sampled from
a weighted mix of transfer_usdt / transfer_eth / swap_eth_for_usdt /
swap_usdt_for_eth / occasional mint / occasional new user. No mixer,
no smurfing, no consolidation patterns — the laundering-specific
behaviour the detector is supposed to learn to flag.

Output directory schema MATCHES the attacker runner so the downstream
dataset combiner (Week 7.3) treats both uniformly:
    meta.json          args, timestamps, anvil chain id, block boundaries
    chain_trace.jsonl  one record per on-chain tx (decoded events)
    addresses.json     every address labelled benign_user or infrastructure
    summary.txt        human-readable one-pager
    (no campaign.json — no LLM ran)

Each address in addresses.json gets a `label` so the downstream graph
extractor can colour benign users vs deployer/contracts without
guessing. The full dataset (attacker runs + benign runs) gives the
detector both positive and negative ground truth, which is what
supervised training needs.

Determinism: fully seeded — same --seed + same --num-* args reproduce
the same trace bit-for-bit (modulo Anvil's deterministic-but-version-
sensitive default block timestamp). Useful for re-running a single
campaign to debug a detector.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from eth_account import Account
from web3 import Web3

from aml.chains import AnvilNode
from aml.chains.eth_stack import (
    POOL_BOOTSTRAP_ETH_WEI,
    POOL_BOOTSTRAP_USDT_BASE,
    deploy_pool,
    deploy_usdt,
    raw_tx,
    send_tx,
)
from aml.chains.trace import extract_chain_trace


# Activity mix and weights. Tuned to roughly mimic a small-population
# Anvil chain: most txs are simple transfers; swaps happen occasionally;
# mints + new-user joins are rare. The exact weights aren't sacred —
# what matters is that the resulting graph has SHAPE without containing
# laundering-specific patterns (smurf bursts, mixer deposits/withdrawals,
# consolidation funnels into clean exits).
_ACTIVITY_WEIGHTS: dict[str, float] = {
    "transfer_usdt":     0.40,
    "transfer_eth":      0.20,
    "swap_eth_for_usdt": 0.15,
    "swap_usdt_for_eth": 0.15,
    "mint_usdt":         0.05,   # "user got paid off-chain"
    "new_user":          0.05,   # "another user joins the ecosystem"
}

# Per-user starting endowment ranges, in human units. Deliberately wide
# so the graph has heterogeneous wealth — real chains do too.
_INITIAL_ETH_MIN = 0.2
_INITIAL_ETH_MAX = 5.0
_INITIAL_USDT_MIN = 100.0
_INITIAL_USDT_MAX = 5000.0

# Per-tx amount ranges, in human units. Kept modest so we don't exhaust
# users mid-run (the activity loop just skips a tx if the sampled
# sender doesn't hold enough).
_TRANSFER_USDT_MIN = 10.0
_TRANSFER_USDT_MAX = 500.0
_TRANSFER_ETH_MIN = 0.01
_TRANSFER_ETH_MAX = 1.0
_SWAP_ETH_MIN = 0.05
_SWAP_ETH_MAX = 0.5
_SWAP_USDT_MIN = 50.0
_SWAP_USDT_MAX = 800.0
_MINT_USDT_MIN = 100.0
_MINT_USDT_MAX = 2000.0

# Gas-reserve floor for benign users — they need to keep enough ETH to
# pay gas, same as anyone else. Matches the attacker-side floor so
# behaviour is consistent.
_GAS_RESERVE_ETH = 0.05
_ETH_TRANSFER_GAS = 21_000


def _weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights.keys())
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def _seed_user_wallet(
    w3, deployer: str, deployer_key: str, usdt, recipient: str,
    eth_amount: float, usdt_amount: float,
) -> None:
    """Send ETH and mint USDT to a fresh user wallet, both from deployer."""
    # ETH transfer from deployer
    tx = {
        "from": deployer,
        "to": recipient,
        "value": int(eth_amount * 10**18),
        "nonce": w3.eth.get_transaction_count(deployer),
        "gas": _ETH_TRANSFER_GAS,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, private_key=deployer_key)
    w3.eth.wait_for_transaction_receipt(
        w3.eth.send_raw_transaction(raw_tx(signed))
    )
    # USDT mint
    send_tx(w3, usdt.functions.mint(recipient, int(usdt_amount * 10**6)),
            deployer, deployer_key, gas=200_000)


def _do_transfer_usdt(rng, w3, usdt, users, amount_range) -> bool:
    """Pick a sender with enough USDT, transfer a random amount to another user."""
    sender_pool = [u for u in users if u["usdt"] > 0]
    if not sender_pool:
        return False
    sender = rng.choice(sender_pool)
    recipient = rng.choice([u for u in users if u["address"] != sender["address"]])
    max_amt = min(amount_range[1], sender["usdt"])
    if max_amt < amount_range[0]:
        return False
    amount = rng.uniform(amount_range[0], max_amt)
    amount_base = int(amount * 10**6)

    tx = usdt.functions.transfer(recipient["address"], amount_base).build_transaction({
        "from": sender["address"],
        "nonce": w3.eth.get_transaction_count(sender["address"]),
        "gas": 200_000,
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=sender["key"])
    receipt = w3.eth.wait_for_transaction_receipt(
        w3.eth.send_raw_transaction(raw_tx(signed))
    )
    if receipt.status == 1:
        sender["usdt"] -= amount
        recipient["usdt"] += amount
        return True
    return False


def _do_transfer_eth(rng, w3, users, amount_range) -> bool:
    """Pick a sender with enough ETH above reserve, transfer to another user."""
    gas_cost_eth = _ETH_TRANSFER_GAS * w3.eth.gas_price / 10**18
    sender_pool = [
        u for u in users
        if u["eth"] - amount_range[0] - gas_cost_eth >= _GAS_RESERVE_ETH
    ]
    if not sender_pool:
        return False
    sender = rng.choice(sender_pool)
    recipient = rng.choice([u for u in users if u["address"] != sender["address"]])
    max_amt = min(amount_range[1], sender["eth"] - _GAS_RESERVE_ETH - gas_cost_eth)
    if max_amt < amount_range[0]:
        return False
    amount = rng.uniform(amount_range[0], max_amt)

    tx = {
        "from": sender["address"],
        "to": recipient["address"],
        "value": int(amount * 10**18),
        "nonce": w3.eth.get_transaction_count(sender["address"]),
        "gas": _ETH_TRANSFER_GAS,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, private_key=sender["key"])
    receipt = w3.eth.wait_for_transaction_receipt(
        w3.eth.send_raw_transaction(raw_tx(signed))
    )
    if receipt.status == 1:
        sender["eth"] -= (amount + gas_cost_eth)
        recipient["eth"] += amount
        return True
    return False


def _do_swap_eth_for_usdt(rng, w3, usdt, pool, users, amount_range) -> bool:
    if pool is None:
        return False
    gas_cost_eth = 200_000 * w3.eth.gas_price / 10**18
    sender_pool = [
        u for u in users
        if u["eth"] - amount_range[0] - gas_cost_eth >= _GAS_RESERVE_ETH
    ]
    if not sender_pool:
        return False
    sender = rng.choice(sender_pool)
    max_amt = min(amount_range[1], sender["eth"] - _GAS_RESERVE_ETH - gas_cost_eth)
    if max_amt < amount_range[0]:
        return False
    amount = rng.uniform(amount_range[0], max_amt)

    tx = pool.functions.swapETHForUSDT(0).build_transaction({
        "from": sender["address"],
        "nonce": w3.eth.get_transaction_count(sender["address"]),
        "gas": 200_000,
        "gasPrice": w3.eth.gas_price,
        "value": int(amount * 10**18),
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=sender["key"])
    try:
        receipt = w3.eth.wait_for_transaction_receipt(
            w3.eth.send_raw_transaction(raw_tx(signed))
        )
    except Exception:   # noqa: BLE001 — pool drift / slippage extreme
        return False
    if receipt.status != 1:
        return False
    # Re-read sender balances; pool dynamics make pre-computing tricky
    sender["eth"] = w3.eth.get_balance(sender["address"]) / 10**18
    sender["usdt"] = usdt.functions.balanceOf(sender["address"]).call() / 10**6
    return True


def _do_swap_usdt_for_eth(rng, w3, usdt, pool, users, amount_range) -> bool:
    if pool is None:
        return False
    gas_cost_eth = 350_000 * w3.eth.gas_price / 10**18   # approve + swap
    sender_pool = [
        u for u in users
        if u["usdt"] >= amount_range[0]
        and u["eth"] - gas_cost_eth >= _GAS_RESERVE_ETH
    ]
    if not sender_pool:
        return False
    sender = rng.choice(sender_pool)
    max_amt = min(amount_range[1], sender["usdt"])
    if max_amt < amount_range[0]:
        return False
    amount = rng.uniform(amount_range[0], max_amt)
    amount_base = int(amount * 10**6)

    # Approve
    nonce = w3.eth.get_transaction_count(sender["address"])
    approve_tx = usdt.functions.approve(pool.address, amount_base).build_transaction({
        "from": sender["address"], "nonce": nonce,
        "gas": 100_000, "gasPrice": w3.eth.gas_price,
    })
    signed_a = w3.eth.account.sign_transaction(approve_tx, private_key=sender["key"])
    rec_a = w3.eth.wait_for_transaction_receipt(
        w3.eth.send_raw_transaction(raw_tx(signed_a))
    )
    if rec_a.status != 1:
        return False
    # Swap
    swap_tx = pool.functions.swapUSDTForETH(amount_base, 0).build_transaction({
        "from": sender["address"], "nonce": nonce + 1,
        "gas": 250_000, "gasPrice": w3.eth.gas_price,
    })
    signed_s = w3.eth.account.sign_transaction(swap_tx, private_key=sender["key"])
    try:
        rec_s = w3.eth.wait_for_transaction_receipt(
            w3.eth.send_raw_transaction(raw_tx(signed_s))
        )
    except Exception:   # noqa: BLE001
        return False
    if rec_s.status != 1:
        return False
    sender["eth"] = w3.eth.get_balance(sender["address"]) / 10**18
    sender["usdt"] = usdt.functions.balanceOf(sender["address"]).call() / 10**6
    return True


def _do_mint_usdt(rng, w3, usdt, deployer, deployer_key, users, amount_range) -> bool:
    recipient = rng.choice(users)
    amount = rng.uniform(amount_range[0], amount_range[1])
    amount_base = int(amount * 10**6)
    send_tx(w3, usdt.functions.mint(recipient["address"], amount_base),
            deployer, deployer_key, gas=200_000)
    recipient["usdt"] += amount
    return True


def _do_new_user(rng, w3, usdt, deployer, deployer_key, users) -> bool:
    acct = Account.create()
    eth_amount = rng.uniform(_INITIAL_ETH_MIN, _INITIAL_ETH_MAX)
    usdt_amount = rng.uniform(_INITIAL_USDT_MIN, _INITIAL_USDT_MAX)
    _seed_user_wallet(
        w3, deployer, deployer_key, usdt, acct.address,
        eth_amount, usdt_amount,
    )
    users.append({
        "address": acct.address,
        "key": acct.key.hex(),
        "eth": eth_amount,
        "usdt": usdt_amount,
    })
    return True


def generate_benign(
    w3, usdt, pool, deployer: str, deployer_key: str,
    *, num_users: int, num_txs: int, seed: int,
) -> tuple[list[dict], dict[str, int]]:
    """Build N users, run M activities. Returns (users, activity_counts)."""
    rng = random.Random(seed)

    # Bootstrap N users.
    users: list[dict] = []
    for _ in range(num_users):
        acct = Account.create()
        eth_amount = rng.uniform(_INITIAL_ETH_MIN, _INITIAL_ETH_MAX)
        usdt_amount = rng.uniform(_INITIAL_USDT_MIN, _INITIAL_USDT_MAX)
        _seed_user_wallet(
            w3, deployer, deployer_key, usdt, acct.address,
            eth_amount, usdt_amount,
        )
        users.append({
            "address": acct.address,
            "key": acct.key.hex(),
            "eth": eth_amount,
            "usdt": usdt_amount,
        })

    # Run M activities.
    activity_counts: dict[str, int] = {k: 0 for k in _ACTIVITY_WEIGHTS}
    activity_counts["skipped"] = 0
    for _ in range(num_txs):
        kind = _weighted_choice(rng, _ACTIVITY_WEIGHTS)
        if kind == "transfer_usdt":
            ok = _do_transfer_usdt(rng, w3, usdt, users,
                                   (_TRANSFER_USDT_MIN, _TRANSFER_USDT_MAX))
        elif kind == "transfer_eth":
            ok = _do_transfer_eth(rng, w3, users,
                                  (_TRANSFER_ETH_MIN, _TRANSFER_ETH_MAX))
        elif kind == "swap_eth_for_usdt":
            ok = _do_swap_eth_for_usdt(rng, w3, usdt, pool, users,
                                       (_SWAP_ETH_MIN, _SWAP_ETH_MAX))
        elif kind == "swap_usdt_for_eth":
            ok = _do_swap_usdt_for_eth(rng, w3, usdt, pool, users,
                                       (_SWAP_USDT_MIN, _SWAP_USDT_MAX))
        elif kind == "mint_usdt":
            ok = _do_mint_usdt(rng, w3, usdt, deployer, deployer_key, users,
                               (_MINT_USDT_MIN, _MINT_USDT_MAX))
        elif kind == "new_user":
            ok = _do_new_user(rng, w3, usdt, deployer, deployer_key, users)
        else:
            ok = False
        if ok:
            activity_counts[kind] += 1
        else:
            activity_counts["skipped"] += 1

    return users, activity_counts


def run_benign_main(args) -> Path:
    seed = args.seed if args.seed is not None else random.randint(1, 10**9)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    run_name = f"{timestamp}_benign_seed{seed}"
    out_dir = Path(args.out) / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[benign] starting {run_name} → {out_dir}", file=sys.stderr)
    start_wall = time.time()

    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]

        usdt = deploy_usdt(w3, deployer, deployer_key)
        pool = deploy_pool(w3, deployer, deployer_key, usdt) if args.with_pool else None
        deploy_end_block = w3.eth.block_number

        print(
            f"[benign] chain ready (block {deploy_end_block}); "
            f"generating {args.num_users} users × {args.num_txs} txs...",
            file=sys.stderr,
        )
        users, activity_counts = generate_benign(
            w3, usdt, pool, deployer, deployer_key,
            num_users=args.num_users, num_txs=args.num_txs, seed=seed,
        )
        end_block = w3.eth.block_number
        wall_clock = time.time() - start_wall
        print(
            f"[benign] generated {len(users)} users in {wall_clock:.1f}s; "
            f"end block {end_block}",
            file=sys.stderr,
        )

        # Build labelled address registry. All organic users are
        # `benign_user`. Deployer is the "operator/infrastructure" who
        # also runs the pool — labelled so the graph extractor can
        # exclude it from clustering or treat it as a known hub.
        addresses = {
            "benign_users": [u["address"] for u in users],
            "operator_wallet": deployer,
            "contracts": {
                "usdt": usdt.address,
                "pool": pool.address if pool is not None else None,
            },
            "labels": {
                **{u["address"]: "benign_user" for u in users},
                deployer: "infrastructure",
                usdt.address: "contract",
                **({pool.address: "contract"} if pool is not None else {}),
            },
        }

        print("[benign] extracting chain trace...", file=sys.stderr)
        trace = extract_chain_trace(
            w3, end_block,
            known_contracts={"usdt": usdt, "pool": pool},
        )
        print(f"[benign] {len(trace)} txs traced", file=sys.stderr)

        # --- write artifacts ---
        meta = {
            "run_name": run_name,
            "kind": "benign",
            "seed": seed,
            "num_users": args.num_users,
            "num_txs": args.num_txs,
            "with_pool": args.with_pool,
            "timestamp_utc": timestamp,
            "wall_clock_seconds": wall_clock,
            "anvil_chain_id": w3.eth.chain_id,
            "deploy_end_block": deploy_end_block,
            "end_block": end_block,
            "activity_counts": activity_counts,
            "args": vars(args),
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
        (out_dir / "addresses.json").write_text(json.dumps(addresses, indent=2))

        with (out_dir / "chain_trace.jsonl").open("w") as f:
            for record in trace:
                f.write(json.dumps(record, default=str) + "\n")

        summary_lines = [
            f"Run:        {run_name}",
            f"Kind:       benign (negative class for detector training)",
            f"Users:      {len(users)} (started {args.num_users})",
            f"Activities: {args.num_txs} requested",
            "",
            "Activity breakdown:",
        ]
        for kind, count in sorted(activity_counts.items()):
            summary_lines.append(f"  {kind:20s} {count}")
        summary_lines += [
            "",
            f"Chain txs traced: {len(trace)}",
            f"With pool:        {args.with_pool}",
            f"Wall clock:       {wall_clock:.1f}s",
            "",
            f"Artifacts: {out_dir}",
        ]
        summary_text = "\n".join(summary_lines)
        (out_dir / "summary.txt").write_text(summary_text + "\n")
        print("\n" + "-" * 60, file=sys.stderr)
        print(summary_text, file=sys.stderr)

        return out_dir


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="aml.detectors.run_benign",
        description=(
            "Generate a synthetic benign on-chain trace (no laundering) "
            "in the same artifact format as aml.attackers.run_campaign, "
            "labelled for use as the negative class in detector training."
        ),
    )
    ap.add_argument("--num-users", type=int, default=20,
                    help="Number of organic users to bootstrap (default: 20).")
    ap.add_argument("--num-txs", type=int, default=100,
                    help="Number of random activities to execute (default: 100).")
    ap.add_argument("--seed", type=int, default=None,
                    help="RNG seed for reproducibility. Default: random.")
    ap.add_argument("--out", type=str, default="runs",
                    help="Output directory; each run gets a timestamped subdirectory (default: runs/).")
    ap.add_argument("--with-pool", action="store_true", default=True,
                    help="Deploy + bootstrap the Uniswap pool so users can swap (default: on).")
    ap.add_argument("--no-pool", dest="with_pool", action="store_false",
                    help="Skip the pool deploy; swap activities become no-ops.")
    return ap


def _load_env_if_available() -> None:
    """Load .env into os.environ via python-dotenv if installed.

    The benign generator doesn't currently need an API key, but loading
    .env eagerly matches what aml.attackers.run_campaign does — keeps
    CLI ergonomics consistent and future-proofs against new env-driven
    config. Silent no-op when python-dotenv isn't installed.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.num_users < 2:
        print("error: --num-users must be at least 2", file=sys.stderr)
        return 2
    if args.num_txs < 1:
        print("error: --num-txs must be at least 1", file=sys.stderr)
        return 2
    _load_env_if_available()
    try:
        run_benign_main(args)
    except Exception as e:
        print(f"[benign] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
