"""Integration test: Anvil + MockUSDT.

Requires Foundry (anvil + forge) on PATH. Tests skip if either is missing,
so CI without Foundry doesn't fail spuriously.
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
ARTIFACT_PATH = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"

needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH; run `foundryup`",
)


def _raw_tx(signed) -> bytes:
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes attribute")
    return raw


def _send(w3, fn, sender_addr: str, sender_key: str, gas: int = 1_500_000):
    tx = fn.build_transaction({
        "from": sender_addr,
        "nonce": w3.eth.get_transaction_count(sender_addr),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=sender_key)
    tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
    return w3.eth.wait_for_transaction_receipt(tx_hash)


@pytest.fixture(scope="module")
def usdt_artifact() -> tuple[list, str]:
    if not ARTIFACT_PATH.exists():
        subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)
    with ARTIFACT_PATH.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


@needs_foundry
def test_anvil_boots_and_responds():
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        assert w3.is_connected()
        assert w3.eth.chain_id == node.chain_id


@needs_foundry
def test_default_accounts_are_funded():
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        for addr in node.accounts:
            assert w3.eth.get_balance(addr) > 0, f"{addr} not funded"


@needs_foundry
def test_anvil_picks_free_port_per_instance():
    # Two simultaneous nodes should get different ports.
    with AnvilNode() as a, AnvilNode() as b:
        assert a.port != b.port
        assert a.rpc_url != b.rpc_url


@needs_foundry
def test_deploy_mint_transfer_usdt(usdt_artifact):
    abi, bytecode = usdt_artifact
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        alice, bob = node.accounts[0], node.accounts[1]
        alice_key = node.private_keys[0]

        # Deploy
        factory = w3.eth.contract(abi=abi, bytecode=bytecode)
        receipt = _send(w3, factory.constructor(), alice, alice_key)
        usdt = w3.eth.contract(address=receipt.contractAddress, abi=abi)

        assert usdt.functions.name().call() == "Mock USDT"
        assert usdt.functions.symbol().call() == "USDT"
        assert usdt.functions.decimals().call() == 6
        assert usdt.functions.totalSupply().call() == 0

        # Mint 1000 USDT to alice (1000 * 10^6 base units)
        amount = 1_000 * 10**6
        _send(w3, usdt.functions.mint(alice, amount), alice, alice_key, gas=200_000)
        assert usdt.functions.balanceOf(alice).call() == amount
        assert usdt.functions.balanceOf(bob).call() == 0
        assert usdt.functions.totalSupply().call() == amount

        # Transfer 250 USDT alice -> bob
        transfer_amount = 250 * 10**6
        _send(w3, usdt.functions.transfer(bob, transfer_amount), alice, alice_key, gas=200_000)
        assert usdt.functions.balanceOf(alice).call() == amount - transfer_amount
        assert usdt.functions.balanceOf(bob).call() == transfer_amount
        # Total supply unchanged by transfer
        assert usdt.functions.totalSupply().call() == amount


@needs_foundry
def test_transfer_more_than_balance_reverts(usdt_artifact):
    abi, bytecode = usdt_artifact
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        alice, bob = node.accounts[0], node.accounts[1]
        alice_key = node.private_keys[0]

        factory = w3.eth.contract(abi=abi, bytecode=bytecode)
        receipt = _send(w3, factory.constructor(), alice, alice_key)
        usdt = w3.eth.contract(address=receipt.contractAddress, abi=abi)

        # Alice has zero USDT. An on-chain send raises only via the eth_call
        # simulation path; once a tx is broadcast with explicit gas, web3
        # doesn't auto-raise on revert — the receipt just has status=0.
        # Cover both: simulation should raise with the require message, AND
        # if we force the tx through, the receipt should report failure.
        with pytest.raises(ContractLogicError, match="insufficient balance"):
            usdt.functions.transfer(bob, 1).call({"from": alice})

        receipt = _send(w3, usdt.functions.transfer(bob, 1), alice, alice_key, gas=200_000)
        assert receipt.status == 0, "expected on-chain revert (status=0)"
        assert usdt.functions.balanceOf(bob).call() == 0
        assert usdt.functions.balanceOf(alice).call() == 0
