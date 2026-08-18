"""Manual recovery of ETH stuck in the Tornado mixer from a persisted notes file.

Reads a mixer_notes.jsonl (written by ToolDispatcher when notes_file is set)
and, for each note whose nullifier hasn't been spent on-chain yet, executes
mixer_withdraw sending 1 ETH to a recovery recipient (default: the deployer).

Motivated by the 2026-08-18 seed 500 incident on Sepolia:
  - 10 mixer deposits emitted successfully on-chain
  - 9 mixer_withdraw calls failed inside the sub-agent (Merkle root desync)
  - Sub-agent context was lost after the failures → 9 notes irrecoverable
  - Runner now persists notes at deposit-time (fix 2026-08-18)

Usage:
  python scripts/mixer_recover.py <run-dir> [--recipient 0x...]
  python scripts/mixer_recover.py <run-dir> --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

from aml.attackers.tools import ToolDispatcher

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENTS_JSON = REPO_ROOT / "deployments" / "sepolia.json"
USDT_ABI_PATH = REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json"
TORNADO_ABI_PATH = REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path,
                        help="Path to a campaign run dir containing mixer_notes.jsonl")
    parser.add_argument("--recipient", default=None,
                        help="Address to receive the reclaimed ETH (default: deployer)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List recoverable notes without executing withdrawals")
    args = parser.parse_args()

    notes_file = args.run_dir / "mixer_notes.jsonl"
    if not notes_file.exists():
        raise SystemExit(
            f"missing {notes_file} — this run predates the notes persistence "
            f"fix (2026-08-18), or ToolDispatcher was constructed without "
            f"notes_file. The mixer ETH from this run cannot be recovered."
        )

    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(REPO_ROOT / ".env.sepolia", override=True)
    rpc = os.environ["SEPOLIA_RPC_URL"]
    logs_rpc = os.environ.get(
        "SEPOLIA_LOGS_RPC_URL",
        "https://ethereum-sepolia-rpc.publicnode.com",
    )
    deployer_key = os.environ.get("SEPOLIA_DEPLOYER_PRIVATE_KEY")
    if deployer_key is None:
        raise SystemExit("SEPOLIA_DEPLOYER_PRIVATE_KEY not set")

    w3 = Web3(Web3.HTTPProvider(rpc))
    if w3.eth.chain_id != 11155111:
        raise SystemExit(f"wrong chain: {w3.eth.chain_id}")

    deployment = json.loads(DEPLOYMENTS_JSON.read_text())
    tornado_addr = deployment["contracts"]["MockTornado"]
    usdt_addr = deployment["contracts"]["MockUSDT"]
    tornado_deploy_block = int(deployment.get("tornado_deploy_block", 0))
    tornado = w3.eth.contract(
        address=tornado_addr,
        abi=json.loads(TORNADO_ABI_PATH.read_text())["abi"],
    )
    usdt = w3.eth.contract(
        address=usdt_addr,
        abi=json.loads(USDT_ABI_PATH.read_text())["abi"],
    )

    from eth_account import Account
    deployer_addr = Account.from_key(deployer_key).address
    recipient = args.recipient or deployer_addr

    print(f"Recovery run: {args.run_dir.name}")
    print(f"Recipient:    {recipient}")
    print(f"Deployer bal: {w3.eth.get_balance(deployer_addr)/1e18:.4f} ETH")
    print()

    notes = [json.loads(l) for l in notes_file.open() if l.strip()]
    print(f"Found {len(notes)} persisted notes.")

    # Load wallets from wallets_keys.json so mixer_withdraw can sign
    # (needs gas_payer key). If missing, use deployer for everything.
    wallets_file = args.run_dir / "wallets_keys.json"
    if wallets_file.exists():
        wallets_data = json.loads(wallets_file.read_text())
        wallets = wallets_data["wallets"]
    else:
        wallets = {deployer_addr: deployer_key}
    if deployer_addr not in wallets:
        wallets[deployer_addr] = deployer_key

    dispatcher = ToolDispatcher(
        w3=w3, usdt_contract=usdt, wallets=wallets,
        pool_contract=None, tornado_contract=tornado,
        mixer_events_from_block=tornado_deploy_block,
        logs_rpc_url=logs_rpc,
    )

    ok = 0
    already_spent = 0
    failed = 0
    for i, entry in enumerate(notes, 1):
        note = entry.get("note")
        if not note:
            continue
        # Check if nullifier already spent
        try:
            nullifier_int, secret_int = note.split(":", 2)[1:]
            from aml.attackers.tools import _run_zk_helper
            nh_int = int(_run_zk_helper("mimc", str(int(nullifier_int, 16))))
            nh_bytes = nh_int.to_bytes(32, "big")
            spent = tornado.functions.isSpent(nh_bytes).call()
        except Exception as e:
            print(f"[{i}] {note[:60]}...  ERROR checking spent state: {e}")
            failed += 1
            continue
        if spent:
            already_spent += 1
            print(f"[{i}] {note[:60]}...  already withdrawn")
            continue

        print(f"[{i}] {note[:60]}...  attempting withdrawal to {recipient[:12]}...")
        if args.dry_run:
            continue

        result = dispatcher.dispatch(
            "mixer_withdraw",
            {"deposit_note": note, "recipient": recipient,
             "gas_payer": deployer_addr},
        )
        if result.error:
            print(f"    FAILED: {result.error[:200]}")
            failed += 1
        else:
            print(f"    OK: tx={result.output.get('tx_hash')}")
            ok += 1
        time.sleep(1)   # rate limit between withdraws

    print()
    print(f"=== Recovery summary ===")
    print(f"  Notes total:     {len(notes)}")
    print(f"  Already spent:   {already_spent}")
    print(f"  Recovered:       {ok}  (= {ok} ETH reclaimed)")
    print(f"  Failed:          {failed}")
    if not args.dry_run:
        print(f"  Deployer balance AFTER: "
              f"{w3.eth.get_balance(deployer_addr)/1e18:.4f} ETH")


if __name__ == "__main__":
    main()
