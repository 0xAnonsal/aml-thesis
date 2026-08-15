"""Boot Anvil, compile contracts via Foundry, deploy and bootstrap mocks.

Sanity check that proves the full toolchain (Foundry + Anvil + web3.py +
ZK Tornado) works end-to-end. Anvil tears down on exit; deployments are
ephemeral by design.

Currently deploys:
    - MockUSDT          (ERC-20, 6 decimals)
    - MockUniswapV2Pool (ETH/USDT constant-product AMM, 500 ETH + 1M USDT)
    - MiMCSponge        (auto-generated from circomlibjs, used by tree+circuit)
    - Verifier          (auto-generated Groth16 verifier for the withdraw circuit)
    - MockTornado       (real ZK mixer at depth 10, denomination 1 ETH)
    - MockBridge        (USDT lock-and-release for ETH<->Tron)

Closes the ETH-side mock-contract checklist for ROADMAP §3.1, with the
mixer upgraded to a real Tornado-Cash-style ZK construction in week 4.

Prereqs:
    - Foundry installed (curl -L https://foundry.paradigm.xyz | bash; foundryup)
    - aml package installed in editable mode (pip install -e .)
    - bash scripts/install_zk_tools.sh
    - bash scripts/setup_zk.sh withdraw      # writes contracts/Verifier.sol

Usage:
    python scripts/deploy_eth_mocks.py
"""
from __future__ import annotations

import json
import secrets
import subprocess
from pathlib import Path

from web3 import Web3

from aml.chains import AnvilNode
from aml.chains.mimc import deploy_mimc

REPO_ROOT = Path(__file__).resolve().parents[1]
USDT_ARTIFACT = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"
POOL_ARTIFACT = REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json"
TORNADO_ARTIFACT = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"
BRIDGE_ARTIFACT = REPO_ROOT / "out" / "MockBridge.sol" / "MockBridge.json"
VERIFIER_ARTIFACT = REPO_ROOT / "out" / "Verifier.sol" / "Groth16Verifier.json"

ALL_ARTIFACTS = [
    USDT_ARTIFACT, POOL_ARTIFACT, TORNADO_ARTIFACT, BRIDGE_ARTIFACT, VERIFIER_ARTIFACT,
]

CIRCUIT_BUILD = REPO_ROOT / "circuits" / "build" / "withdraw"
WASM = CIRCUIT_BUILD / "withdraw_js" / "withdraw.wasm"
ZKEY = CIRCUIT_BUILD / "withdraw_final.zkey"
HELPER_JS = REPO_ROOT / "scripts" / "zk_helpers.js"

BOOTSTRAP_USDT = 10_000_000 * 10**6   # was 1M — 10× larger pool (2026-08-14)
BOOTSTRAP_ETH_WEI = 5_000 * 10**18    # was 500 — reduces 10-ETH swap slippage 1.86% → 0.46%
TORNADO_DENOMINATION_WEI = 10**18
BRIDGE_DEMO_AMOUNT = 2_500 * 10**6
TRON_DEST_DEMO = b"TR1ce9NK4DM7XFq9RzTronAddrPadding"[:32].ljust(32, b"\x00")
MERKLE_DEPTH = 10


def _raw_tx(signed) -> bytes:
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes attribute")
    return raw


def _send(w3, fn, sender: str, key: str, gas: int = 2_000_000, value: int = 0):
    tx = fn.build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
        "value": value,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    return w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(_raw_tx(signed)))


def ensure_compiled() -> None:
    if all(p.exists() for p in ALL_ARTIFACTS):
        return
    print("Compiling contracts (forge build)...")
    subprocess.run(["forge", "build"], cwd=REPO_ROOT, check=True)


def load_artifact(path: Path) -> tuple[list, str]:
    with path.open() as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


def _proof_to_solidity(proof, public_signals):
    pi_a = [int(proof["pi_a"][0]), int(proof["pi_a"][1])]
    pi_b = [
        [int(proof["pi_b"][0][1]), int(proof["pi_b"][0][0])],
        [int(proof["pi_b"][1][1]), int(proof["pi_b"][1][0])],
    ]
    pi_c = [int(proof["pi_c"][0]), int(proof["pi_c"][1])]
    return pi_a, pi_b, pi_c


def main():
    ensure_compiled()
    usdt_abi, usdt_bytecode = load_artifact(USDT_ARTIFACT)
    pool_abi, pool_bytecode = load_artifact(POOL_ARTIFACT)
    tornado_abi, tornado_bytecode = load_artifact(TORNADO_ARTIFACT)
    bridge_abi, bridge_bytecode = load_artifact(BRIDGE_ARTIFACT)
    verifier_abi, verifier_bytecode = load_artifact(VERIFIER_ARTIFACT)

    with AnvilNode() as node:
        print(f"Anvil: {node.rpc_url}  (chain_id={node.chain_id})\n")
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer, key = node.accounts[0], node.private_keys[0]

        # --- USDT ---
        usdt = w3.eth.contract(
            address=_send(w3, w3.eth.contract(abi=usdt_abi, bytecode=usdt_bytecode).constructor(),
                          deployer, key).contractAddress,
            abi=usdt_abi,
        )
        print(f"MockUSDT:           {usdt.address}")

        # --- Uniswap-style pool ---
        pool = w3.eth.contract(
            address=_send(
                w3, w3.eth.contract(abi=pool_abi, bytecode=pool_bytecode).constructor(usdt.address),
                deployer, key,
            ).contractAddress,
            abi=pool_abi,
        )
        print(f"MockUniswapV2Pool:  {pool.address}")
        _send(w3, usdt.functions.mint(deployer, BOOTSTRAP_USDT), deployer, key, gas=200_000)
        _send(w3, usdt.functions.approve(pool.address, BOOTSTRAP_USDT), deployer, key, gas=200_000)
        _send(w3, pool.functions.bootstrap(BOOTSTRAP_USDT), deployer, key, value=BOOTSTRAP_ETH_WEI)
        eth_r, usdt_r = pool.functions.getReserves().call()
        print(
            f"  pool reserves:      {eth_r / 10**18:.2f} ETH / {usdt_r / 10**6:,.2f} USDT"
            f" (spot: 1 ETH = {(usdt_r / 10**6) / (eth_r / 10**18):,.2f} USDT)"
        )

        # --- ZK mixer prerequisites (MiMC + Verifier) ---
        mimc = deploy_mimc(w3, deployer, key)
        print(f"MiMCSponge:         {mimc.address}")

        verifier = w3.eth.contract(
            address=_send(
                w3, w3.eth.contract(abi=verifier_abi, bytecode=verifier_bytecode).constructor(),
                deployer, key,
            ).contractAddress,
            abi=verifier_abi,
        )
        print(f"Verifier (Groth16): {verifier.address}")

        # --- ZK MockTornado (depth 10, 1 ETH denomination) ---
        tornado = w3.eth.contract(
            address=_send(
                w3, w3.eth.contract(abi=tornado_abi, bytecode=tornado_bytecode).constructor(
                    verifier.address, mimc.address, MERKLE_DEPTH,
                ),
                deployer, key, gas=10_000_000,
            ).contractAddress,
            abi=tornado_abi,
        )
        print(f"MockTornado (ZK):   {tornado.address}")
        print(f"  depth:              {MERKLE_DEPTH} (capacity 2^{MERKLE_DEPTH} = {1 << MERKLE_DEPTH} deposits)")
        print(f"  denomination:       {tornado.functions.DENOMINATION().call() / 10**18:.0f} ETH")

        # ZK laundering demo: alice deposits, bob submits withdraw, charlie receives
        alice, alice_key = node.accounts[1], node.private_keys[1]
        bob, bob_key = node.accounts[2], node.private_keys[2]
        charlie = node.accounts[3]

        secret = int.from_bytes(secrets.token_bytes(31), "big")
        nullifier = int.from_bytes(secrets.token_bytes(31), "big")
        prepared = json.loads(subprocess.run(
            ["node", str(HELPER_JS), "prepare-withdraw",
             str(secret), str(nullifier), "0", str(MERKLE_DEPTH)],
            check=True, capture_output=True, text=True,
        ).stdout)
        commitment_bytes = int(prepared["commitment"]).to_bytes(32, "big")

        _send(w3, tornado.functions.deposit(commitment_bytes), alice, alice_key,
              value=TORNADO_DENOMINATION_WEI)

        # Generate proof off-chain
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            inp = {
                "root": prepared["root"],
                "nullifierHash": prepared["nullifierHash"],
                "recipient": str(int(charlie, 16)),
                "fee": "0",
                "refund": "0",
                "nullifier": str(nullifier),
                "secret": str(secret),
                "pathElements": prepared["pathElements"],
                "pathIndices": prepared["pathIndices"],
            }
            (tmp / "input.json").write_text(json.dumps(inp))
            subprocess.run([
                "snarkjs", "wtns", "calculate",
                str(WASM), str(tmp / "input.json"), str(tmp / "witness.wtns"),
            ], check=True, capture_output=True)
            subprocess.run([
                "snarkjs", "groth16", "prove",
                str(ZKEY), str(tmp / "witness.wtns"),
                str(tmp / "proof.json"), str(tmp / "public.json"),
            ], check=True, capture_output=True)
            proof = json.loads((tmp / "proof.json").read_text())
            public_signals = json.loads((tmp / "public.json").read_text())

        pa, pb, pc = _proof_to_solidity(proof, public_signals)
        root_bytes = int(prepared["root"]).to_bytes(32, "big")
        nh_bytes = int(prepared["nullifierHash"]).to_bytes(32, "big")

        charlie_before = w3.eth.get_balance(charlie)
        _send(w3, tornado.functions.withdraw(pa, pb, pc, root_bytes, nh_bytes, charlie, 0, 0),
              bob, bob_key)
        charlie_delta = w3.eth.get_balance(charlie) - charlie_before
        print(
            f"  ZK laundering demo: alice deposited 1 ETH, bob submitted withdraw,"
            f" charlie received {charlie_delta / 10**18:.4f} ETH"
        )

        # --- Bridge ---
        bridge = w3.eth.contract(
            address=_send(
                w3, w3.eth.contract(abi=bridge_abi, bytecode=bridge_bytecode).constructor(usdt.address),
                deployer, key,
            ).contractAddress,
            abi=bridge_abi,
        )
        print(f"MockBridge:         {bridge.address}")
        dave = node.accounts[4]
        _send(w3, usdt.functions.mint(alice, BRIDGE_DEMO_AMOUNT), alice, alice_key, gas=200_000)
        _send(w3, usdt.functions.approve(bridge.address, BRIDGE_DEMO_AMOUNT), alice, alice_key, gas=200_000)
        _send(w3, bridge.functions.lockUSDT(BRIDGE_DEMO_AMOUNT, TRON_DEST_DEMO), alice, alice_key)
        _send(w3, bridge.functions.releaseUSDT(1, dave, BRIDGE_DEMO_AMOUNT), deployer, key)
        print(
            f"  bridge demo:        alice locked {BRIDGE_DEMO_AMOUNT / 10**6:,.0f} USDT,"
            f" dave received {usdt.functions.balanceOf(dave).call() / 10**6:,.0f} USDT"
        )

        print("\nDeployment OK. ZK MockTornado operational. Anvil tears down on exit.")


if __name__ == "__main__":
    main()
