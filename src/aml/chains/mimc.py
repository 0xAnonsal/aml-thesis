"""Deploy and call the auto-generated MiMCSponge contract on Anvil.

The contract bytecode + ABI come from circomlibjs's `mimcSpongecontract.createCode`
(seed="mimcsponge", 220 rounds) — the same generator Tornado Cash uses. Bytecode
is fetched on demand via `node scripts/zk_helpers.js mimc-bytecode`.

The deployed contract exposes a single function:

    function MiMCSponge(uint256 in_xL, uint256 in_xR)
        external pure returns (uint256 xL, uint256 xR)

This is ONE Feistel permutation. To compute MiMCSponge(2, 220, 1) — i.e. the
2-input, 1-output sponge our withdraw circuit uses — call the contract twice
in sequence:

    (R, C) = mimc.MiMCSponge(left, 0)
    (R, _) = mimc.MiMCSponge((R + right) % FIELD_SIZE, C)
    return R    # equivalent to circomlibjs.multiHash([left, right], 0n)

The on-chain Merkle tree (PR 4.5b) wraps that pattern in a `hashLeftRight`
helper.
"""
from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER_JS = REPO_ROOT / "scripts" / "zk_helpers.js"

# bn254 scalar field — same as circomlib's snark field
FIELD_SIZE = (
    21888242871839275222246405745257275088548364400416034343698204186575808495617
)


@lru_cache(maxsize=1)
def mimc_abi() -> list:
    """ABI of the MiMCSponge contract from circomlibjs's gencontract helper."""
    result = subprocess.run(
        ["node", str(HELPER_JS), "mimc-abi"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


@lru_cache(maxsize=1)
def mimc_bytecode() -> str:
    """Creation bytecode (hex with 0x prefix) of the MiMCSponge contract."""
    result = subprocess.run(
        ["node", str(HELPER_JS), "mimc-bytecode"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _raw_tx(signed) -> bytes:
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes attribute")
    return raw


def deploy_mimc(w3: Web3, deployer: str, deployer_key: str):
    """Deploy the MiMCSponge contract and return a web3 contract handle."""
    abi = mimc_abi()
    bytecode = mimc_bytecode()
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = factory.constructor().build_transaction({
        "from": deployer,
        "nonce": w3.eth.get_transaction_count(deployer),
        "gas": 5_000_000,   # MiMC bytecode is ~6 KB, deployment is gas-heavy
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=deployer_key)
    receipt = w3.eth.wait_for_transaction_receipt(
        w3.eth.send_raw_transaction(_raw_tx(signed))
    )
    return w3.eth.contract(address=receipt.contractAddress, abi=abi)


def hash_left_right(mimc_contract, left: int, right: int) -> int:
    """Compute MiMCSponge(2, 220, 1)([left, right], k=0) on-chain.

    Uses two single-Feistel calls in the canonical 2-input sponge pattern.
    Equivalent to circomlibjs's `multiHash([left, right], 0n)` and to our
    in-circuit `MiMCSponge(2, 220, 1)`.

    The auto-generated MiMC contract's function signature is
    `MiMCSponge(uint256 xL_in, uint256 xR_in, uint256 k)`. We always pass
    k=0 (matching circomlib's MerkleTree convention).
    """
    r, c = mimc_contract.functions.MiMCSponge(left, 0, 0).call()
    r = (r + right) % FIELD_SIZE
    r, _ = mimc_contract.functions.MiMCSponge(r, c, 0).call()
    return r
