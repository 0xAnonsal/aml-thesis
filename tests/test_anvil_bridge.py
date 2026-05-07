"""Integration tests: MockBridge on Anvil.

Tests the lock + release lifecycle and the cross-chain laundering primitive:
launderer locks on ETH side -> operator releases mirror funds elsewhere
(simulated by lock here, release here in this single-chain test).
Requires Foundry on PATH; skips otherwise.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError

from aml.chains import AnvilNode

REPO_ROOT = Path(__file__).resolve().parents[1]
USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"
BRIDGE_ARTIFACT = REPO_ROOT / "out" / "MockBridge.sol" / "MockBridge.json"

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


def _build(path: Path) -> tuple[list, str]:
    if not path.exists():
        subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)
    with path.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


@pytest.fixture(scope="module")
def usdt_artifact():
    return _build(USDT_ARTIFACT)


@pytest.fixture(scope="module")
def bridge_artifact():
    return _build(BRIDGE_ARTIFACT)


def _setup(node, usdt_artifact, bridge_artifact):
    """Deploy USDT + Bridge. Operator (deployer) holds the only privilege."""
    w3 = Web3(Web3.HTTPProvider(node.rpc_url))
    deployer, deployer_key = node.accounts[0], node.private_keys[0]
    alice, alice_key = node.accounts[1], node.private_keys[1]

    usdt_factory = w3.eth.contract(abi=usdt_artifact[0], bytecode=usdt_artifact[1])
    usdt = w3.eth.contract(
        address=_send(w3, usdt_factory.constructor(), deployer, deployer_key).contractAddress,
        abi=usdt_artifact[0],
    )

    bridge_factory = w3.eth.contract(abi=bridge_artifact[0], bytecode=bridge_artifact[1])
    bridge = w3.eth.contract(
        address=_send(
            w3, bridge_factory.constructor(usdt.address), deployer, deployer_key
        ).contractAddress,
        abi=bridge_artifact[0],
    )
    return w3, deployer, deployer_key, alice, alice_key, usdt, bridge


# Tron-style destination address right-padded into bytes32 (research stand-in).
DEST = b"TR1ce9NK4DM7XFq9RzTronAddrPadding"[:32].ljust(32, b"\x00")


@needs_foundry
def test_lock_records_sequence_and_transfers_usdt(usdt_artifact, bridge_artifact):
    with AnvilNode() as node:
        w3, _, _, alice, alice_key, usdt, bridge = _setup(node, usdt_artifact, bridge_artifact)

        amount = 1000 * 10**6
        _send(w3, usdt.functions.mint(alice, amount), alice, alice_key, gas=200_000)
        _send(w3, usdt.functions.approve(bridge.address, amount), alice, alice_key, gas=200_000)
        _send(w3, bridge.functions.lockUSDT(amount, DEST), alice, alice_key)

        assert bridge.functions.lockSequence().call() == 1
        assert bridge.functions.lockedBalance().call() == amount
        assert usdt.functions.balanceOf(alice).call() == 0


@needs_foundry
def test_lock_zero_amount_reverts(usdt_artifact, bridge_artifact):
    with AnvilNode() as node:
        w3, _, _, alice, _, _, bridge = _setup(node, usdt_artifact, bridge_artifact)
        with pytest.raises(ContractLogicError, match="zero amount"):
            bridge.functions.lockUSDT(0, DEST).call({"from": alice})


@needs_foundry
def test_lock_zero_destination_reverts(usdt_artifact, bridge_artifact):
    with AnvilNode() as node:
        w3, _, _, alice, _, _, bridge = _setup(node, usdt_artifact, bridge_artifact)
        with pytest.raises(ContractLogicError, match="zero destination"):
            bridge.functions.lockUSDT(1, b"\x00" * 32).call({"from": alice})


@needs_foundry
def test_release_by_operator_pays_recipient(usdt_artifact, bridge_artifact):
    with AnvilNode() as node:
        w3, deployer, deployer_key, alice, alice_key, usdt, bridge = _setup(
            node, usdt_artifact, bridge_artifact
        )

        # Seed the bridge with USDT (simulating prior locks from foreign chain)
        seed = 5_000 * 10**6
        _send(w3, usdt.functions.mint(bridge.address, seed), deployer, deployer_key, gas=200_000)

        recipient = node.accounts[3]
        amount = 750 * 10**6
        foreign_seq = 42
        _send(
            w3,
            bridge.functions.releaseUSDT(foreign_seq, recipient, amount),
            deployer,
            deployer_key,
        )

        assert usdt.functions.balanceOf(recipient).call() == amount
        assert bridge.functions.foreignSequenceProcessed(foreign_seq).call() is True
        assert bridge.functions.lockedBalance().call() == seed - amount


@needs_foundry
def test_release_by_non_operator_reverts(usdt_artifact, bridge_artifact):
    with AnvilNode() as node:
        w3, deployer, deployer_key, alice, alice_key, usdt, bridge = _setup(
            node, usdt_artifact, bridge_artifact
        )

        seed = 1_000 * 10**6
        _send(w3, usdt.functions.mint(bridge.address, seed), deployer, deployer_key, gas=200_000)

        recipient = node.accounts[3]
        with pytest.raises(ContractLogicError, match="only operator"):
            bridge.functions.releaseUSDT(1, recipient, 100 * 10**6).call({"from": alice})


@needs_foundry
def test_release_replays_revert(usdt_artifact, bridge_artifact):
    with AnvilNode() as node:
        w3, deployer, deployer_key, _, _, usdt, bridge = _setup(
            node, usdt_artifact, bridge_artifact
        )

        seed = 2_000 * 10**6
        _send(w3, usdt.functions.mint(bridge.address, seed), deployer, deployer_key, gas=200_000)

        recipient = node.accounts[3]
        amount = 500 * 10**6
        foreign_seq = 99
        _send(
            w3,
            bridge.functions.releaseUSDT(foreign_seq, recipient, amount),
            deployer,
            deployer_key,
        )

        with pytest.raises(ContractLogicError, match="already processed"):
            bridge.functions.releaseUSDT(foreign_seq, recipient, amount).call({"from": deployer})


@needs_foundry
def test_round_trip_lock_then_release_to_different_address(usdt_artifact, bridge_artifact):
    """Headline cross-chain laundering primitive:
    alice locks USDT to a Tron destination; later, operator releases USDT
    on the same chain to a *different* address (charlie). The chain-level
    observation is: alice -> bridge in, bridge -> charlie out, with nothing
    on the source side directly linking alice to charlie.
    """
    with AnvilNode() as node:
        w3, deployer, deployer_key, alice, alice_key, usdt, bridge = _setup(
            node, usdt_artifact, bridge_artifact
        )

        amount = 2_500 * 10**6
        _send(w3, usdt.functions.mint(alice, amount), alice, alice_key, gas=200_000)
        _send(w3, usdt.functions.approve(bridge.address, amount), alice, alice_key, gas=200_000)
        _send(w3, bridge.functions.lockUSDT(amount, DEST), alice, alice_key)

        charlie = node.accounts[4]
        # Foreign chain processes the deposit, signs a release.
        # In real life there'd be a delay and a different tx ordering;
        # here the operator releases the mirror amount to charlie on this chain.
        _send(
            w3,
            bridge.functions.releaseUSDT(1, charlie, amount),
            deployer,
            deployer_key,
        )

        assert usdt.functions.balanceOf(charlie).call() == amount
        assert usdt.functions.balanceOf(alice).call() == 0
        # Bridge net balance returned to zero — locked then released.
        assert bridge.functions.lockedBalance().call() == 0
