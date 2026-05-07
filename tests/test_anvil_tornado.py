"""Integration tests: MockTornado on Anvil.

Tests the deposit/withdraw lifecycle and the laundering primitive: depositor
address != withdraw recipient. Requires Foundry on PATH; skips otherwise.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError

from aml.chains import AnvilNode

REPO_ROOT = Path(__file__).resolve().parents[1]
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"

DENOMINATION_WEI = 10**18  # 1 ETH

needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH; run `foundryup`",
)


def _raw_tx(signed):
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes attribute")
    return raw


def _send(w3, fn, sender, key, gas=2_000_000, value=0):
    tx = fn.build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
        "value": value,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    return w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(_raw_tx(signed)))


def _deploy_tornado(w3, abi, bytecode, deployer, key):
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    r = _send(w3, factory.constructor(), deployer, key)
    return w3.eth.contract(address=r.contractAddress, abi=abi)


def _make_note() -> tuple[bytes, bytes, bytes]:
    """Generate a (secret, nullifier, commitment) triple. Off-chain step."""
    secret = os.urandom(32)
    nullifier = os.urandom(32)
    commitment = Web3.keccak(secret + nullifier)
    return secret, nullifier, commitment


@pytest.fixture(scope="module")
def tornado_artifact() -> tuple[list, str]:
    if not TORNADO_ARTIFACT.exists():
        subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)
    with TORNADO_ARTIFACT.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


@needs_foundry
def test_deposit_records_commitment(tornado_artifact):
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        alice, alice_key = node.accounts[0], node.private_keys[0]
        tornado = _deploy_tornado(w3, *tornado_artifact, alice, alice_key)

        _, _, commitment = _make_note()
        _send(w3, tornado.functions.deposit(commitment), alice, alice_key, value=DENOMINATION_WEI)

        assert tornado.functions.commitments(commitment).call() is True
        assert tornado.functions.depositCount().call() == 1
        assert tornado.functions.poolBalance().call() == DENOMINATION_WEI


@needs_foundry
def test_wrong_denomination_reverts(tornado_artifact):
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        alice, alice_key = node.accounts[0], node.private_keys[0]
        tornado = _deploy_tornado(w3, *tornado_artifact, alice, alice_key)

        _, _, commitment = _make_note()
        with pytest.raises(ContractLogicError, match="wrong denomination"):
            tornado.functions.deposit(commitment).call({
                "from": alice,
                "value": DENOMINATION_WEI // 2,  # half ETH — wrong
            })


@needs_foundry
def test_duplicate_commitment_reverts(tornado_artifact):
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        alice, alice_key = node.accounts[0], node.private_keys[0]
        tornado = _deploy_tornado(w3, *tornado_artifact, alice, alice_key)

        _, _, commitment = _make_note()
        _send(w3, tornado.functions.deposit(commitment), alice, alice_key, value=DENOMINATION_WEI)
        with pytest.raises(ContractLogicError, match="duplicate commitment"):
            tornado.functions.deposit(commitment).call({
                "from": alice,
                "value": DENOMINATION_WEI,
            })


@needs_foundry
def test_laundering_lifecycle_alice_to_charlie(tornado_artifact):
    """The headline test: alice deposits, charlie (different account) withdraws.

    On-chain, the link is alice -> commitment -> nullifier -> charlie. Without
    a ZK proof the defender can hash (secret || nullifier) and recover the
    commitment, but the *primitive* of moving funds across addresses works.
    """
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        alice, alice_key = node.accounts[0], node.private_keys[0]
        bob, bob_key = node.accounts[1], node.private_keys[1]
        charlie = node.accounts[2]
        tornado = _deploy_tornado(w3, *tornado_artifact, alice, alice_key)

        secret, nullifier, commitment = _make_note()
        _send(w3, tornado.functions.deposit(commitment), alice, alice_key, value=DENOMINATION_WEI)

        charlie_before = w3.eth.get_balance(charlie)
        # bob (yet another account) submits the withdraw — recipient is charlie.
        # In real Tornado this would be a relayer; here the launderer just uses
        # a fresh wallet to post the withdraw tx so its gas isn't paid by alice.
        _send(w3, tornado.functions.withdraw(secret, nullifier, charlie), bob, bob_key)

        assert w3.eth.get_balance(charlie) == charlie_before + DENOMINATION_WEI
        assert tornado.functions.nullifierUsed(nullifier).call() is True
        assert tornado.functions.poolBalance().call() == 0


@needs_foundry
def test_unknown_commitment_reverts(tornado_artifact):
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        alice, alice_key = node.accounts[0], node.private_keys[0]
        charlie = node.accounts[2]
        tornado = _deploy_tornado(w3, *tornado_artifact, alice, alice_key)

        # Generate a note but never deposit it — withdraw should fail.
        secret, nullifier, _ = _make_note()
        with pytest.raises(ContractLogicError, match="unknown commitment"):
            tornado.functions.withdraw(secret, nullifier, charlie).call({"from": alice})


@needs_foundry
def test_double_withdraw_reverts(tornado_artifact):
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        alice, alice_key = node.accounts[0], node.private_keys[0]
        charlie = node.accounts[2]
        tornado = _deploy_tornado(w3, *tornado_artifact, alice, alice_key)

        secret, nullifier, commitment = _make_note()
        _send(w3, tornado.functions.deposit(commitment), alice, alice_key, value=DENOMINATION_WEI)
        _send(w3, tornado.functions.withdraw(secret, nullifier, charlie), alice, alice_key)

        with pytest.raises(ContractLogicError, match="nullifier already used"):
            tornado.functions.withdraw(secret, nullifier, charlie).call({"from": alice})


@needs_foundry
def test_anonymity_set_grows(tornado_artifact):
    """Each deposit increases the anonymity set size visible on-chain."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        tornado = _deploy_tornado(w3, *tornado_artifact, deployer, deployer_key)

        for i in range(4):
            sender = node.accounts[i]
            sender_key = node.private_keys[i]
            _, _, commitment = _make_note()
            _send(w3, tornado.functions.deposit(commitment), sender, sender_key,
                  value=DENOMINATION_WEI)
            assert tornado.functions.depositCount().call() == i + 1

        assert tornado.functions.poolBalance().call() == 4 * DENOMINATION_WEI


@needs_foundry
def test_direct_send_reverts(tornado_artifact):
    """Sending ETH directly (without going through deposit) must revert."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        alice, alice_key = node.accounts[0], node.private_keys[0]
        tornado = _deploy_tornado(w3, *tornado_artifact, alice, alice_key)

        tx = {
            "from": alice,
            "to": tornado.address,
            "value": DENOMINATION_WEI,
            "nonce": w3.eth.get_transaction_count(alice),
            "gas": 100_000,
            "gasPrice": w3.eth.gas_price,
        }
        signed = w3.eth.account.sign_transaction(tx, private_key=alice_key)
        receipt = w3.eth.wait_for_transaction_receipt(
            w3.eth.send_raw_transaction(_raw_tx(signed))
        )
        assert receipt.status == 0, "direct send should revert"
        assert tornado.functions.poolBalance().call() == 0
