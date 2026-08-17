"""Chain trace extraction — walks a chain's block history and emits one
record per tx with decoded events, in a JSON-serialisable form.

This is the format the detector trains on: a flat list of tx records,
each with from/to/value/gas/status + an `events` list of decoded
ERC-20/swap/mixer log entries. Same schema for attacker runs (via
aml.attackers.run_campaign) and benign runs (via
aml.detectors.run_benign); the dataset combiner stitches them together
without knowing which source produced which file.
"""
from __future__ import annotations

from typing import Any


def jsonable(v: Any) -> Any:
    """Coerce web3.py / bytes / huge-int values into JSON-safe types.

    Recursive over dicts / lists / tuples. Huge ints (>2^53) are
    stringified to dodge JS-side int overflow in any downstream consumer
    that uses JSON.parse.

    The `hasattr(v, "hex")` branch is meant for web3.py `HexBytes` and
    similar — we EXCLUDE str/int/float/bool from it because Python's
    built-in numeric types also have a `.hex()` method that produces
    a hex *representation of the bits*, not a chain-trace hex string
    (e.g. `(1.5).hex() == '0x1.8000000000000p+0'`). Those types should
    pass through unchanged.
    """
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()
    if hasattr(v, "hex") and not isinstance(v, (str, int, float, bool)):
        # web3.py HexBytes etc. — call .hex() to get a hex string
        try:
            return v.hex()
        except Exception:   # noqa: BLE001 — fall through to other handlers
            pass
    if isinstance(v, int) and abs(v) > 2**53:
        return str(v)
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def decode_event(log, known_contracts: dict[str, Any]) -> dict | None:
    """Try to decode a log against each known contract; return event dict or None.

    Walks the contract ABI directly (stable across web3.py versions,
    unlike iterating `contract.events` which has different semantics in
    v6 vs v7). For each event ABI entry, tries to process the log; on the
    first match returns the decoded fields.

    `known_contracts` maps a short label ("usdt", "pool", "tornado") to
    a web3.py contract handle (or None — skipped). The label survives
    into the output, so downstream consumers can group events by
    contract without re-looking up addresses.
    """
    log_addr = log.address.lower()
    for label, contract in known_contracts.items():
        if contract is None or contract.address.lower() != log_addr:
            continue
        for abi_item in contract.abi:
            if abi_item.get("type") != "event":
                continue
            event_name = abi_item.get("name")
            if not event_name:
                continue
            try:
                event_obj = getattr(contract.events, event_name)()
                ev = event_obj.process_log(log)
            except Exception:   # noqa: BLE001 — wrong event ABI or anon event
                continue
            return {
                "contract": label,
                "event": ev["event"],
                "args": jsonable(dict(ev["args"])),
            }
    return None


def extract_chain_trace(
    w3, end_block: int, known_contracts: dict[str, Any],
    start_block: int = 0,
) -> list[dict]:
    """Walk every tx in blocks [start_block, end_block]; emit one record per tx.

    start_block defaults to 0 (Anvil-compatible: walks from genesis). For
    live testnets (Sepolia, mainnet forks) callers MUST pass a non-zero
    start_block — walking from genesis on public chains would iterate
    millions of unrelated blocks.

    Each record fields:
      tx_hash    hex hash, 0x-prefixed
      block      block number
      from       sender address
      to         recipient address (or None for contract-creation txs)
      value_wei  stringified wei amount (str avoids 53-bit JSON int limit)
      value_eth  same in ETH (float, may lose precision; for human display)
      gas_used   from receipt
      status     1 = success, 0 = reverted
      events     list of decoded events matching `known_contracts`

    Suitable for direct json.dumps line-by-line into a .jsonl file.
    """
    trace: list[dict] = []
    for block_num in range(start_block, end_block + 1):
        block = w3.eth.get_block(block_num, full_transactions=True)
        for tx in block.transactions:
            try:
                receipt = w3.eth.get_transaction_receipt(tx.hash)
            except Exception:   # noqa: BLE001 — should never happen on local Anvil
                continue
            events = []
            for log in receipt.logs:
                ev = decode_event(log, known_contracts)
                if ev is not None:
                    events.append(ev)
            trace.append({
                "tx_hash": tx.hash.hex(),
                "block": block_num,
                "from": tx["from"],
                "to": tx.to,
                "value_wei": str(tx.value),
                "value_eth": tx.value / 10**18,
                "gas_used": receipt.gasUsed,
                "status": receipt.status,
                "events": events,
            })
    return trace
