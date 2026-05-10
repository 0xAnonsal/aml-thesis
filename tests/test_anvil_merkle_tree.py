"""On-chain Merkle tree integration test.

Deploys MiMC + MerkleTreeWithHistory(depth=10), inserts a sequence of
leaves on-chain, and verifies that the resulting on-chain root matches the
off-chain root computed by the same MiMC primitive (via circomlibjs's
multiHash). If this passes, on-chain deposits produce the exact roots that
the withdraw circuit's prepare-withdraw helper computes — which means the
ZK verifier (PR 4.4) will accept proofs against on-chain deposits.

This is the cryptographic precondition for PR 4.5c (MockTornado refactor).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from web3 import Web3

from aml.chains import AnvilNode
from aml.chains.mimc import FIELD_SIZE, deploy_mimc

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_JS = REPO_ROOT / "scripts" / "zk_helpers.js"
NODE_MODULES_CIRCOMLIBJS = REPO_ROOT / "node_modules" / "circomlibjs"
TREE_ARTIFACT = REPO_ROOT / "out" / "MerkleTreeWithHistory.sol" / "MerkleTreeWithHistory.json"

DEPTH = 10


needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None or shutil.which("forge") is None,
    reason="requires Foundry (anvil + forge) on PATH",
)


needs_circomlibjs = pytest.mark.skipif(
    not NODE_MODULES_CIRCOMLIBJS.exists() or shutil.which("node") is None,
    reason="circomlibjs not installed or node missing; run bash scripts/setup_zk.sh",
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
    if TREE_ARTIFACT.exists():
        return
    subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)


def _load_tree_artifact():
    _ensure_compiled()
    with TREE_ARTIFACT.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


def _offchain_root(leaves: list[int], depth: int = DEPTH) -> int:
    """Compute the Merkle root of `leaves` (zero-padded) using circomlibjs."""
    result = subprocess.run(
        ["node", str(HELPER_JS), "merkle-root", str(depth), *map(str, leaves)],
        check=True, capture_output=True, text=True,
    )
    return int(json.loads(result.stdout)["root"])


def _deploy_tree(w3, deployer, deployer_key, hasher_address):
    abi, bytecode = _load_tree_artifact()
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    receipt = _send(
        w3, factory.constructor(DEPTH, hasher_address),
        deployer, deployer_key,
        gas=8_000_000,   # constructor pre-computes 10 levels of zeros (MiMC calls)
    )
    return w3.eth.contract(address=receipt.contractAddress, abi=abi)


@needs_circomlibjs
def test_offchain_merkle_root_matches_known_value():
    """Sanity: an empty tree's off-chain root is deterministic."""
    empty_root = _offchain_root([])
    same = _offchain_root([])
    assert empty_root == same, "off-chain root not deterministic"
    assert 0 < empty_root < FIELD_SIZE


@needs_foundry
@needs_circomlibjs
def test_empty_tree_root_matches(tmp_path: Path):
    """Constructor-computed empty-tree root == off-chain empty-tree root."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        mimc = deploy_mimc(w3, deployer, deployer_key)
        tree = _deploy_tree(w3, deployer, deployer_key, mimc.address)

        onchain_root = int.from_bytes(tree.functions.getLastRoot().call(), "big")
        offchain_root = _offchain_root([])
        assert onchain_root == offchain_root, (
            f"empty tree root mismatch:\n"
            f"  on-chain : {onchain_root}\n"
            f"  off-chain: {offchain_root}"
        )


@needs_foundry
@needs_circomlibjs
def test_single_leaf_root_matches(tmp_path: Path):
    """Insert one leaf; on-chain root matches off-chain root."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        mimc = deploy_mimc(w3, deployer, deployer_key)
        tree = _deploy_tree(w3, deployer, deployer_key, mimc.address)

        leaf = 12345
        leaf_bytes = leaf.to_bytes(32, "big")
        _send(w3, tree.functions.insert(leaf_bytes), deployer, deployer_key)

        onchain_root = int.from_bytes(tree.functions.getLastRoot().call(), "big")
        offchain_root = _offchain_root([leaf])
        assert onchain_root == offchain_root, (
            f"single-leaf root mismatch:\n"
            f"  on-chain : {onchain_root}\n"
            f"  off-chain: {offchain_root}"
        )


@needs_foundry
@needs_circomlibjs
def test_sequential_insertion_root_matches_each_step(tmp_path: Path):
    """Insert 5 leaves one at a time; root matches off-chain after each step."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        mimc = deploy_mimc(w3, deployer, deployer_key)
        tree = _deploy_tree(w3, deployer, deployer_key, mimc.address)

        leaves = [11111, 22222, 33333, 44444, 55555]
        for i, leaf in enumerate(leaves):
            leaf_bytes = leaf.to_bytes(32, "big")
            _send(w3, tree.functions.insert(leaf_bytes), deployer, deployer_key)

            onchain = int.from_bytes(tree.functions.getLastRoot().call(), "big")
            expected = _offchain_root(leaves[: i + 1])
            assert onchain == expected, (
                f"root mismatch after inserting leaf {i} ({leaf}):\n"
                f"  on-chain : {onchain}\n"
                f"  off-chain: {expected}"
            )

        # nextIndex tracks total inserted leaves
        assert tree.functions.nextIndex().call() == len(leaves)


@needs_foundry
@needs_circomlibjs
def test_isKnownRoot_finds_historical_roots():
    """After several inserts, all past roots remain queryable via isKnownRoot."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        mimc = deploy_mimc(w3, deployer, deployer_key)
        tree = _deploy_tree(w3, deployer, deployer_key, mimc.address)

        seen_roots = []
        for leaf in [10, 20, 30]:
            _send(w3, tree.functions.insert(leaf.to_bytes(32, "big")), deployer, deployer_key)
            seen_roots.append(tree.functions.getLastRoot().call())

        for root in seen_roots:
            assert tree.functions.isKnownRoot(root).call() is True

        # A made-up root must not appear
        bogus = (1234567890).to_bytes(32, "big")
        assert tree.functions.isKnownRoot(bogus).call() is False
        # Zero root is explicitly rejected
        assert tree.functions.isKnownRoot(b"\x00" * 32).call() is False


@needs_foundry
@needs_circomlibjs
def test_leaf_in_field_is_required():
    """Inserting a leaf >= FIELD_SIZE reverts at hashLeftRight's field check."""
    with AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, deployer_key = node.accounts[0], node.private_keys[0]
        mimc = deploy_mimc(w3, deployer, deployer_key)
        tree = _deploy_tree(w3, deployer, deployer_key, mimc.address)

        # FIELD_SIZE itself is invalid (must be strictly less than)
        out_of_field = FIELD_SIZE.to_bytes(32, "big")
        receipt = _send(
            w3, tree.functions.insert(out_of_field),
            deployer, deployer_key, gas=4_000_000,
        )
        assert receipt.status == 0, "expected on-chain revert for out-of-field leaf"
