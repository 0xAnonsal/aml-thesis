"""End-to-end ZK toolchain validation.

Generates a Groth16 proof for the trivial multiplier circuit (a * b = c) via
snarkjs, then verifies it via snarkjs. If both pass, the install + setup +
prove + verify pipeline works.

Skips cleanly if circom or snarkjs are not on PATH (e.g. CI without the
toolchain installed). Skips if scripts/setup_zk.sh hasn't been run yet.

Real cryptographic content (MiMC + Merkle inclusion proof) lands in PR #11.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CIRCUIT = "multiplier"
BUILD = REPO_ROOT / "circuits" / "build" / CIRCUIT
WASM = BUILD / f"{CIRCUIT}_js" / f"{CIRCUIT}.wasm"
ZKEY = BUILD / f"{CIRCUIT}_final.zkey"
VKEY = BUILD / "verification_key.json"
VERIFIER_SOL = BUILD / "Verifier.sol"


needs_zk_toolchain = pytest.mark.skipif(
    shutil.which("circom") is None or shutil.which("snarkjs") is None,
    reason="requires circom + snarkjs on PATH; run bash scripts/install_zk_tools.sh",
)


needs_zk_setup = pytest.mark.skipif(
    not (WASM.exists() and ZKEY.exists() and VKEY.exists()),
    reason="ZK setup artifacts missing; run bash scripts/setup_zk.sh",
)


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


@needs_zk_toolchain
def test_tools_on_path():
    """circom and snarkjs are on PATH and identify themselves when invoked.

    Note: snarkjs --version prints its version banner but exits with code 99
    (it treats "no subcommand given" as an error condition). circom --version
    exits cleanly. We tolerate either by checking returncode separately and
    asserting only on stdout content.
    """
    cv = subprocess.run(
        ["circom", "--version"], capture_output=True, text=True, check=False
    )
    assert cv.returncode == 0 and "circom" in cv.stdout.lower(), cv.stdout

    sj = subprocess.run(
        ["snarkjs", "--version"], capture_output=True, text=True, check=False
    )
    assert "snarkjs" in sj.stdout.lower(), sj.stdout


@needs_zk_setup
def test_setup_artifacts_present():
    """setup_zk.sh produced the expected files."""
    for path in [WASM, ZKEY, VKEY, VERIFIER_SOL]:
        assert path.exists(), f"missing setup artifact: {path}"
    # Verifier.sol non-empty + has the verifyProof signature
    text = VERIFIER_SOL.read_text()
    assert "function verifyProof" in text, "Verifier.sol does not export verifyProof"


@needs_zk_setup
def test_proof_roundtrip(tmp_path: Path):
    """Generate a witness, prove, verify — full cryptographic round-trip."""
    # Witness input: a=3, b=11, c=33 (the public output)
    input_json = tmp_path / "input.json"
    input_json.write_text(json.dumps({"a": "3", "b": "11", "c": "33"}))

    witness = tmp_path / "witness.wtns"
    proof = tmp_path / "proof.json"
    public = tmp_path / "public.json"

    # 1. Calculate witness from inputs
    _run([
        "snarkjs", "wtns", "calculate",
        str(WASM), str(input_json), str(witness),
    ])
    assert witness.exists()

    # 2. Generate Groth16 proof
    _run([
        "snarkjs", "groth16", "prove",
        str(ZKEY), str(witness),
        str(proof), str(public),
    ])
    assert proof.exists() and public.exists()

    # Public signals should contain c=33 (the only public input)
    public_signals = json.loads(public.read_text())
    assert public_signals == ["33"], f"unexpected public signals: {public_signals}"

    # 3. Verify the proof against the verification key
    result = _run([
        "snarkjs", "groth16", "verify",
        str(VKEY), str(public), str(proof),
    ])
    # snarkjs prints either to stdout or stderr depending on version
    output = (result.stdout + result.stderr).lower()
    assert "ok" in output, f"verification did not report OK: {result.stdout}\n{result.stderr}"


@needs_zk_setup
def test_proof_roundtrip_rejects_wrong_witness(tmp_path: Path):
    """Witness whose inputs don't satisfy a*b=c should fail to compute."""
    input_json = tmp_path / "input.json"
    input_json.write_text(json.dumps({"a": "3", "b": "11", "c": "32"}))  # 3*11 != 32

    witness = tmp_path / "witness.wtns"
    with pytest.raises(subprocess.CalledProcessError):
        _run([
            "snarkjs", "wtns", "calculate",
            str(WASM), str(input_json), str(witness),
        ])
