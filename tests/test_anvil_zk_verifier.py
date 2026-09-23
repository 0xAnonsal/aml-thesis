"""On-chain ZK verifier integration test.

Deploys the auto-generated Verifier.sol on a fresh Anvil instance, generates
a real Groth16 proof off-chain (via the same prepare-withdraw + snarkjs
pipeline as test_zk_withdraw.py), and calls verifyProof(...) on-chain. If
the on-chain verification matches the off-chain one, snarkjs's proof format
is compatible with the Solidity verifier — which is the precondition for
wiring the verifier into MockTornado in PR 4.5.

Skips cleanly if any of: Foundry, the ZK setup artifacts, the compiled
Verifier.sol, circomlibjs, or node are missing.
"""
from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3

from aml.chains import AnvilNode

REPO_ROOT = Path(__file__).resolve().parents[1]
CIRCUIT = "withdraw"
BUILD = REPO_ROOT / "circuits" / "build" / CIRCUIT
WASM = BUILD / f"{CIRCUIT}_js" / f"{CIRCUIT}.wasm"
ZKEY = BUILD / f"{CIRCUIT}_final.zkey"
VKEY = BUILD / "verification_key.json"
HELPER_JS = REPO_ROOT / "scripts" / "zk_helpers.js"
NODE_MODULES_CIRCOMLIBJS = REPO_ROOT / "node_modules" / "circomlibjs"
VERIFIER_SOL = REPO_ROOT / "contracts" / "Verifier.sol"
VERIFIER_ARTIFACT = REPO_ROOT / "out" / "Verifier.sol" / "Groth16Verifier.json"

MERKLE_DEPTH = 10
ALICE = 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266


needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH",
)


needs_zk_setup = pytest.mark.skipif(
    not (WASM.exists() and ZKEY.exists() and VKEY.exists() and VERIFIER_SOL.exists()),
    reason=f"ZK setup artifacts missing; run bash scripts/setup_zk.sh {CIRCUIT}",
)


needs_circomlibjs = pytest.mark.skipif(
    not NODE_MODULES_CIRCOMLIBJS.exists() or shutil.which("node") is None,
    reason="circomlibjs not installed or node missing; run bash scripts/setup_zk.sh",
)


needs_snarkjs = pytest.mark.skipif(
    shutil.which("snarkjs") is None,
    reason="snarkjs/circom not on PATH (run scripts/setup_zk.sh)",
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


def _ensure_compiled():
    if VERIFIER_ARTIFACT.exists():
        return
    subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)


def _load_verifier_artifact() -> tuple[list, str]:
    _ensure_compiled()
    with VERIFIER_ARTIFACT.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


def _prepare_withdraw(secret: int, nullifier: int, leaf_index: int = 0) -> dict:
    result = subprocess.run(
        [
            "node", str(HELPER_JS), "prepare-withdraw",
            str(secret), str(nullifier), str(leaf_index), str(MERKLE_DEPTH),
        ],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def _generate_proof(circuit_input: dict, work_dir: Path) -> tuple[dict, list]:
    input_json = work_dir / "input.json"
    input_json.write_text(json.dumps(circuit_input))
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


def _proof_to_solidity_args(proof: dict, public_signals: list) -> tuple:
    """Format a snarkjs Groth16 proof for the auto-generated Verifier.sol.

    snarkjs's pi_b is given as G2 element with two field-element pairs; the
    pairing precompile expects them in *swapped* order. Matches what
    `snarkjs zkey export soliditycalldata` outputs.
    """
    pi_a = [int(proof["pi_a"][0]), int(proof["pi_a"][1])]
    pi_b = [
        [int(proof["pi_b"][0][1]), int(proof["pi_b"][0][0])],
        [int(proof["pi_b"][1][1]), int(proof["pi_b"][1][0])],
    ]
    pi_c = [int(proof["pi_c"][0]), int(proof["pi_c"][1])]
    pub = [int(p) for p in public_signals]
    return pi_a, pi_b, pi_c, pub


def _circuit_input(prepared: dict, secret: int, nullifier: int, recipient: int) -> dict:
    return {
        "root": prepared["root"],
        "nullifierHash": prepared["nullifierHash"],
        "recipient": str(recipient),
        "fee": "0",
        "refund": "0",
        "nullifier": str(nullifier),
        "secret": str(secret),
        "pathElements": prepared["pathElements"],
        "pathIndices": prepared["pathIndices"],
    }


def _random_field_element() -> int:
    return int.from_bytes(secrets.token_bytes(31), "big")


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
@needs_snarkjs
def test_onchain_verifier_accepts_valid_proof(tmp_path: Path):
    """Off-chain Groth16 proof verifies on-chain via the deployed Verifier.sol."""
    abi, bytecode = _load_verifier_artifact()

    secret = _random_field_element()
    nullifier = _random_field_element()
    prepared = _prepare_withdraw(secret, nullifier, leaf_index=0)
    inp = _circuit_input(prepared, secret, nullifier, recipient=ALICE)
    proof, public_signals = _generate_proof(inp, tmp_path)
    pa, pb, pc, pub = _proof_to_solidity_args(proof, public_signals)

    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]

        factory = w3.eth.contract(abi=abi, bytecode=bytecode)
        receipt = _send(w3, factory.constructor(), deployer, deployer_key)
        verifier = w3.eth.contract(address=receipt.contractAddress, abi=abi)

        # Auto-generated Verifier.sol from snarkjs exposes:
        #   function verifyProof(uint[2] _pA, uint[2][2] _pB, uint[2] _pC,
        #                        uint[5] _pubSignals) public view returns (bool)
        ok = verifier.functions.verifyProof(pa, pb, pc, pub).call()
        assert ok is True, "on-chain verifier rejected a valid proof"


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
@needs_snarkjs
def test_onchain_verifier_rejects_tampered_public_signal(tmp_path: Path):
    """Flipping a public input (recipient) must cause on-chain verification to fail."""
    abi, bytecode = _load_verifier_artifact()

    secret = _random_field_element()
    nullifier = _random_field_element()
    prepared = _prepare_withdraw(secret, nullifier, leaf_index=0)
    inp = _circuit_input(prepared, secret, nullifier, recipient=ALICE)
    proof, public_signals = _generate_proof(inp, tmp_path)
    pa, pb, pc, pub = _proof_to_solidity_args(proof, public_signals)

    # Tamper with recipient (public signal index 2) — proof is no longer valid
    pub[2] = pub[2] ^ 1   # flip lowest bit

    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]

        factory = w3.eth.contract(abi=abi, bytecode=bytecode)
        receipt = _send(w3, factory.constructor(), deployer, deployer_key)
        verifier = w3.eth.contract(address=receipt.contractAddress, abi=abi)

        ok = verifier.functions.verifyProof(pa, pb, pc, pub).call()
        assert ok is False, "on-chain verifier accepted tampered public signal"


@needs_foundry
@needs_zk_setup
@needs_circomlibjs
@needs_snarkjs
def test_onchain_verifier_rejects_garbage_proof(tmp_path: Path):
    """Random invalid proof values must verify to false (not revert)."""
    abi, bytecode = _load_verifier_artifact()

    secret = _random_field_element()
    nullifier = _random_field_element()
    prepared = _prepare_withdraw(secret, nullifier, leaf_index=0)
    inp = _circuit_input(prepared, secret, nullifier, recipient=ALICE)
    proof, public_signals = _generate_proof(inp, tmp_path)
    pa, pb, pc, pub = _proof_to_solidity_args(proof, public_signals)

    # Corrupt pi_a — should still pass field-range checks but fail pairing
    pa[0] = (pa[0] + 1) % (
        21888242871839275222246405745257275088696311157297823662689037894645226208583
    )

    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]

        factory = w3.eth.contract(abi=abi, bytecode=bytecode)
        receipt = _send(w3, factory.constructor(), deployer, deployer_key)
        verifier = w3.eth.contract(address=receipt.contractAddress, abi=abi)

        ok = verifier.functions.verifyProof(pa, pb, pc, pub).call()
        assert ok is False, "on-chain verifier accepted corrupted proof"
