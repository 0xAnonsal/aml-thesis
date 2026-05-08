"""Validate MiMC integration.

Compiles circuits/preimage_mimc.circom (which uses circomlib's MiMCSponge),
generates a Groth16 proof off-chain, and verifies. The expected hash is
computed off-chain via circomlibjs (scripts/zk_helpers.js) — this matches
what the in-circuit MiMCSponge computes constraint-by-constraint.

If this passes, MiMC-based circuits compile, prove, and verify correctly,
which unblocks the full Tornado-style withdraw circuit (PR 4.3).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CIRCUIT = "preimage_mimc"
BUILD = REPO_ROOT / "circuits" / "build" / CIRCUIT
WASM = BUILD / f"{CIRCUIT}_js" / f"{CIRCUIT}.wasm"
ZKEY = BUILD / f"{CIRCUIT}_final.zkey"
VKEY = BUILD / "verification_key.json"
HELPER_JS = REPO_ROOT / "scripts" / "zk_helpers.js"
NODE_MODULES_CIRCOMLIBJS = REPO_ROOT / "node_modules" / "circomlibjs"

# bn254 scalar field prime
BN254_P = 21888242871839275222246405745257275088548364400416034343698204186575808495617


needs_zk_setup = pytest.mark.skipif(
    not (WASM.exists() and ZKEY.exists() and VKEY.exists()),
    reason=f"ZK setup artifacts missing; run bash scripts/setup_zk.sh {CIRCUIT}",
)


needs_circomlibjs = pytest.mark.skipif(
    not NODE_MODULES_CIRCOMLIBJS.exists() or shutil.which("node") is None,
    reason="circomlibjs not installed or node missing; run bash scripts/setup_zk.sh",
)


def _mimc_off_chain(preimage: int) -> int:
    """MiMCSponge([preimage], k=0, nOuts=1) computed via circomlibjs."""
    result = subprocess.run(
        ["node", str(HELPER_JS), "mimc", str(preimage)],
        check=True, capture_output=True, text=True,
    )
    return int(result.stdout.strip())


@needs_circomlibjs
def test_offchain_mimc_helper_returns_field_element():
    h = _mimc_off_chain(12345)
    assert 0 < h < BN254_P, f"hash out of bn254 field range: {h}"


@needs_circomlibjs
def test_offchain_mimc_is_deterministic():
    assert _mimc_off_chain(42) == _mimc_off_chain(42)


@needs_circomlibjs
def test_offchain_mimc_different_inputs_differ():
    assert _mimc_off_chain(1) != _mimc_off_chain(2)


@needs_zk_setup
@needs_circomlibjs
def test_proof_roundtrip(tmp_path: Path):
    """The full ZK round-trip: off-chain MiMC -> witness -> proof -> verify."""
    preimage = 12345
    expected_hash = _mimc_off_chain(preimage)

    input_json = tmp_path / "input.json"
    input_json.write_text(json.dumps({
        "preimage": str(preimage),
        "expectedHash": str(expected_hash),
    }))

    witness = tmp_path / "witness.wtns"
    proof = tmp_path / "proof.json"
    public = tmp_path / "public.json"

    subprocess.run([
        "snarkjs", "wtns", "calculate",
        str(WASM), str(input_json), str(witness),
    ], check=True, capture_output=True)

    subprocess.run([
        "snarkjs", "groth16", "prove",
        str(ZKEY), str(witness),
        str(proof), str(public),
    ], check=True, capture_output=True)

    public_signals = json.loads(public.read_text())
    assert public_signals == [str(expected_hash)], f"unexpected public signals: {public_signals}"

    result = subprocess.run([
        "snarkjs", "groth16", "verify",
        str(VKEY), str(public), str(proof),
    ], capture_output=True, text=True, check=True)
    assert "ok" in (result.stdout + result.stderr).lower()


@needs_zk_setup
@needs_circomlibjs
def test_wrong_hash_rejects(tmp_path: Path):
    """Witness generation must fail when expectedHash doesn't match MiMC(preimage)."""
    input_json = tmp_path / "input.json"
    input_json.write_text(json.dumps({
        "preimage": "12345",
        "expectedHash": "999999999999",
    }))
    witness = tmp_path / "witness.wtns"
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run([
            "snarkjs", "wtns", "calculate",
            str(WASM), str(input_json), str(witness),
        ], check=True, capture_output=True)
