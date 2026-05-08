"""Full Tornado-style ZK withdraw roundtrip.

Generates a deposit note (random secret + nullifier), builds a Merkle tree
with the resulting commitment placed at a chosen leaf, then proves and
verifies a Groth16 withdraw proof entirely off-chain. This is the
cryptographic heart of the ZK mixer — once green, the only thing left for
the upgrade is wiring the generated Verifier.sol into MockTornado on Anvil
(PR 4.4).
"""
from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CIRCUIT = "withdraw"
BUILD = REPO_ROOT / "circuits" / "build" / CIRCUIT
WASM = BUILD / f"{CIRCUIT}_js" / f"{CIRCUIT}.wasm"
ZKEY = BUILD / f"{CIRCUIT}_final.zkey"
VKEY = BUILD / "verification_key.json"
HELPER_JS = REPO_ROOT / "scripts" / "zk_helpers.js"
NODE_MODULES_CIRCOMLIBJS = REPO_ROOT / "node_modules" / "circomlibjs"

# Must match `component main = Withdraw(10);` in circuits/withdraw.circom
MERKLE_DEPTH = 10
ALICE = 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266   # Anvil default account 0


needs_zk_setup = pytest.mark.skipif(
    not (WASM.exists() and ZKEY.exists() and VKEY.exists()),
    reason=f"ZK setup artifacts missing; run bash scripts/setup_zk.sh {CIRCUIT}",
)


needs_circomlibjs = pytest.mark.skipif(
    not NODE_MODULES_CIRCOMLIBJS.exists() or shutil.which("node") is None,
    reason="circomlibjs not installed or node missing; run bash scripts/setup_zk.sh",
)


def _random_field_element() -> int:
    """Random 31-byte int — fits comfortably under bn254's 254-bit prime."""
    return int.from_bytes(secrets.token_bytes(31), "big")


def _prepare_withdraw(secret: int, nullifier: int, leaf_index: int) -> dict:
    """Off-chain helper: compute commitment, nullifierHash, root, Merkle path."""
    result = subprocess.run(
        [
            "node", str(HELPER_JS), "prepare-withdraw",
            str(secret), str(nullifier), str(leaf_index), str(MERKLE_DEPTH),
        ],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


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


def _verify_proof(proof: dict, public_signals: list, work_dir: Path) -> bool:
    proof_path = work_dir / "verify_proof.json"
    public_path = work_dir / "verify_public.json"
    proof_path.write_text(json.dumps(proof))
    public_path.write_text(json.dumps(public_signals))

    result = subprocess.run([
        "snarkjs", "groth16", "verify",
        str(VKEY), str(public_path), str(proof_path),
    ], capture_output=True, text=True, check=True)
    return "ok" in (result.stdout + result.stderr).lower()


@needs_circomlibjs
def test_prepare_withdraw_returns_well_formed_inputs():
    prepared = _prepare_withdraw(secret=12345, nullifier=67890, leaf_index=0)

    P = 21888242871839275222246405745257275088548364400416034343698204186575808495617
    for key in ["commitment", "nullifierHash", "root"]:
        assert 0 < int(prepared[key]) < P, f"{key} out of bn254 range"

    assert len(prepared["pathElements"]) == MERKLE_DEPTH
    assert len(prepared["pathIndices"]) == MERKLE_DEPTH
    # leaf_index=0 means the path goes always-left (every pathIndices bit is 0)
    assert all(int(b) == 0 for b in prepared["pathIndices"])


@needs_zk_setup
@needs_circomlibjs
def test_full_withdraw_roundtrip(tmp_path: Path):
    """The headline test: deposit note -> Merkle path -> proof -> verify."""
    secret = _random_field_element()
    nullifier = _random_field_element()
    leaf_index = 0

    prepared = _prepare_withdraw(secret, nullifier, leaf_index)
    inp = _circuit_input(prepared, secret, nullifier, recipient=ALICE)

    proof, public_signals = _generate_proof(inp, tmp_path)

    # public_signals order: [root, nullifierHash, recipient, fee, refund]
    assert len(public_signals) == 5
    assert public_signals[0] == prepared["root"]
    assert public_signals[1] == prepared["nullifierHash"]
    assert int(public_signals[2]) == ALICE
    assert public_signals[3] == "0"
    assert public_signals[4] == "0"

    assert _verify_proof(proof, public_signals, tmp_path)


@needs_zk_setup
@needs_circomlibjs
def test_proof_at_nonzero_leaf_index(tmp_path: Path):
    """The Merkle path is right for a leaf at a non-zero index."""
    secret = _random_field_element()
    nullifier = _random_field_element()
    leaf_index = 7   # arbitrary non-zero, well within depth=10

    prepared = _prepare_withdraw(secret, nullifier, leaf_index)
    # 7 = 0b0000000111 — the low 3 bits are 1, the rest 0
    expected_indices = [1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
    assert [int(b) for b in prepared["pathIndices"]] == expected_indices

    inp = _circuit_input(prepared, secret, nullifier, recipient=ALICE)
    _, public_signals = _generate_proof(inp, tmp_path)
    assert public_signals[0] == prepared["root"]


@needs_zk_setup
@needs_circomlibjs
def test_wrong_nullifier_hash_rejects(tmp_path: Path):
    """Tampered nullifierHash must be rejected at witness generation."""
    secret = 11111
    nullifier = 22222
    prepared = _prepare_withdraw(secret, nullifier, 0)

    inp = _circuit_input(prepared, secret, nullifier, recipient=ALICE)
    inp["nullifierHash"] = "1"   # wrong

    input_json = tmp_path / "input.json"
    input_json.write_text(json.dumps(inp))
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run([
            "snarkjs", "wtns", "calculate",
            str(WASM), str(input_json), str(tmp_path / "witness.wtns"),
        ], check=True, capture_output=True)


@needs_zk_setup
@needs_circomlibjs
def test_wrong_root_rejects(tmp_path: Path):
    """Tampered Merkle root must be rejected at witness generation."""
    secret = 33333
    nullifier = 44444
    prepared = _prepare_withdraw(secret, nullifier, 0)

    inp = _circuit_input(prepared, secret, nullifier, recipient=ALICE)
    inp["root"] = "999"   # wrong

    input_json = tmp_path / "input.json"
    input_json.write_text(json.dumps(inp))
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run([
            "snarkjs", "wtns", "calculate",
            str(WASM), str(input_json), str(tmp_path / "witness.wtns"),
        ], check=True, capture_output=True)


@needs_zk_setup
@needs_circomlibjs
def test_proof_does_not_reveal_secret_or_nullifier(tmp_path: Path):
    """Public signals must NOT include `secret` or `nullifier` — only their derived hashes."""
    secret = _random_field_element()
    nullifier = _random_field_element()
    prepared = _prepare_withdraw(secret, nullifier, 0)

    inp = _circuit_input(prepared, secret, nullifier, recipient=ALICE)
    _, public_signals = _generate_proof(inp, tmp_path)

    # The 5 public signals are root, nullifierHash, recipient, fee, refund.
    # Neither secret nor nullifier should be among them.
    assert str(secret) not in public_signals
    assert str(nullifier) not in public_signals
    # nullifierHash IS in public — that's by design (contract uses it for replay protection)
    assert prepared["nullifierHash"] in public_signals
