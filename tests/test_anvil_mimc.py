"""On-chain MiMCSponge sanity check.

Deploys the auto-generated MiMC contract (circomlibjs's mimcSpongecontract,
seed='mimcsponge', 220 rounds) on Anvil and verifies that the on-chain hash
matches the off-chain MiMC used by our circuit + prepare-withdraw helper.

If this passes, the precondition for PR 4.5b (Solidity Merkle tree) holds:
the on-chain Merkle tree built using this MiMC contract will produce the
same root that the circuit verifies against.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3

from aml.chains import AnvilNode
from aml.chains.mimc import FIELD_SIZE, deploy_mimc, hash_left_right

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_JS = REPO_ROOT / "scripts" / "zk_helpers.js"
NODE_MODULES_CIRCOMLIBJS = REPO_ROOT / "node_modules" / "circomlibjs"


needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None,
    reason="requires Foundry's anvil on PATH",
)


needs_circomlibjs = pytest.mark.skipif(
    not NODE_MODULES_CIRCOMLIBJS.exists() or shutil.which("node") is None,
    reason="circomlibjs not installed or node missing; run bash scripts/setup_zk.sh",
)


def _mimc_offchain_hash2(a: int, b: int) -> int:
    """MiMCSponge(2, 220, 1)([a, b], k=0) computed via circomlibjs."""
    result = subprocess.run(
        ["node", str(HELPER_JS), "mimc2", str(a), str(b)],
        check=True, capture_output=True, text=True,
    )
    return int(result.stdout.strip())


def _mimc_offchain_hash1(a: int) -> int:
    """MiMCSponge(1, 220, 1)([a], k=0) computed via circomlibjs."""
    result = subprocess.run(
        ["node", str(HELPER_JS), "mimc", str(a)],
        check=True, capture_output=True, text=True,
    )
    return int(result.stdout.strip())


@needs_circomlibjs
def test_helper_returns_bytecode_and_abi():
    """zk_helpers.js exposes the MiMC bytecode + ABI sensibly."""
    bytecode = subprocess.run(
        ["node", str(HELPER_JS), "mimc-bytecode"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert bytecode.startswith("0x"), f"bytecode missing 0x prefix: {bytecode[:20]}"
    assert len(bytecode) > 100, "bytecode unrealistically short"

    abi_text = subprocess.run(
        ["node", str(HELPER_JS), "mimc-abi"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    abi = json.loads(abi_text)
    fn_names = [item.get("name") for item in abi if item.get("type") == "function"]
    assert "MiMCSponge" in fn_names, f"ABI missing MiMCSponge: {fn_names}"


@needs_foundry
@needs_circomlibjs
def test_mimc_deploys_and_responds():
    """Deploy MiMC bytecode and call it once — confirms it accepts the ABI."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        mimc = deploy_mimc(w3, deployer, deployer_key)

        assert w3.eth.get_code(mimc.address) != b"", "MiMC contract has no code"
        # Single-Feistel call: MiMCSponge(xL_in, xR_in, k). We pass k=0.
        xL, xR = mimc.functions.MiMCSponge(1234, 5678, 0).call()
        assert xL != 0
        assert xR != 0
        assert xL < FIELD_SIZE
        assert xR < FIELD_SIZE


@needs_foundry
@needs_circomlibjs
def test_onchain_hash_left_right_matches_offchain():
    """The headline test: on-chain MiMCSponge(2, 220, 1) == off-chain version.

    If this passes, we can build a Solidity Merkle tree that produces the
    same root as our circomlibjs off-chain tree, which is the precondition
    for the ZK verifier to accept proofs against on-chain deposits.
    """
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        mimc = deploy_mimc(w3, deployer, deployer_key)

        # Range of representative inputs — small, large, near-field-edge
        cases = [
            (0, 0),
            (1, 2),
            (1234, 5678),
            (FIELD_SIZE - 1, FIELD_SIZE - 2),
            (
                int.from_bytes(b"alice deposit secret", "big"),
                int.from_bytes(b"alice deposit nullif", "big"),
            ),
        ]

        for left, right in cases:
            offchain = _mimc_offchain_hash2(left, right)
            onchain = hash_left_right(mimc, left, right)
            assert onchain == offchain, (
                f"mismatch for ({left}, {right}):\n"
                f"  off-chain: {offchain}\n"
                f"  on-chain:  {onchain}"
            )


@needs_foundry
@needs_circomlibjs
def test_onchain_hash_is_deterministic_and_field_bounded():
    """Same inputs must produce same output; output must be in the field."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        mimc = deploy_mimc(w3, deployer, deployer_key)

        h1 = hash_left_right(mimc, 7, 11)
        h2 = hash_left_right(mimc, 7, 11)
        assert h1 == h2, "MiMC not deterministic on-chain"
        assert 0 < h1 < FIELD_SIZE


@needs_foundry
@needs_circomlibjs
def test_different_inputs_produce_different_hashes():
    """Trivial sanity: collision-resistance proxy."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        mimc = deploy_mimc(w3, deployer, deployer_key)

        seen = {hash_left_right(mimc, i, i + 1) for i in range(10)}
        assert len(seen) == 10, "hash collisions across 10 simple inputs"
