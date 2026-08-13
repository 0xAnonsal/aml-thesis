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
