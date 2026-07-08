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
    # Distribution calibrated against empirical Ethereum data:
    # - Gini coefficient 0.90-0.95 (highly unequal) - Nature 2025
    # - 0.3% of addresses hold 95% of ETH supply
    # - Power-law / heavy-tailed transaction values
    # - Whales (10,000+ ETH holders) = ~0.01% of wallets
    # - Centralized exchanges account for 93.4% of trading volume
    #   (Messari 2025); ~10.5% of ETH supply sits on exchange wallets
    #   (Cryptoslate Dec 2024). Deposits and withdrawals to/from
    #   exchanges are among the most common on-chain transaction types.
    "transfer_usdt":     0.1599,   # Micro retail P2P ($10-500)
    "transfer_eth":      0.10,     # Micro retail ETH (0.01-1.0)
    "swap_eth_for_usdt": 0.08,     # Retail DEX swap
    "swap_usdt_for_eth": 0.08,     # Retail DEX swap
    "mint_usdt":         0.04,     # "user got paid off-chain"
    "new_user":          0.04,     # "another user joins the ecosystem"
    # STRUCTURED legitimate patterns — these produce the same graph
    # signatures attackers do (hub-and-spoke, chains, sub-CTR structuring)
    # but from legitimate business/DEX behaviour. Their presence in the
    # benign class removes the classifier's easy "any structure = attacker"
    # shortcut and makes the classification task realistic.
    "hub_broadcast":     0.06,     # payroll: one wallet pays many recipients
    "business_chain":    0.08,     # supplier chain: A pays B, B pays C, C pays D
    "sub999_invoice":    0.08,     # legitimate invoice batching under CTR cap
    # MEDIUM tier — 8% of activities are medium ETH transfers (0.5-10 ETH).
    # Models small businesses, DEX arbitrageurs, freelancer payments, and
    # working capital movements. Matches the empirical Ethereum long-tail
    # where the 80-95th percentile of tx amounts sits in this range.
    "medium_eth_transfer": 0.08,   # 0.5-10 ETH: small business / arbitrage
    # WHALE / institutional flows — very rare (0.01% of activities) so
    # only 1-5 whales appear across the whole 400-campaign benign corpus.
    # Matches the extreme long-tail of real Ethereum: whale movements
    # (10+ ETH institutional / OTC settlement) are rare but happen.
    "whale_transfer":    0.0001,   # $10k-$50k USDT or 10-50 ETH: rare whale
    # CENTRALIZED EXCHANGE interactions — CEX handle 93.4% of trading
    # volume globally (Messari 2025). Retail users routinely deposit
    # from personal wallets to exchange hot wallets and withdraw to
    # personal wallets. 20% of activities go through this pattern,
    # split evenly between deposits (user -> CEX hot wallet) and
    # withdrawals (CEX hot wallet -> user).
    "exchange_deposit":  0.10,     # user -> exchange hot wallet
    "exchange_withdrawal": 0.10,   # exchange hot wallet -> user
}

# Per-user starting endowment ranges, calibrated to reproduce empirical
# Ethereum wealth heterogeneity (Gini 0.90-0.95). Range spans:
#   - retail micro-holders (bottom-tier: 0.2-2 ETH, $100-2000 USDT)
#   - small-holder / medium (2-10 ETH, $2000-20000 USDT)
#   - large-holder (10-20 ETH, $20000-50000 USDT) — few users, enable
#     medium_eth_transfer and (with accumulation) whale_transfer
_INITIAL_ETH_MIN = 0.2
_INITIAL_ETH_MAX = 20.0
_INITIAL_USDT_MIN = 100.0
_INITIAL_USDT_MAX = 50_000.0

# Per-tx amount ranges, in human units. Kept modest so we don't exhaust
# users mid-run (the activity loop just skips a tx if the sampled
# sender doesn't hold enough).
_TRANSFER_USDT_MIN = 10.0
_TRANSFER_USDT_MAX = 500.0
_TRANSFER_ETH_MIN = 0.01
_TRANSFER_ETH_MAX = 1.0

# --- Centralized exchange (CEX) configuration -----------------------------
# The 7 exchange platforms that the attacker's Coordinator can register
# clean_exits under (see prompts.py). We use the same list so the benign
# side of the graph and the attacker side share a coherent universe of
# exchange platforms. See PR #55 for the partial-visibility motivation.
_EXCHANGE_PLATFORMS: tuple[str, ...] = (
    "Binance", "Coinbase", "Kraken", "OKX",
    "Kucoin", "Bitfinex", "Gate",
)
# Hot wallets per exchange. Real CEX (Binance, Coinbase) maintain 3-10
# active hot wallets per asset with periodic rebalancing from cold
# storage. 3 is a moderate baseline that keeps the graph tractable and
# still produces enough exchange-associated nodes for the multi-agent
# detector's platform-aware partition to have meaningful per-exchange
# subgraphs.
_HOT_WALLETS_PER_EXCHANGE = 3
# Initial funding of exchange hot wallets from the deployer. Real hot
# wallets hold significant liquidity to service withdrawals without
# constant cold-storage refills. These starting balances let each hot
# wallet service ~50-100 withdrawals per campaign before running out.
_HOT_WALLET_INIT_ETH_MIN = 20.0
_HOT_WALLET_INIT_ETH_MAX = 100.0
_HOT_WALLET_INIT_USDT_MIN = 50_000.0
_HOT_WALLET_INIT_USDT_MAX = 500_000.0
# Per-tx deposit/withdrawal amounts. Retail deposits to CEX are
# typically $50-$5000 (Chainalysis 2024); withdrawals from CEX to
# self-custody are usually similar in magnitude (people move working
# capital in and out of exchanges rather than settling in one big move).
_CEX_TX_USDT_MIN = 50.0
_CEX_TX_USDT_MAX = 5_000.0
_CEX_TX_ETH_MIN = 0.05
_CEX_TX_ETH_MAX = 3.0
# Medium tier: 10% of activities are medium-size ETH transfers modeling
# small businesses, DEX arbitrageurs, freelancer settlements, working
# capital moves. Matches the 80-95th percentile of empirical Ethereum
# tx amounts (Nature 2025 wealth distribution study).
_MEDIUM_ETH_MIN = 0.5
_MEDIUM_ETH_MAX = 10.0

# Whale ranges — real institutional / whale movements on Ethereum start
# at 10 ETH (Nature 2025 threshold for "whale wallet"). Capping at 50
# ETH keeps the simulation feasible without draining seed users.
_WHALE_USDT_MIN = 10_000.0
_WHALE_USDT_MAX = 50_000.0
_WHALE_ETH_MIN = 10.0
_WHALE_ETH_MAX = 50.0
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


# --- STRUCTURED legitimate patterns (added PR #50 to close the classifier
#     "any structure = attacker" loophole) --------------------------------


def _do_hub_broadcast(rng, w3, usdt, users, amount_range) -> bool:
    """One user pays K recipients from a single sender in one burst.

    Models payroll: a business account distributes small USDT amounts to
    a handful of employees/contractors. Produces the same fan-out topology
    an attacker Placement burner produces but from a legitimate source.
    """
    if len(users) < 4:
        return False
    hub = rng.choice(users)
    if hub["usdt"] < amount_range[1] * 3:
        return False
    k = rng.randint(3, min(6, len(users) - 1))
    recipients = rng.sample([u for u in users if u["address"] != hub["address"]], k)
    for r in recipients:
        amount = rng.uniform(amount_range[0], amount_range[1])
        base = int(amount * 10**6)
        if hub["usdt"] < amount:
            continue
        send_tx(
            w3, usdt.functions.transfer(r["address"], base),
            hub["address"], hub["key"],
        )
        hub["usdt"] -= amount
        r["usdt"] += amount
    return True


def _do_business_chain(rng, w3, usdt, users, amount_range) -> bool:
    """Sequential A -> B -> C -> D USDT chain, each step slightly smaller.

    Models supplier chains where a payment flows through resellers: the
    end customer pays the retailer, the retailer pays the wholesaler,
    the wholesaler pays the manufacturer. Produces the same peel-chain
    topology attackers use for Layering but from a legitimate source.
    """
    if len(users) < 4:
        return False
    chain = rng.sample(users, 4)
    src = chain[0]
    if src["usdt"] < amount_range[1]:
        return False
    amount = rng.uniform(amount_range[0], amount_range[1])
    for i in range(3):
        sender, receiver = chain[i], chain[i + 1]
        if sender["usdt"] < amount:
            return False
        base = int(amount * 10**6)
        send_tx(
            w3, usdt.functions.transfer(receiver["address"], base),
            sender["address"], sender["key"],
        )
        sender["usdt"] -= amount
        receiver["usdt"] += amount
        # Each subsequent hop keeps ~70-90% of the value (fees, margin)
        amount *= rng.uniform(0.7, 0.9)
    return True


def _do_sub999_invoice(rng, w3, usdt, users, cap: float = 999.0) -> bool:
    """One user sends 2-4 sub-CTR USDT amounts to distinct recipients.

    Models legitimate invoice batching: a business pays several suppliers
    with amounts each below internal-audit / regulatory thresholds ($999
    aligns with US CTR). Produces the same sub-cap structuring signature
    attackers use in Integration but from a legitimate source. This is
    the most-attacker-looking benign pattern.
    """
    if len(users) < 3:
        return False
    sender = rng.choice(users)
    if sender["usdt"] < 500:
        return False
    k = rng.randint(2, min(4, len(users) - 1))
    recipients = rng.sample(
        [u for u in users if u["address"] != sender["address"]], k,
    )
    for r in recipients:
        # Amounts in [10, 500] — well under the 999 cap, matches invoice ranges
        amount = rng.uniform(10.0, 500.0)
        if sender["usdt"] < amount:
            continue
        base = int(amount * 10**6)
        send_tx(
            w3, usdt.functions.transfer(r["address"], base),
            sender["address"], sender["key"],
        )
        sender["usdt"] -= amount
        r["usdt"] += amount
    return True


def _do_medium_eth_transfer(rng, w3, users) -> bool:
    """Medium-size ETH transfer (0.5-10 ETH) between two users.

    Models the 80-95th percentile of empirical Ethereum tx amounts:
    small businesses moving working capital, freelancer settlements,
    DEX arbitrageurs rebalancing, DAO grant payouts. Represents a real
    slice of legitimate on-chain activity that produces medium-value
    edges — larger than retail P2P but well below whale thresholds.

    Amount range 0.5-10 ETH matches the medium-holder tier identified
    in Nature 2025's Ethereum wealth distribution analysis.
    """
    if len(users) < 2:
        return False
    sender, recipient = rng.sample(users, 2)
    amount = rng.uniform(_MEDIUM_ETH_MIN, _MEDIUM_ETH_MAX)
    # Leave gas reserve
    if sender["eth"] < amount + _GAS_RESERVE_ETH:
        return False
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
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash)
    sender["eth"] -= amount
    recipient["eth"] += amount
    return True


def _do_whale_transfer(rng, w3, usdt, users) -> bool:
    """Rare large-value transfer: USDT $5k-$50k OR ETH 2-20.

    Models institutional / whale movements. On real Ethereum ~0.1% of
    transactions are in this range (exchange hot-wallet rebalancing, OTC
    settlements, corporate treasury moves, DAO fund transfers). Their
    presence in the benign class prevents the classifier from learning
    "any large amount = suspicious" — a shortcut that would fail in
    production where whales are routine.

    Randomly chooses USDT (60%) or ETH (40%). Skips if the sender does
    not hold enough of the chosen asset.
    """
    if len(users) < 2:
        return False
    sender, recipient = rng.sample(users, 2)
    # 60/40 split between USDT whales (more common on stable-heavy DeFi)
    # and ETH whales (institutional treasury moves)
    if rng.random() < 0.6:
        amount = rng.uniform(_WHALE_USDT_MIN, _WHALE_USDT_MAX)
        if sender["usdt"] < amount:
            return False
        base = int(amount * 10**6)
        send_tx(
            w3, usdt.functions.transfer(recipient["address"], base),
            sender["address"], sender["key"],
        )
        sender["usdt"] -= amount
        recipient["usdt"] += amount
    else:
        amount = rng.uniform(_WHALE_ETH_MIN, _WHALE_ETH_MAX)
        # Leave gas floor
        if sender["eth"] < amount + _GAS_RESERVE_ETH:
            return False
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
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash)
        sender["eth"] -= amount
        recipient["eth"] += amount
    return True


# --- Centralized exchange (CEX) interactions ------------------------------


def _bootstrap_exchange_hot_wallets(
    w3, usdt, deployer, deployer_key, rng,
) -> dict[str, list[dict]]:
    """Create N hot wallets per exchange platform and seed with liquidity.

    Returns a dict {platform: [wallet_dict, ...]} where each wallet_dict is
    the same shape as a user wallet ({address, key, eth, usdt}). These hot
    wallets model the real-world reality that every CEX (Binance, Coinbase,
    Kraken, etc.) maintains a small number of active hot wallets to service
    user deposits and withdrawals on-chain.

    Called once at the start of generate_benign. The wallets persist across
    all activities in the campaign.
    """
    hot_wallets: dict[str, list[dict]] = {}
    for platform in _EXCHANGE_PLATFORMS:
        hot_wallets[platform] = []
        for _ in range(_HOT_WALLETS_PER_EXCHANGE):
            acct = Account.create()
            eth_amount = rng.uniform(
                _HOT_WALLET_INIT_ETH_MIN, _HOT_WALLET_INIT_ETH_MAX,
            )
            usdt_amount = rng.uniform(
                _HOT_WALLET_INIT_USDT_MIN, _HOT_WALLET_INIT_USDT_MAX,
            )
            _seed_user_wallet(
                w3, deployer, deployer_key, usdt, acct.address,
                eth_amount, usdt_amount,
            )
            hot_wallets[platform].append({
                "address": acct.address,
                "key": acct.key.hex(),
                "eth": eth_amount,
                "usdt": usdt_amount,
                "platform": platform,
            })
    return hot_wallets


def _do_exchange_deposit(rng, w3, usdt, users, exchanges) -> bool:
    """A retail user deposits funds to a CEX hot wallet.

    Models the most common CEX interaction: a user sends USDT or ETH from
    their personal wallet to one of the exchange's hot wallets. Real
    Ethereum shows this happens continuously — CEX handle ~93% of trading
    volume so users are constantly moving funds in.
    """
    if not users or not exchanges:
        return False
    sender = rng.choice(users)
    platform = rng.choice(list(exchanges.keys()))
    hot_wallet = rng.choice(exchanges[platform])
    # Choose asset (60% USDT, 40% ETH — matches stablecoin dominance)
    if rng.random() < 0.6:
        amount = rng.uniform(_CEX_TX_USDT_MIN, _CEX_TX_USDT_MAX)
        if sender["usdt"] < amount:
            return False
        base = int(amount * 10**6)
        send_tx(
            w3, usdt.functions.transfer(hot_wallet["address"], base),
            sender["address"], sender["key"],
        )
        sender["usdt"] -= amount
        hot_wallet["usdt"] += amount
    else:
        amount = rng.uniform(_CEX_TX_ETH_MIN, _CEX_TX_ETH_MAX)
        if sender["eth"] < amount + _GAS_RESERVE_ETH:
            return False
        tx = {
            "from": sender["address"],
            "to": hot_wallet["address"],
            "value": int(amount * 10**18),
            "nonce": w3.eth.get_transaction_count(sender["address"]),
            "gas": _ETH_TRANSFER_GAS,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id,
        }
        signed = w3.eth.account.sign_transaction(tx, private_key=sender["key"])
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash)
        sender["eth"] -= amount
        hot_wallet["eth"] += amount
    return True


def _do_exchange_withdrawal(rng, w3, usdt, users, exchanges) -> bool:
    """A CEX hot wallet withdraws funds to a retail user.

    Models the counterpart of exchange_deposit: users request withdrawals
    from their exchange balance, and the exchange's hot wallet transfers
    the funds on-chain to the user's self-custody wallet.
    """
    if not users or not exchanges:
        return False
    recipient = rng.choice(users)
    platform = rng.choice(list(exchanges.keys()))
    hot_wallet = rng.choice(exchanges[platform])
    # Choose asset (60% USDT, 40% ETH)
    if rng.random() < 0.6:
        amount = rng.uniform(_CEX_TX_USDT_MIN, _CEX_TX_USDT_MAX)
        if hot_wallet["usdt"] < amount:
            return False
        base = int(amount * 10**6)
        send_tx(
            w3, usdt.functions.transfer(recipient["address"], base),
            hot_wallet["address"], hot_wallet["key"],
        )
        hot_wallet["usdt"] -= amount
        recipient["usdt"] += amount
    else:
        amount = rng.uniform(_CEX_TX_ETH_MIN, _CEX_TX_ETH_MAX)
        if hot_wallet["eth"] < amount + _GAS_RESERVE_ETH:
            return False
        tx = {
            "from": hot_wallet["address"],
            "to": recipient["address"],
            "value": int(amount * 10**18),
            "nonce": w3.eth.get_transaction_count(hot_wallet["address"]),
            "gas": _ETH_TRANSFER_GAS,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id,
        }
        signed = w3.eth.account.sign_transaction(
            tx, private_key=hot_wallet["key"],
        )
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash)
        hot_wallet["eth"] -= amount
        recipient["eth"] += amount
    return True


def generate_benign(
    w3, usdt, pool, deployer: str, deployer_key: str,
    *, num_users: int, num_txs: int, seed: int,
) -> tuple[list[dict], dict[str, list[dict]], dict[str, int]]:
    """Build N users + exchange hot wallets, run M activities.

    Returns (users, exchange_hot_wallets, activity_counts).
    exchange_hot_wallets is {platform: [wallet_dict, ...]}.
    """
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

    # Bootstrap exchange hot wallets (3 per platform × 7 platforms = 21).
    # These persist across all activities and service exchange_deposit
    # (user -> hot) and exchange_withdrawal (hot -> user) calls.
    exchange_hot_wallets = _bootstrap_exchange_hot_wallets(
        w3, usdt, deployer, deployer_key, rng,
    )

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
        elif kind == "hub_broadcast":
            ok = _do_hub_broadcast(
                rng, w3, usdt, users,
                (_TRANSFER_USDT_MIN, _TRANSFER_USDT_MAX),
            )
        elif kind == "business_chain":
            ok = _do_business_chain(
                rng, w3, usdt, users,
                (_TRANSFER_USDT_MIN, _TRANSFER_USDT_MAX),
            )
        elif kind == "sub999_invoice":
            ok = _do_sub999_invoice(rng, w3, usdt, users)
        elif kind == "medium_eth_transfer":
            ok = _do_medium_eth_transfer(rng, w3, users)
        elif kind == "whale_transfer":
            ok = _do_whale_transfer(rng, w3, usdt, users)
        elif kind == "exchange_deposit":
            ok = _do_exchange_deposit(
                rng, w3, usdt, users, exchange_hot_wallets,
            )
        elif kind == "exchange_withdrawal":
            ok = _do_exchange_withdrawal(
                rng, w3, usdt, users, exchange_hot_wallets,
            )
        else:
            ok = False
        if ok:
            activity_counts[kind] += 1
        else:
            activity_counts["skipped"] += 1

    return users, exchange_hot_wallets, activity_counts


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
        users, exchange_hot_wallets, activity_counts = generate_benign(
            w3, usdt, pool, deployer, deployer_key,
            num_users=args.num_users, num_txs=args.num_txs, seed=seed,
        )
        end_block = w3.eth.block_number
        wall_clock = time.time() - start_wall
        total_hot = sum(len(v) for v in exchange_hot_wallets.values())
        print(
            f"[benign] generated {len(users)} users + {total_hot} "
            f"exchange hot wallets in {wall_clock:.1f}s; end block {end_block}",
            file=sys.stderr,
        )

        # Build labelled address registry. All organic users are
        # `benign_user`. Deployer is the "operator/infrastructure" who
        # also runs the pool — labelled so the graph extractor can
        # exclude it from clustering or treat it as a known hub.
        # Exchange hot wallets are `benign_user` for binary classification
        # (they're legitimate CEX infrastructure) but carry a platform
        # metadata field so partial_visibility_split_by_platform can
        # assign them to their exchange's federated partition.
        hot_wallet_labels = {
            hw["address"]: "benign_user"
            for wallets in exchange_hot_wallets.values()
            for hw in wallets
        }
        addresses = {
            "benign_users": [u["address"] for u in users],
            "exchange_wallets": {
                platform: [hw["address"] for hw in wallets]
                for platform, wallets in exchange_hot_wallets.items()
            },
            "operator_wallet": deployer,
            "contracts": {
                "usdt": usdt.address,
                "pool": pool.address if pool is not None else None,
            },
            "labels": {
                **{u["address"]: "benign_user" for u in users},
                **hot_wallet_labels,
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
    """Load .env into os.environ using the shared stdlib parser.

    Delegates to aml.utils.env.load_dotenv_if_present so this CLI
    parses .env the same way pytest's conftest.py and run_campaign do.
    No dependency on python-dotenv. The benign generator doesn't
    currently need an API key, but loading .env eagerly matches the
    attacker CLI and future-proofs against new env-driven config.
    """
    from aml.utils.env import load_dotenv_if_present
    load_dotenv_if_present()


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
