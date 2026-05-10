"""Integration tests: real ZK MockTornado on Anvil.

The mixer now uses MiMC commitments + on-chain Merkle tree state + Groth16
proof verification. Replaces the keccak-mock shipped in week 3.3.

End-to-end lifecycle exercised by the headline test:
  1. Alice generates (secret, nullifier) and computes commitment off-chain
  2. Alice deposits 1 ETH on-chain with the commitment
  3. The on-chain Merkle tree updates its root
  4. Alice generates a Groth16 proof off-chain that the commitment is in
     the tree at that root, with `charlie` as the bound recipient
  5. Bob (a different address) submits the withdraw tx
  6. Contract verifies proof, records nullifierHash, pays charlie 1 ETH
"""
from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError

from aml.chains import AnvilNode
from aml.chains.mimc import FIELD_SIZE, deploy_mimc

REPO_ROOT = Path(__file__).resolve().parents[1]
CIRCUIT = "withdraw"
BUILD = REPO_ROOT / "circuits" / "build" / CIRCUIT
WASM = BUILD / f"{CIRCUIT}_js" / f"{CIRCUIT}.wasm"
ZKEY = BUILD / f"{CIRCUIT}_final.zkey"
VKEY = BUILD / "verification_key.json"
HELPER_JS = REPO_ROOT / "scripts" / "zk_helpers.js"
NODE_MODULES_CIRCOMLIBJS = REPO_ROOT / "node_modules" / "circomlibjs"
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"
VERIFIER_ARTIFACT = REPO_ROOT / "out" / "Verifier.sol" / "Groth16Verifier.json"

DENOMINATION_WEI = 10**18
MERKLE_DEPTH = 10


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


def _random_field_element() -> int:
    return int.from_bytes(secrets.token_bytes(31), "big")


def _prepare_withdraw(secret: int, nullifier: int, leaf_index: int) -> dict:
    result = subprocess.run(
        ["node", str(HELPER_JS), "prepare-withdraw",
         str(secret), str(nullifier), str(leaf_index), str(MERKLE_DEPTH)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def _generate_proof(secret, nullifier, prepared, recipient_int, work_dir: Path):
    inp = {
        "root": prepared["root"],
        "nullifierHash": prepared["nullifierHash"],
        "recipient": str(recipient_int),
        "fee": "0",
        "refund": "0",
        "nullifier": str(nullifier),
        "secret": str(secret),
        "pathElements": prepared["pathElements"],
        "pathIndices": prepared["pathIndices"],
    }
    input_json = work_dir / "input.json"
    input_json.write_text(json.dumps(inp))
    witness = work_dir / "witness.wtns"
    proof_path = work_dir / "proof.json"
    public_path = work_dir / "public.json"

    subprocess.run([
        "snarkjs", "wtns", "calculate",
        str(WASM), str(input_json), str(witness),
    ], check=True, capture_output=True)
    subprocess.run([
        "snarkjs", "groth16", "prove",
        str(ZKEY), str(witness), str(proof_path), str(public_path),
    ], check=True, capture_output=True)
    return json.loads(proof_path.read_text()), json.loads(public_path.read_text())


def _proof_to_solidity(proof: dict, public_signals: list):
    pi_a = [int(proof["pi_a"][0]), int(proof["pi_a"][1])]
    pi_b = [
        [int(proof["pi_b"][0][1]), int(proof["pi_b"][0][0])],
        [int(proof["pi_b"][1][1]), int(proof["pi_b"][1][0])],
    ]
    pi_c = [int(proof["pi_c"][0]), int(proof["pi_c"][1])]
    return pi_a, pi_b, pi_c


def _commitment_bytes(prepared) -> bytes:
    return int(prepared["commitment"]).to_bytes(32, "big")


def _root_bytes(prepared) -> bytes:
    return int(prepared["root"]).to_bytes(32, "big")


def _nullifier_hash_bytes(prepared) -> bytes:
    return int(prepared["nullifierHash"]).to_bytes(32, "big")


def _addr_to_int(addr: str) -> int:
    return int(addr, 16)


def _deploy_tornado(w3, deployer, deployer_key):
    """Deploy MiMC + Verifier + MockTornado, return (mimc, verifier, tornado)."""
    mimc = deploy_mimc(w3, deployer, deployer_key)

    verifier_abi, verifier_bytecode = _load_artifact(VERIFIER_ARTIFACT)
    v_factory = w3.eth.contract(abi=verifier_abi, bytecode=verifier_bytecode)
    verifier = w3.eth.contract(
        address=_send(w3, v_factory.constructor(), deployer, deployer_key).contractAddress,
        abi=verifier_abi,
    )

    tornado_abi, tornado_bytecode = _load_artifact(TORNADO_ARTIFACT)
    t_factory = w3.eth.contract(abi=tornado_abi, bytecode=tornado_bytecode)
    tornado = w3.eth.contract(
        address=_send(
            w3, t_factory.constructor(verifier.address, mimc.address, MERKLE_DEPTH),
            deployer, deployer_key,
            gas=10_000_000,
        ).contractAddress,
        abi=tornado_abi,
    )
    return mimc, verifier, tornado


# --- Tests ---------------------------------------------------------------


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_deposit_records_commitment_and_grows_tree():
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        _, _, tornado = _deploy_tornado(w3, deployer, deployer_key)

        secret = _random_field_element()
        nullifier = _random_field_element()
        prepared = _prepare_withdraw(secret, nullifier, 0)
        commitment = _commitment_bytes(prepared)

        _send(w3, tornado.functions.deposit(commitment), alice, alice_key,
              value=DENOMINATION_WEI)

        assert tornado.functions.commitments(commitment).call() is True
        assert tornado.functions.nextIndex().call() == 1
        assert tornado.functions.poolBalance().call() == DENOMINATION_WEI


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_deposit_wrong_denomination_reverts():
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice = node.accounts[1]
        _, _, tornado = _deploy_tornado(w3, deployer, deployer_key)

        secret = _random_field_element()
        nullifier = _random_field_element()
        prepared = _prepare_withdraw(secret, nullifier, 0)
        commitment = _commitment_bytes(prepared)

        with pytest.raises(ContractLogicError, match="wrong denomination"):
            tornado.functions.deposit(commitment).call({
                "from": alice,
                "value": DENOMINATION_WEI // 2,
            })


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_deposit_duplicate_commitment_reverts():
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        _, _, tornado = _deploy_tornado(w3, deployer, deployer_key)

        secret = _random_field_element()
        nullifier = _random_field_element()
        prepared = _prepare_withdraw(secret, nullifier, 0)
        commitment = _commitment_bytes(prepared)

        _send(w3, tornado.functions.deposit(commitment), alice, alice_key,
              value=DENOMINATION_WEI)
        with pytest.raises(ContractLogicError, match="duplicate commitment"):
            tornado.functions.deposit(commitment).call({
                "from": alice,
                "value": DENOMINATION_WEI,
            })


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_zk_laundering_lifecycle_alice_to_charlie(tmp_path: Path):
    """The headline: alice deposits, bob submits the withdraw tx, charlie receives.

    Three different addresses involved. The ZK proof binds the recipient,
    so even bob (who pays gas for the withdraw tx) can't redirect funds.
    This is the laundering primitive done with real cryptographic privacy.
    """
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        bob, bob_key = node.accounts[2], node.private_keys[2]
        charlie = node.accounts[3]
        _, _, tornado = _deploy_tornado(w3, deployer, deployer_key)

        # 1. Alice's deposit
        secret = _random_field_element()
        nullifier = _random_field_element()
        prepared = _prepare_withdraw(secret, nullifier, 0)
        _send(w3, tornado.functions.deposit(_commitment_bytes(prepared)),
              alice, alice_key, value=DENOMINATION_WEI)

        # 2. Off-chain proof generation, recipient bound to charlie
        proof, public_signals = _generate_proof(
            secret, nullifier, prepared, _addr_to_int(charlie), tmp_path,
        )
        pa, pb, pc = _proof_to_solidity(proof, public_signals)

        # 3. Bob submits the withdraw — note: NOT alice, NOT charlie
        charlie_before = w3.eth.get_balance(charlie)
        _send(w3, tornado.functions.withdraw(
            pa, pb, pc, _root_bytes(prepared), _nullifier_hash_bytes(prepared),
            charlie, 0, 0,
        ), bob, bob_key)

        # 4. Charlie has 1 ETH; alice's address is only linked via the deposit
        assert w3.eth.get_balance(charlie) == charlie_before + DENOMINATION_WEI
        assert tornado.functions.nullifierHashes(_nullifier_hash_bytes(prepared)).call() is True
        assert tornado.functions.poolBalance().call() == 0


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_withdraw_unknown_root_reverts(tmp_path: Path):
    """Proof generated against a root that was never deposited reverts."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        bob = node.accounts[2]
        charlie = node.accounts[3]
        _, _, tornado = _deploy_tornado(w3, deployer, deployer_key)

        # No deposit happens. Off-chain root is well-formed but never on-chain.
        secret = _random_field_element()
        nullifier = _random_field_element()
        prepared = _prepare_withdraw(secret, nullifier, 0)
        proof, public_signals = _generate_proof(
            secret, nullifier, prepared, _addr_to_int(charlie), tmp_path,
        )
        pa, pb, pc = _proof_to_solidity(proof, public_signals)

        with pytest.raises(ContractLogicError, match="unknown root"):
            tornado.functions.withdraw(
                pa, pb, pc,
                _root_bytes(prepared), _nullifier_hash_bytes(prepared),
                charlie, 0, 0,
            ).call({"from": bob})


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_withdraw_double_nullifier_reverts(tmp_path: Path):
    """Same nullifier can't be spent twice."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        charlie = node.accounts[3]
        _, _, tornado = _deploy_tornado(w3, deployer, deployer_key)

        secret = _random_field_element()
        nullifier = _random_field_element()
        prepared = _prepare_withdraw(secret, nullifier, 0)
        _send(w3, tornado.functions.deposit(_commitment_bytes(prepared)),
              alice, alice_key, value=DENOMINATION_WEI)

        proof, public_signals = _generate_proof(
            secret, nullifier, prepared, _addr_to_int(charlie), tmp_path,
        )
        pa, pb, pc = _proof_to_solidity(proof, public_signals)

        # First withdraw succeeds
        _send(w3, tornado.functions.withdraw(
            pa, pb, pc, _root_bytes(prepared), _nullifier_hash_bytes(prepared),
            charlie, 0, 0,
        ), alice, alice_key)

        with pytest.raises(ContractLogicError, match="nullifier already used"):
            tornado.functions.withdraw(
                pa, pb, pc,
                _root_bytes(prepared), _nullifier_hash_bytes(prepared),
                charlie, 0, 0,
            ).call({"from": alice})


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_withdraw_invalid_proof_reverts(tmp_path: Path):
    """Tampered proof component → verifier rejects → contract reverts."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        bob = node.accounts[2]
        charlie = node.accounts[3]
        _, _, tornado = _deploy_tornado(w3, deployer, deployer_key)

        secret = _random_field_element()
        nullifier = _random_field_element()
        prepared = _prepare_withdraw(secret, nullifier, 0)
        _send(w3, tornado.functions.deposit(_commitment_bytes(prepared)),
              alice, alice_key, value=DENOMINATION_WEI)

        proof, public_signals = _generate_proof(
            secret, nullifier, prepared, _addr_to_int(charlie), tmp_path,
        )
        pa, pb, pc = _proof_to_solidity(proof, public_signals)
        # Tamper pi_a within the field
        pa[0] = (pa[0] + 1) % FIELD_SIZE

        with pytest.raises(ContractLogicError, match="invalid proof"):
            tornado.functions.withdraw(
                pa, pb, pc,
                _root_bytes(prepared), _nullifier_hash_bytes(prepared),
                charlie, 0, 0,
            ).call({"from": bob})


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_anonymity_set_grows_with_deposits():
    """Each deposit increases the on-chain anonymity set."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        _, _, tornado = _deploy_tornado(w3, deployer, deployer_key)

        for i in range(4):
            sender = node.accounts[i]
            sender_key = node.private_keys[i]
            secret = _random_field_element()
            nullifier = _random_field_element()
            prepared = _prepare_withdraw(secret, nullifier, i)
            _send(w3, tornado.functions.deposit(_commitment_bytes(prepared)),
                  sender, sender_key, value=DENOMINATION_WEI)
            assert tornado.functions.nextIndex().call() == i + 1

        assert tornado.functions.poolBalance().call() == 4 * DENOMINATION_WEI


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
def test_direct_send_reverts():
    """Sending ETH directly (not via deposit) must revert via receive()."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        alice, alice_key = node.accounts[1], node.private_keys[1]
        _, _, tornado = _deploy_tornado(w3, deployer, deployer_key)

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
        assert receipt.status == 0
        assert tornado.functions.poolBalance().call() == 0
