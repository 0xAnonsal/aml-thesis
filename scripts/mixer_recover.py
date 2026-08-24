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

    # Primary source: mixer_notes.jsonl (written by ToolDispatcher at deposit).
    # Fallback source: sub_agent_transcripts.json (written by the runner —
    # tool_calls include the deposit_note output). Both are additive; we
    # deduplicate by the note string so no withdrawal is attempted twice.
    notes_file = args.run_dir / "mixer_notes.jsonl"
    transcripts_file = args.run_dir / "sub_agent_transcripts.json"
    if not notes_file.exists() and not transcripts_file.exists():
        raise SystemExit(
            f"neither {notes_file.name} nor {transcripts_file.name} exists "
            f"in {args.run_dir}. This run predates BOTH the notes-persistence "
            f"fix (2026-08-18) and the sub-agent transcripts fix "
            f"(2026-08-20). The mixer ETH from this run cannot be recovered."
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
    # Recipient defaults to a placeholder — after we bootstrap the gas_payer
    # below we'll reuse it as recipient (dispatcher's deployer guardrail
    # forbids the deployer from appearing as recipient too). After all
    # withdraws land, we do a plain transfer gas_payer → deployer to
    # consolidate.
    recipient = args.recipient   # None → use gas_payer, filled in below

    print(f"Recovery run: {args.run_dir.name}")
    print(f"Deployer bal: {w3.eth.get_balance(deployer_addr)/1e18:.4f} ETH")
    print()

    notes = []
    seen: set[str] = set()

    def _add(entry: dict, source: str) -> None:
        n = entry.get("note") or ""
        if not n or n in seen:
            return
        seen.add(n)
        entry.setdefault("_source", source)
        notes.append(entry)

    if notes_file.exists():
        for line in notes_file.open():
            line = line.strip()
            if not line:
                continue
            _add(json.loads(line), "mixer_notes.jsonl")

    # Fallback / supplement: extract deposit_note from tool_call outputs.
    if transcripts_file.exists():
        transcripts = json.loads(transcripts_file.read_text())
        for tr in transcripts:
            for tc in tr.get("tool_calls", []) or []:
                if tc.get("name") not in ("mixer_deposit", "mixer_batch_deposit"):
                    continue
                out = tc.get("output") or {}
                # mixer_deposit → out['deposit_note']
                # mixer_batch_deposit → out['deposits'][i]['deposit_note']
                singles: list[dict] = []
                if isinstance(out, dict):
                    if "deposit_note" in out:
                        singles.append(out)
                    for d in out.get("deposits", []) or []:
                        if isinstance(d, dict) and "deposit_note" in d:
                            singles.append(d)
                for d in singles:
                    _add({
                        "note": d.get("deposit_note"),
                        "tx_hash": d.get("tx_hash"),
                        "from_address": d.get("from_address"),
                        "leaf_index": d.get("leaf_index"),
                    }, "sub_agent_transcripts.json")

    print(f"Found {len(notes)} unique persisted notes "
          f"({sum(1 for n in notes if n.get('_source') == 'mixer_notes.jsonl')} "
          f"from notes_file, "
          f"{sum(1 for n in notes if n.get('_source') == 'sub_agent_transcripts.json')} "
          f"from transcripts).")

    # Load wallets from wallets_keys.jsonl (write-through log, preferred —
    # crash-safe) or wallets_keys.json (legacy end-of-run snapshot).
    wallets: dict[str, str] = {}
    wallets_jsonl = args.run_dir / "wallets_keys.jsonl"
    wallets_json = args.run_dir / "wallets_keys.json"
    if wallets_jsonl.exists():
        for line in wallets_jsonl.open():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            wallets[row["address"]] = row["private_key"]
    elif wallets_json.exists():
        wallets_data = json.loads(wallets_json.read_text())
        wallets.update(wallets_data["wallets"])
    if deployer_addr not in wallets:
        wallets[deployer_addr] = deployer_key

    # Pick a non-deployer wallet as gas_payer — the dispatcher's guardrail
    # forbids the deployer from appearing anywhere in a mixer operation.
    # We need a wallet with enough ETH to cover the withdraw gas (~0.001 ETH).
    # If none exists in the run's wallets, fund a fresh one from the deployer.
    MIN_GAS_ETH = int(2e15)   # 0.002 ETH — covers 200k-gas withdraw at 10 gwei
    gas_payer = None
    for addr in wallets:
        if addr == deployer_addr:
            continue
        try:
            if w3.eth.get_balance(addr) >= MIN_GAS_ETH:
                gas_payer = addr
                break
        except Exception:   # noqa: BLE001
            continue
    if gas_payer is None:
        # Bootstrap a fresh gas_payer from the deployer.
        from eth_account import Account as _Account
        _acct = _Account.create()
        print(f"[recover] no wallet in run has ≥ 0.002 ETH gas — bootstrapping "
              f"fresh gas_payer {_acct.address} with 0.005 ETH from deployer")
        tx = {"from": deployer_addr, "to": _acct.address,
              "value": int(5e15),
              "nonce": w3.eth.get_transaction_count(deployer_addr),
              "gas": 21000, "gasPrice": w3.eth.gas_price,
              "chainId": 11155111}
        signed = w3.eth.account.sign_transaction(tx, private_key=deployer_key)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw))
        wallets[_acct.address] = _acct.key.hex()
        gas_payer = _acct.address
    # If no explicit --recipient given, reuse gas_payer as the withdraw
    # destination. After all withdraws we sweep the accumulated ETH from
    # gas_payer back to the deployer via a plain transfer.
    if recipient is None:
        recipient = gas_payer
    print(f"[recover] gas_payer: {gas_payer[:10]}... "
          f"(balance {w3.eth.get_balance(gas_payer)/1e18:.6f} ETH)")
    print(f"[recover] recipient: {recipient[:10]}...")

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
            # Contract exposes `nullifierHashes(bytes32) → bool` via the
            # auto-generated public mapping getter (no explicit isSpent).
            spent = tornado.functions.nullifierHashes(nh_bytes).call()
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
             "gas_payer": gas_payer},
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

    # Consolidation phase: sweep recipient wallet back to deployer if:
    #   (a) it isn't the deployer itself (user didn't override --recipient),
    #   (b) it holds any ETH.
    if (not args.dry_run) and recipient != deployer_addr and recipient in wallets:
        r_bal = w3.eth.get_balance(recipient)
        gas_est = int(21000 * w3.eth.gas_price)
        if r_bal > gas_est:
            amount = r_bal - gas_est
            print()
            print(f"[consolidation] sweeping recipient {recipient[:10]}... "
                  f"({r_bal/1e18:.6f} ETH) → deployer")
            tx = {
                "from": recipient, "to": deployer_addr, "value": amount,
                "nonce": w3.eth.get_transaction_count(recipient),
                "gas": 21000, "gasPrice": w3.eth.gas_price,
                "chainId": 11155111,
            }
            signed = w3.eth.account.sign_transaction(
                tx, private_key=wallets[recipient],
            )
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            r = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw))
            print(f"  sweep tx: {r.transactionHash.hex()} "
                  f"({amount/1e18:.6f} ETH consolidated)")

    if not args.dry_run:
        print(f"  Deployer balance AFTER: "
              f"{w3.eth.get_balance(deployer_addr)/1e18:.4f} ETH")


if __name__ == "__main__":
    main()
