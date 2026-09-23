"""Integration tests: ZK mixer tools (mixer_deposit / mixer_withdraw) driven
through ToolDispatcher against the real Groth16 MockTornado on Anvil.

These exercise PR 5.6 — the agent-facing wrapper around the ZK Tornado mixer
built in week 4. mixer_deposit generates a secret note and commits it
on-chain; mixer_withdraw rebuilds the Merkle path from on-chain Deposit
events, generates a Groth16 proof via snarkjs, and submits the withdraw tx.

No LLM API calls — pure on-chain + ZK toolchain, costs $0. Slow: each
withdraw runs snarkjs witness-calc + proof generation (~2-5s wallclock).

The deploy stack (MiMC + Verifier + MockTornado) mirrors test_anvil_tornado.py.
"""
from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3

from aml.attackers import ToolDispatcher
from aml.chains import AnvilNode
from aml.chains.mimc import deploy_mimc

REPO_ROOT = Path(__file__).resolve().parents[1]
CIRCUIT = "withdraw"
BUILD = REPO_ROOT / "circuits" / "build" / CIRCUIT
WASM = BUILD / f"{CIRCUIT}_js" / f"{CIRCUIT}.wasm"
ZKEY = BUILD / f"{CIRCUIT}_final.zkey"
VKEY = BUILD / "verification_key.json"
NODE_MODULES_CIRCOMLIBJS = REPO_ROOT / "node_modules" / "circomlibjs"
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"
VERIFIER_ARTIFACT = REPO_ROOT / "out" / "Verifier.sol" / "Groth16Verifier.json"

MERKLE_DEPTH = 10
DENOMINATION_WEI = 10**18


needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH",
)
needs_zk_setup = pytest.mark.skipif(
    not (WASM.exists() and ZKEY.exists() and VKEY.exists() and VERIFIER_ARTIFACT.exists()),
    reason=f"ZK setup missing; run bash scripts/setup_zk.sh {CIRCUIT} && forge build",
)
needs_circomlibjs = pytest.mark.skipif(
    not NODE_MODULES_CIRCOMLIBJS.exists() or shutil.which("node") is None,
    reason="circomlibjs not installed or node missing",
)
needs_snarkjs = pytest.mark.skipif(
    shutil.which("snarkjs") is None,
    reason="snarkjs not on PATH",
)


def _raw_tx(signed):
    return getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")


def _send(w3, fn, sender, key, gas=4_000_000, value=0):
    tx = fn.build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
        "value": value,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    return w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(_raw_tx(signed)))


def _ensure_compiled():
    if TORNADO_ARTIFACT.exists() and VERIFIER_ARTIFACT.exists():
        return
    subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)


def _load_artifact(path: Path):
    _ensure_compiled()
    with path.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


def _deploy_tornado(w3, deployer, deployer_key):
    """Deploy MiMC + Verifier + MockTornado; return the tornado contract handle."""
    mimc = deploy_mimc(w3, deployer, deployer_key)

    verifier_abi, verifier_bytecode = _load_artifact(VERIFIER_ARTIFACT)
    v_factory = w3.eth.contract(abi=verifier_abi, bytecode=verifier_bytecode)
    verifier_addr = _send(
        w3, v_factory.constructor(), deployer, deployer_key,
    ).contractAddress

    tornado_abi, tornado_bytecode = _load_artifact(TORNADO_ARTIFACT)
    t_factory = w3.eth.contract(abi=tornado_abi, bytecode=tornado_bytecode)
    # MockTornado's constructor now takes a fixed per-pool denomination as
    # its fourth argument (see contracts/MockTornado.sol; each denomination
    # is a separate pool instance, mirroring real Tornado Cash).
    tornado_addr = _send(
        w3, t_factory.constructor(
            verifier_addr, mimc.address, MERKLE_DEPTH, DENOMINATION_WEI,
        ),
        deployer, deployer_key, gas=10_000_000,
    ).contractAddress
    return w3.eth.contract(address=tornado_addr, abi=tornado_abi)


# --- Tests ---------------------------------------------------------------


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_mixer_deposit_returns_note_and_records_commitment():
    """A deposit returns a well-formed note and the commitment lands on-chain."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        tornado = _deploy_tornado(w3, deployer, deployer_key)

        # Deployer stays first (chain infra, rejected as a sender); alice is
        # the attacker wallet that actually deposits.
        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={deployer: deployer_key, alice: alice_key},
            tornado_contract=tornado,
        )
        result = dispatcher.dispatch("mixer_deposit", {"from_address": alice})
        assert not result.is_error, result.error
        assert result.output["deposit_note"].startswith("aml-mixer-note-v1:")
        assert result.output["leaf_index"] == 0
        assert result.output["amount_eth"] == 1.0

        commitment = bytes.fromhex(result.output["commitment"][2:])
        assert tornado.functions.commitments(commitment).call() is True
        assert tornado.functions.poolBalance().call() == DENOMINATION_WEI


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_mixer_deposit_insufficient_eth_returns_error():
    """A wallet with 0 ETH can't deposit — clean error, no chain call crash."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        tornado = _deploy_tornado(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None, wallets={}, tornado_contract=tornado,
        )
        # Fresh burner — 0 ETH
        burner = dispatcher.dispatch("generate_burner_wallet", {}).output["address"]
        result = dispatcher.dispatch("mixer_deposit", {"from_address": burner})
        assert result.is_error
        assert "insufficient eth" in result.error.lower()


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
@needs_snarkjs
def test_mixer_deposit_withdraw_lifecycle():
    """The headline: alice deposits, 1 ETH is withdrawn to a fresh recipient
    via a Groth16 proof, with bob paying the withdraw gas. Three distinct
    addresses — the ZK proof binds the recipient so bob can't redirect it."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        bob, bob_key = node.accounts[2], node.private_keys[2]
        tornado = _deploy_tornado(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={alice: alice_key, bob: bob_key},
            tornado_contract=tornado,
        )

        deposit = dispatcher.dispatch("mixer_deposit", {"from_address": alice})
        assert not deposit.is_error, deposit.error
        note = deposit.output["deposit_note"]

        # Fresh recipient — not in the registry, starts at 0 ETH
        recipient = w3.eth.account.create().address
        assert w3.eth.get_balance(recipient) == 0

        withdraw = dispatcher.dispatch("mixer_withdraw", {
            "deposit_note": note,
            "recipient": recipient,
            "gas_payer": bob,
        })
        assert not withdraw.is_error, withdraw.error
        assert withdraw.output["recipient"] == recipient
        assert withdraw.output["gas_payer"] == bob
        assert withdraw.output["anonymity_set_size"] == 1
        # Recipient got exactly 1 ETH and paid no gas (bob did)
        assert w3.eth.get_balance(recipient) == DENOMINATION_WEI
        assert tornado.functions.poolBalance().call() == 0


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
@needs_snarkjs
def test_mixer_withdraw_from_multi_deposit_anonymity_set():
    """Withdraw one specific note when several deposits share the tree — the
    tool must rebuild the correct Merkle path from on-chain Deposit events,
    not assume a single-commitment tree."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        tornado = _deploy_tornado(w3, deployer, deployer_key)

        # Five funded depositors
        wallets = {node.accounts[i]: node.private_keys[i] for i in range(1, 6)}
        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None, wallets=wallets, tornado_contract=tornado,
        )
        addrs = list(wallets)

        notes = []
        for addr in addrs:
            d = dispatcher.dispatch("mixer_deposit", {"from_address": addr})
            assert not d.is_error, d.error
            notes.append(d.output["deposit_note"])

        # Withdraw the THIRD deposit (leaf index 2) to a fresh recipient
        recipient = w3.eth.account.create().address
        withdraw = dispatcher.dispatch("mixer_withdraw", {
            "deposit_note": notes[2],
            "recipient": recipient,
            "gas_payer": addrs[0],
        })
        assert not withdraw.is_error, withdraw.error
        assert withdraw.output["anonymity_set_size"] == 5
        assert w3.eth.get_balance(recipient) == DENOMINATION_WEI
        # Four deposits' worth of ETH still locked in the mixer
        assert tornado.functions.poolBalance().call() == 4 * DENOMINATION_WEI


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
@needs_snarkjs
def test_mixer_withdraw_double_spend_returns_error():
    """The same note can't be withdrawn twice — the nullifier is spent."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        tornado = _deploy_tornado(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None, wallets={alice: alice_key},
            tornado_contract=tornado,
        )
        note = dispatcher.dispatch(
            "mixer_deposit", {"from_address": alice},
        ).output["deposit_note"]
        recipient = w3.eth.account.create().address

        first = dispatcher.dispatch("mixer_withdraw", {
            "deposit_note": note, "recipient": recipient,
        })
        assert not first.is_error, first.error

        second = dispatcher.dispatch("mixer_withdraw", {
            "deposit_note": note, "recipient": recipient,
        })
        assert second.is_error
        assert "already been withdrawn" in second.error.lower()


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_mixer_withdraw_unknown_note_returns_error():
    """A well-formed note that was never deposited → clean error, no crash."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        tornado = _deploy_tornado(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={deployer: deployer_key, alice: alice_key},
            tornado_contract=tornado,
        )
        # Valid format, random components, never deposited
        nullifier = int.from_bytes(secrets.token_bytes(31), "big")
        secret = int.from_bytes(secrets.token_bytes(31), "big")
        note = f"aml-mixer-note-v1:{nullifier:064x}:{secret:064x}"

        result = dispatcher.dispatch("mixer_withdraw", {
            "deposit_note": note,
            "recipient": node.accounts[3],
        })
        assert result.is_error
        assert "never deposited" in result.error.lower() \
            or "not found" in result.error.lower()


def test_mixer_withdraw_malformed_note_returns_error():
    """A malformed note string → clean error before any chain interaction.

    No Anvil needed: a non-None sentinel gets us past the contract check,
    and note decoding fails before any chain call is made.
    """
    dispatcher = ToolDispatcher(
        w3=Web3(), usdt_contract=None, wallets={},
        tornado_contract=object(),   # non-None sentinel
    )
    bad_notes = [
        "not-a-note",
        "aml-mixer-note-v1:xyz:abc",       # non-hex components
        "aml-mixer-note-v1:deadbeef",      # too few parts
        "wrong-prefix:" + "0" * 64 + ":" + "0" * 64,
    ]
    for bad in bad_notes:
        result = dispatcher.dispatch("mixer_withdraw", {
            "deposit_note": bad,
            "recipient": "0x" + "0" * 40,
        })
        assert result.is_error, f"{bad!r} should have errored"
        assert "note" in result.error.lower(), f"{bad!r}: {result.error}"


# --- Batched mixer tools (tutor-approved batched pattern) ----------------


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_mixer_batch_deposit_creates_n_notes():
    """Batch of 3 deposits: 3 distinct notes, 3 sequential leaf indices."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        tornado = _deploy_tornado(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={deployer: deployer_key, alice: alice_key},
            tornado_contract=tornado,
        )
        result = dispatcher.dispatch("mixer_batch_deposit", {
            "from_address": alice, "num_deposits": 3,
        })
        assert not result.is_error, result.error
        assert result.output["num_requested"] == 3
        assert result.output["num_successful"] == 3
        notes = result.output["deposit_notes"]
        assert len(notes) == 3
        # All notes must be distinct (fresh nullifier+secret per deposit)
        assert len(set(notes)) == 3
        for n in notes:
            assert n.startswith("aml-mixer-note-v1:")
        assert result.output["leaf_indices"] == [0, 1, 2]
        assert result.output["total_eth_deposited"] == 3.0
        assert result.output["failures"] == []
        # Pool now holds 3 ETH
        assert tornado.functions.poolBalance().call() == 3 * DENOMINATION_WEI


def test_mixer_batch_deposit_exceeds_cap_returns_error():
    """num_deposits > _MAX_MIXER_BATCH → clean cap error before any chain call."""
    from aml.attackers.tools import _MAX_MIXER_BATCH
    # First wallet is the deployer by convention and is rejected as a sender
    # before any argument validation, so use a second (attacker) wallet.
    dispatcher = ToolDispatcher(
        w3=Web3(), usdt_contract=None,
        wallets={"0x" + "9" * 40: "0x" + "0" * 64, "0x" + "1" * 40: "0x" + "0" * 64},
        tornado_contract=object(),
    )
    result = dispatcher.dispatch("mixer_batch_deposit", {
        "from_address": "0x" + "1" * 40,
        "num_deposits": _MAX_MIXER_BATCH + 1,
    })
    assert result.is_error
    assert "exceeds cap" in result.error.lower()


def test_mixer_batch_deposit_no_tornado_returns_error():
    """Dispatcher without tornado → clean error, no crash."""
    dispatcher = ToolDispatcher(
        w3=Web3(), usdt_contract=None, wallets={}, tornado_contract=None,
    )
    result = dispatcher.dispatch("mixer_batch_deposit", {
        "from_address": "0x" + "1" * 40, "num_deposits": 5,
    })
    assert result.is_error
    assert "tornado" in result.error.lower()


def test_mixer_batch_deposit_zero_or_negative_returns_error():
    """num_deposits must be positive."""
    # First wallet is the deployer by convention (rejected as sender), so the
    # attacker wallet must be a second entry.
    dispatcher = ToolDispatcher(
        w3=Web3(), usdt_contract=None,
        wallets={"0x" + "9" * 40: "0x" + "0" * 64, "0x" + "1" * 40: "0x" + "0" * 64},
        tornado_contract=object(),
    )
    for bad_n in (0, -1, -100):
        result = dispatcher.dispatch("mixer_batch_deposit", {
            "from_address": "0x" + "1" * 40, "num_deposits": bad_n,
        })
        assert result.is_error, f"num_deposits={bad_n} should error"
        assert "positive" in result.error.lower()


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
@needs_snarkjs
def test_mixer_batch_withdraw_broadcast_recipient():
    """3 notes all withdrawn to the SAME recipient — recipient collects 3 ETH."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        bob, bob_key = node.accounts[2], node.private_keys[2]
        tornado = _deploy_tornado(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={alice: alice_key, bob: bob_key},
            tornado_contract=tornado,
        )

        # Batch-deposit 3 notes
        dep = dispatcher.dispatch("mixer_batch_deposit", {
            "from_address": alice, "num_deposits": 3,
        })
        assert not dep.is_error, dep.error
        notes = dep.output["deposit_notes"]

        # Fresh consolidation address (not in registry, starts at 0 ETH)
        consol = w3.eth.account.create().address
        assert w3.eth.get_balance(consol) == 0

        # Broadcast: single string recipient → all 3 withdraws go to consol
        wd = dispatcher.dispatch("mixer_batch_withdraw", {
            "deposit_notes": notes,
            "recipients": consol,
            "gas_payer": bob,
        })
        assert not wd.is_error, wd.error
        assert wd.output["num_successful"] == 3
        assert wd.output["num_failed"] == 0
        assert wd.output["total_eth_withdrawn"] == 3.0
        # Recipient consolidated all 3 ETH
        assert w3.eth.get_balance(consol) == 3 * DENOMINATION_WEI
        # Mixer drained
        assert tornado.functions.poolBalance().call() == 0


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
@needs_snarkjs
def test_mixer_batch_withdraw_per_note_recipients():
    """3 notes → 3 distinct recipients (unlinkability preserving pattern)."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        bob, bob_key = node.accounts[2], node.private_keys[2]
        tornado = _deploy_tornado(w3, deployer, deployer_key)

        dispatcher = ToolDispatcher(
            w3=w3, usdt_contract=None,
            wallets={alice: alice_key, bob: bob_key},
            tornado_contract=tornado,
        )

        dep = dispatcher.dispatch("mixer_batch_deposit", {
            "from_address": alice, "num_deposits": 3,
        })
        notes = dep.output["deposit_notes"]

        # 3 distinct fresh addresses
        recipients = [w3.eth.account.create().address for _ in range(3)]
        for r in recipients:
            assert w3.eth.get_balance(r) == 0

        wd = dispatcher.dispatch("mixer_batch_withdraw", {
            "deposit_notes": notes,
            "recipients": recipients,
            "gas_payer": bob,
        })
        assert not wd.is_error, wd.error
        assert wd.output["num_successful"] == 3
        # Each recipient got exactly 1 ETH
        for r in recipients:
            assert w3.eth.get_balance(r) == DENOMINATION_WEI


def test_mixer_batch_withdraw_recipients_length_mismatch_returns_error():
    """len(recipients_list) != len(deposit_notes) → clean error."""
    dispatcher = ToolDispatcher(
        w3=Web3(), usdt_contract=None, wallets={},
        tornado_contract=object(),
    )
    result = dispatcher.dispatch("mixer_batch_withdraw", {
        "deposit_notes": ["note1", "note2", "note3"],
        "recipients": ["0x" + "1" * 40, "0x" + "2" * 40],   # only 2
    })
    assert result.is_error
    assert "must equal" in result.error.lower()


def test_mixer_batch_withdraw_empty_list_returns_error():
    """Empty deposit_notes → clean error."""
    dispatcher = ToolDispatcher(
        w3=Web3(), usdt_contract=None, wallets={},
        tornado_contract=object(),
    )
    result = dispatcher.dispatch("mixer_batch_withdraw", {
        "deposit_notes": [],
        "recipients": "0x" + "1" * 40,
    })
    assert result.is_error
    assert "non-empty" in result.error.lower()


def test_mixer_batch_withdraw_exceeds_cap_returns_error():
    """len(deposit_notes) > _MAX_MIXER_BATCH → clean cap error."""
    from aml.attackers.tools import _MAX_MIXER_BATCH
    dispatcher = ToolDispatcher(
        w3=Web3(), usdt_contract=None, wallets={},
        tornado_contract=object(),
    )
    too_many = ["note"] * (_MAX_MIXER_BATCH + 1)
    result = dispatcher.dispatch("mixer_batch_withdraw", {
        "deposit_notes": too_many,
        "recipients": "0x" + "1" * 40,
    })
    assert result.is_error
    assert "exceeds cap" in result.error.lower()


def test_mixer_batch_withdraw_no_tornado_returns_error():
    """Dispatcher without tornado → clean error."""
    dispatcher = ToolDispatcher(
        w3=Web3(), usdt_contract=None, wallets={}, tornado_contract=None,
    )
    result = dispatcher.dispatch("mixer_batch_withdraw", {
        "deposit_notes": ["note"], "recipients": "0x" + "1" * 40,
    })
    assert result.is_error
    assert "tornado" in result.error.lower()
