"""Verify the anti-stranding infrastructure.

Three invariants that MUST hold after every campaign:

  1. bootstrap_funder_pool creates K funder wallets, each seeded with
     the requested ETH (proves the pool exists and got its bootstrap
     capital from the deployer).

  2. _ensure_gas_dust tops up a drained wallet from the pool — verifies
     that mid-campaign gas exhaustion self-heals.

  3. After a synthetic mini-flow (create burners, transfer USDT through
     them, drain some intentionally), NO wallet ends up stranded (i.e.
     no wallet holds USDT > 0 while ETH < gas_cost_for_a_usdt_transfer).
     Invariant #3 is the one that broke on 2026-08-13 and prompted the
     whole rescue-in-sweep + funder-pool refactor; a regression here
     means real campaigns will strand USDT again.

Also exports check_no_stranded() as a reusable helper — the runners
call it post-campaign and fail the run loudly if the invariant breaks.

Requires Foundry (anvil + forge on PATH). Tests skip if either
missing so CI without Foundry doesn't fail spuriously.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from eth_account import Account
from web3 import Web3

from aml.attackers import ToolDispatcher
from aml.chains import AnvilNode

REPO_ROOT = Path(__file__).resolve().parents[1]
USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"

needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH; run `foundryup`",
)


def _raw_tx(signed) -> bytes:
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


def _deploy_usdt(w3: Web3, deployer: str, deployer_key: str):
    """Compile if needed, then deploy MockUSDT and return the contract."""
    if not USDT_ARTIFACT.exists():
        subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)
    artifact = json.loads(USDT_ARTIFACT.read_text())
    abi = artifact["abi"]
    bytecode = artifact["bytecode"]["object"]
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = Contract.constructor().build_transaction({
        "from": deployer,
        "nonce": w3.eth.get_transaction_count(deployer),
        "gas": 3_000_000,
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=deployer_key)
    tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    return w3.eth.contract(address=receipt.contractAddress, abi=abi)


def check_no_stranded(
    dispatcher: ToolDispatcher,
    usdt_gas_wei: int | None = None,
) -> list[dict]:
    """Return a list of wallets that hold USDT but insufficient ETH gas.

    Empty list = invariant holds. Non-empty list = each entry has
    {address, usdt, eth, gas_needed} — the runner should log this and
    (optionally) fail loudly.
    """
    w3 = dispatcher.w3
    if usdt_gas_wei is None:
        # Standard ERC20 transfer ~65k gas at current gas price × 1.2 buffer.
        usdt_gas_wei = 65_000 * int(w3.eth.gas_price * 1.2)
    stranded = []
    for addr in dispatcher.wallets:
        eth = w3.eth.get_balance(addr)
        if eth >= usdt_gas_wei:
            continue
        try:
            usdt = dispatcher.usdt.functions.balanceOf(addr).call()
        except Exception:   # noqa: BLE001
            usdt = 0
        if usdt > 0:
            stranded.append({
                "address": addr, "usdt": usdt / 1e6,
                "eth": eth / 1e18, "gas_needed": usdt_gas_wei / 1e18,
            })
    return stranded


# --- tests ------------------------------------------------------------------


@needs_foundry
def test_bootstrap_funder_pool_creates_k_funders_all_seeded():
    """Pool of K funders exists and each holds ~funder_eth ETH."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={deployer: deployer_key},
        )
        assert dispatcher._funder_pool == []

        dispatcher.bootstrap_funder_pool(num_funders=5, eth_per_funder=0.3)

        assert len(dispatcher._funder_pool) == 5
        for funder in dispatcher._funder_pool:
            assert funder in dispatcher.wallets, "funder not in wallet registry"
            balance_eth = w3.eth.get_balance(funder) / 1e18
            assert 0.29 < balance_eth <= 0.31, (
                f"funder {funder} has {balance_eth} ETH, expected ~0.3"
            )

        # Idempotent — second call is a no-op, pool size unchanged.
        dispatcher.bootstrap_funder_pool(num_funders=5, eth_per_funder=0.3)
        assert len(dispatcher._funder_pool) == 5


@needs_foundry
def test_ensure_gas_dust_restores_drained_wallet():
    """A wallet with 0 ETH gets topped up when _ensure_gas_dust runs."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={deployer: deployer_key},
        )
        dispatcher.bootstrap_funder_pool(num_funders=3, eth_per_funder=1.0)

        # Fresh burner with zero ETH — never seeded.
        acct = Account.create()
        dispatcher.wallets[acct.address] = acct.key.hex()
        assert w3.eth.get_balance(acct.address) == 0

        dispatcher._ensure_gas_dust(acct.address, min_eth=0.05)

        eth_after = w3.eth.get_balance(acct.address) / 1e18
        assert eth_after >= 0.049, f"expected >= 0.05 ETH, got {eth_after}"


@needs_foundry
def test_check_no_stranded_after_synthetic_flow():
    """End-to-end: campaign creates burners, moves USDT, drains ETH — none stranded."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt,
            wallets={deployer: deployer_key},
        )
        dispatcher.bootstrap_funder_pool(num_funders=5, eth_per_funder=1.0)

        # MockUSDT.mint(to, value) is public. Give the deployer 100k USDT
        # so the synthetic hops have something to move.
        mint_amount = 100_000 * 10**6
        mint_tx = usdt.functions.mint(deployer, mint_amount).build_transaction({
            "from": deployer,
            "nonce": w3.eth.get_transaction_count(deployer),
            "gas": 150_000,
            "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(mint_tx, private_key=deployer_key)
        tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
        w3.eth.wait_for_transaction_receipt(tx_hash)
        source_usdt = usdt.functions.balanceOf(deployer).call()
        assert source_usdt == mint_amount, "mint didn't land"

        # Generate 4 burners via the dispatcher (this exercises the
        # register_clean_exit / generate_burner_wallet code path that seeds
        # from the funder pool).
        burners = []
        for i in range(4):
            r = dispatcher._generate_burner_wallet()
            assert r.output and "address" in r.output, f"burner {i} failed"
            burners.append(r.output["address"])

        # Move USDT: deployer -> burner_0 -> burner_1 -> burner_2 -> burner_3.
        # Each hop drains some gas on the sender.
        amount_usdt = 800.0
        r = dispatcher._transfer_usdt(deployer, burners[0], amount_usdt)
        assert r.error is None, f"transfer 0 failed: {r.error}"

        for i in range(3):
            r = dispatcher._transfer_usdt(burners[i], burners[i + 1], amount_usdt)
            assert r.error is None, f"hop {i} failed: {r.error}"

        # Invariant: nothing stranded.
        stranded = check_no_stranded(dispatcher)
        assert stranded == [], (
            f"anti-stranding broke — {len(stranded)} wallet(s) hold USDT "
            f"without enough ETH for a transfer: {stranded}"
        )


@needs_foundry
def test_rescue_stranded_wallets_recovers_intentionally_broken_state():
    """rescue_stranded_wallets() rescues a wallet we intentionally stranded.

    Simulates the failure mode we care about: burner has USDT but 0 ETH
    (initial seed failed OR wallet burnt through its dust). Runner calls
    rescue_stranded_wallets() → wallet is topped up → invariant restored.
    """
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt,
            wallets={deployer: deployer_key},
        )
        dispatcher.bootstrap_funder_pool(num_funders=3, eth_per_funder=0.5)

        # Give deployer some USDT.
        mint_tx = usdt.functions.mint(deployer, 5_000 * 10**6).build_transaction({
            "from": deployer,
            "nonce": w3.eth.get_transaction_count(deployer),
            "gas": 150_000,
            "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(mint_tx, private_key=deployer_key)
        tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
        w3.eth.wait_for_transaction_receipt(tx_hash)

        # Create a wallet BYPASSING the auto-seed (simulate: initial seed
        # failed silently, or wallet burnt through its dust in a long
        # relay chain). Register the key so the dispatcher knows about it.
        stranded_acct = Account.create()
        dispatcher.wallets[stranded_acct.address] = stranded_acct.key.hex()
        assert w3.eth.get_balance(stranded_acct.address) == 0

        # Transfer USDT directly from deployer (bypassing _transfer_usdt so
        # we don't trigger the recipient auto-topup). Use raw contract call.
        raw_transfer = usdt.functions.transfer(
            stranded_acct.address, 400 * 10**6,
        ).build_transaction({
            "from": deployer,
            "nonce": w3.eth.get_transaction_count(deployer),
            "gas": 150_000,
            "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(raw_transfer, private_key=deployer_key)
        tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
        w3.eth.wait_for_transaction_receipt(tx_hash)

        # Confirm we broke it: USDT > 0 and ETH = 0.
        assert usdt.functions.balanceOf(stranded_acct.address).call() == 400 * 10**6
        assert w3.eth.get_balance(stranded_acct.address) == 0

        # Now run the rescue.
        report = dispatcher.rescue_stranded_wallets()

        assert report["stranded_before"] == 1
        assert report["rescued"] == 1
        assert report["stranded_after"] == 0
        assert report["stranded_addresses"] == []
        # And the wallet is now above the gas floor.
        assert w3.eth.get_balance(stranded_acct.address) >= int(0.049 * 10**18)


@needs_foundry
def test_rescue_forwards_stranded_usdt_to_random_clean_exit():
    """After rescuing gas, stranded USDT is forwarded to clean_exits.

    Verifies the 'continue the cleaning path' behaviour: an intermediate
    burner left holding USDT after the campaign ends should have that
    USDT routed to a random registered clean_exit in sub-$999 chunks,
    so the laundering flow completes instead of freezing mid-hop.
    """
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt,
            wallets={deployer: deployer_key},
        )
        dispatcher.bootstrap_funder_pool(num_funders=3, eth_per_funder=1.0)

        # Register 3 clean_exits — these are the possible forwarding targets.
        exit_addrs = []
        for platform in ("Binance", "Coinbase", "Kraken"):
            r = dispatcher._register_clean_exit(exchange_platform=platform)
            assert r.output and "address" in r.output
            exit_addrs.append(r.output["address"])

        # Deployer needs USDT to seed the stranded wallet.
        mint_tx = usdt.functions.mint(deployer, 5_000 * 10**6).build_transaction({
            "from": deployer,
            "nonce": w3.eth.get_transaction_count(deployer),
            "gas": 150_000,
            "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(mint_tx, private_key=deployer_key)
        tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
        w3.eth.wait_for_transaction_receipt(tx_hash)

        # Create a stranded intermediate: register the key, transfer USDT
        # to it bypassing auto-topup. $2,400 USDT → forwarded in 3 chunks
        # of sub-$999 each.
        stranded_acct = Account.create()
        dispatcher.wallets[stranded_acct.address] = stranded_acct.key.hex()
        raw_transfer = usdt.functions.transfer(
            stranded_acct.address, 2_400 * 10**6,
        ).build_transaction({
            "from": deployer,
            "nonce": w3.eth.get_transaction_count(deployer),
            "gas": 150_000,
            "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(raw_transfer, private_key=deployer_key)
        tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
        w3.eth.wait_for_transaction_receipt(tx_hash)

        # Precondition: intermediate has USDT, no ETH, and is NOT a clean_exit.
        assert usdt.functions.balanceOf(stranded_acct.address).call() == 2_400 * 10**6
        assert w3.eth.get_balance(stranded_acct.address) == 0
        assert stranded_acct.address not in set(exit_addrs)

        report = dispatcher.rescue_stranded_wallets()

        # Rescue metric: identified + top-upped.
        assert report["stranded_before"] == 1
        assert report["rescued"] == 1
        assert report["stranded_after"] == 0

        # Forwarding metric: USDT went to exits in sub-$999 chunks.
        assert len(report["forwarded"]) == 1
        fw = report["forwarded"][0]
        assert fw["from"] == stranded_acct.address
        # $2,400 in chunks capped at $980 each → 3 chunks.
        assert fw["chunks"] == 3
        assert fw["forwarded_usdt"] == pytest.approx(2400.0, abs=0.01)
        assert all(dest in exit_addrs for dest in fw["destinations"])

        # The intermediate is now empty; the sum of exit balances >= $2,400
        # (existing exits may already have USDT; we assert the delta only).
        assert usdt.functions.balanceOf(stranded_acct.address).call() < 1_000_000
        total_at_exits = sum(
            usdt.functions.balanceOf(ex).call() for ex in exit_addrs
        )
        assert total_at_exits >= 2_400 * 10**6


@needs_foundry
def test_rescue_skips_dust_stranded_wallets():
    """A wallet with <$1 USDT stranded is NOT rescued (economically wasteful)."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        usdt = _deploy_usdt(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=usdt,
            wallets={deployer: deployer_key},
        )
        dispatcher.bootstrap_funder_pool(num_funders=3, eth_per_funder=0.5)

        mint_tx = usdt.functions.mint(deployer, 100 * 10**6).build_transaction({
            "from": deployer,
            "nonce": w3.eth.get_transaction_count(deployer),
            "gas": 150_000, "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(mint_tx, private_key=deployer_key)
        tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
        w3.eth.wait_for_transaction_receipt(tx_hash)

        # Create a wallet with $0.50 USDT (below the $1 threshold).
        dust_acct = Account.create()
        dispatcher.wallets[dust_acct.address] = dust_acct.key.hex()
        raw_transfer = usdt.functions.transfer(
            dust_acct.address, 500_000,   # 0.5 USDT
        ).build_transaction({
            "from": deployer,
            "nonce": w3.eth.get_transaction_count(deployer),
            "gas": 150_000, "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(raw_transfer, private_key=deployer_key)
        tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
        w3.eth.wait_for_transaction_receipt(tx_hash)

        report = dispatcher.rescue_stranded_wallets()

        # Below-threshold dust is invisible to the rescue pass.
        assert report["stranded_before"] == 0
        assert report["rescued"] == 0
        # And the wallet still has 0 ETH — we did not waste gas on it.
        assert w3.eth.get_balance(dust_acct.address) == 0
