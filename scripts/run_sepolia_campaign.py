"""Sepolia campaign runner — Task #7 mini-campaign against deployed contracts.

Adapts src/aml/attackers/run_campaign.py from AnvilNode (ephemeral local)
to Sepolia (persistent testnet). Reuses everything downstream:
- Coordinator + sub-agents from src/aml/attackers/coordinator.py
- ToolDispatcher + 17 tools from src/aml/attackers/tools.py
- Scenarios from src/aml/attackers/scenarios.py
- Chain trace extractor from src/aml/chains/trace.py

Key differences vs Anvil runner:
1. Loads env from .env.sepolia (RPC + deployer key).
2. Loads pre-deployed contract addresses from deployments/sepolia.json
   (no re-deploy — reuses the contracts deployed 2026-08-11).
3. Funds a fresh alice wallet from deployer with configurable ETH amount.
4. Installs a `min_gas_price` Web3 middleware that raises the returned
   value from `eth_gasPrice` to at least MIN_GAS_PRICE_GWEI. This is
   critical because tools.py uses legacy `gasPrice = w3.eth.gas_price`
   in 40+ sites, and Sepolia RPC providers (Alchemy, Infura) frequently
   return values <=1 gwei that are insufficient for reliable inclusion
   (observed during the 2026-08-11 MiMC deploy incident).
5. Extracts on-chain trace via block range instead of "since deploy".

Usage:
    python scripts/run_sepolia_campaign.py --scenario defi-exploit \\
        --amount 3.0 --model sonnet --out results/sepolia_campaign/

For a smaller/safer test:
    python scripts/run_sepolia_campaign.py --scenario defi-exploit \\
        --amount 1.0 --model haiku --out results/sepolia_campaign/

Estimated wall-clock for `defi-exploit 3 ETH sonnet`: ~30-45 min.
Estimated cost: ~$0.30-0.60 LLM (Sonnet) or ~$0.05-0.10 (Haiku).
Estimated ETH spend from deployer: ~1.5 ETH (funds alice + gas).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

from aml.attackers import Coordinator, LLMClient, ToolDispatcher
from aml.attackers.scenarios import SCENARIOS
from aml.attackers.funder_sizing import allocate_funder_amounts
from aml.attackers.tools import _DEFAULT_GAS_RESERVE_ETH
from aml.chains.trace import extract_chain_trace, jsonable
from aml.env import (
    PriceOracle, build_market_context,
    ensure_fresh_prices, resolve_campaign_ts,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PRICE_CACHE = REPO_ROOT / "data" / "prices"
DEPLOYMENTS_JSON = REPO_ROOT / "deployments" / "sepolia.json"

# Sepolia gas — override w3.eth.gas_price to at least this floor. Prevents
# the legacy `w3.eth.gas_price` calls in tools.py from returning the
# Alchemy default (~1 gwei) which is insufficient for reliable inclusion.
MIN_GAS_PRICE_GWEI = 3


class _TokenBucket:
    """Simple client-side rate limiter (token bucket algorithm).

    Blocks callers when the token bucket is empty so we NEVER exceed
    `rate` requests-per-second on our side. Alchemy free tier is
    300 req/s; we self-throttle to ~200 to leave headroom.

    Root cause (2026-08-24 seed 504 Alchemy overage): the previous
    RetryingHTTPProvider had no client-side throttling. Under Integration
    phase burst (~200 req/s legitimate + retries on 429), effective load
    hit 518 req/s on Alchemy — the retry loop AMPLIFIED the overage
    because 429 responses triggered 5 more retries each. Now we
    self-throttle BEFORE hitting the network so 429s become rare, and
    we no longer retry on 429 specifically (see _make_retrying_session).
    """

    def __init__(self, rate: float, capacity: float | None = None):
        import threading
        import time as _time
        self._rate = float(rate)
        self._capacity = float(capacity if capacity is not None else rate)
        self._tokens = self._capacity
        self._last = _time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        """Block until `tokens` are available in the bucket."""
        import time as _time
        while True:
            with self._lock:
                now = _time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(
                    self._capacity, self._tokens + elapsed * self._rate,
                )
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                shortfall = tokens - self._tokens
                sleep_for = shortfall / self._rate
            _time.sleep(sleep_for)


# Global rate limiter shared across all Web3 provider instances in the
# campaign. Sized for Alchemy free tier (300 req/s) with 30% headroom.
# capacity=20 means we allow small legitimate bursts (e.g. 20 balance
# reads back-to-back) but sustained rate stays under 200 req/s.
_RPC_RATE_LIMITER = _TokenBucket(rate=200.0, capacity=20.0)


def _make_retrying_session():
    """requests.Session with client-side rate limiting + selective retry.

    Handles transient RPC failures without amplifying rate-limit overages:
      - Rate limit ourselves to ~200 req/s (Alchemy free tier is 300;
        keeps 30% headroom for LLM/Etherscan/other traffic).
      - Retry ONLY on ConnectionError + 5xx (server-side transient),
        NOT on 429 (rate limit). If Alchemy says slow down we respect
        it instead of hammering.
      - Preserves `RemoteDisconnected` recovery from seed 502.

    Root cause of the 2026-08-24 seed 504 Alchemy overage (518/300 req/s):
    aggressive retry on 429 turned normal rate-limit pushback into a
    snowball. Fix: throttle client-side + don't retry 429s.
    """
    from requests import Session
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retry = Retry(
        total=5,
        backoff_factor=1.0,             # 1s, 2s, 4s, 8s, 16s
        # Removed 429 — respect explicit rate-limit signals instead
        # of retrying and amplifying the overage.
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["POST", "GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8,
                          pool_maxsize=16)
    s = Session()
    s.mount("http://", adapter)
    s.mount("https://", adapter)

    # Client-side rate limit: hook every request through the token bucket
    # BEFORE it hits the wire. Blocking sleep in the calling thread —
    # simple, no async needed.
    _orig_send = s.send

    def _throttled_send(request, **kw):
        _RPC_RATE_LIMITER.acquire()
        return _orig_send(request, **kw)

    s.send = _throttled_send
    return s


# Contract identifier -> Foundry artifact path (for ABI loading).
ARTIFACT_PATHS = {
    "MockUSDT": REPO_ROOT / "out" / "MockUSDT.sol" / "MockUSDT.json",
    "MockUniswapV2Pool": REPO_ROOT / "out" / "MockUniswapV2Pool.sol" / "MockUniswapV2Pool.json",
    "MockTornado": REPO_ROOT / "out" / "MockTornado.sol" / "MockTornado.json",
    "MockBridge": REPO_ROOT / "out" / "MockBridge.sol" / "MockBridge.json",
}


def install_gas_floor_middleware(w3: Web3, min_gwei: int) -> None:
    """Raise eth_gasPrice responses to at least `min_gwei`.

    Sepolia RPC providers routinely return gas price suggestions barely
    above base fee, producing txs that stall in mempool. Since tools.py
    uses legacy `gasPrice = w3.eth.gas_price` in ~40 sites, patching the
    middleware layer is the least invasive fix. Web3.py v7 uses class-
    based middleware (Web3Middleware); this installs a subclass that
    overrides response_processor for the eth_gasPrice method.
    """
    from web3.middleware import Web3Middleware
    min_wei = w3.to_wei(min_gwei, "gwei")

    class GasFloorMiddleware(Web3Middleware):
        def response_processor(self, method, response):
            if method == "eth_gasPrice" and isinstance(response, dict) and "result" in response:
                try:
                    current = int(response["result"], 16)
                    if current < min_wei:
                        response["result"] = hex(min_wei)
                except (ValueError, TypeError):
                    pass
            return response

    w3.middleware_onion.add(GasFloorMiddleware, name="gas_floor")


def _redact_rpc(rpc: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(rpc)
    parts = [x for x in p.path.split("/") if x]
    if parts:
        parts[-1] = "<redacted>"
    return f"{p.scheme}://{p.netloc}/" + "/".join(parts)


def load_sepolia_env() -> tuple[str, str]:
    # Load .env first (ANTHROPIC_API_KEY + shared secrets), then .env.sepolia
    # (chain-specific overrides). Chain-specific keys take precedence.
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(REPO_ROOT / ".env.sepolia", override=True)
    rpc = os.environ.get("SEPOLIA_RPC_URL")
    key = os.environ.get("SEPOLIA_DEPLOYER_PRIVATE_KEY")
    if not rpc:
        raise SystemExit("SEPOLIA_RPC_URL not set")
    if not key:
        raise SystemExit("SEPOLIA_DEPLOYER_PRIVATE_KEY not set")
    if not key.startswith("0x"):
        key = "0x" + key
    return rpc, key


def load_deployed_contracts(w3: Web3):
    """Load pre-deployed Sepolia contracts as web3 handles.

    Returns the classic dict plus a `tornado_pools` sub-dict keyed by
    denomination_wei. The multi-denom family (0.1 / 1 / 10 ETH pools)
    is auto-detected from any deployment key matching MockTornado*.
    Falls back to just the singular MockTornado if no _NETH suffixed
    entries are present (legacy pre-multi-denom deployments).
    """
    if not DEPLOYMENTS_JSON.exists():
        raise SystemExit(
            f"Missing {DEPLOYMENTS_JSON}. Run scripts/deploy_eth_mocks_sepolia.py first."
        )
    deployment = json.loads(DEPLOYMENTS_JSON.read_text())
    addrs = deployment["contracts"]

    # Load ABIs once
    with ARTIFACT_PATHS["MockTornado"].open() as f:
        tornado_abi = json.load(f)["abi"]

    def load(name):
        with ARTIFACT_PATHS[name].open() as f:
            abi = json.load(f)["abi"]
        return w3.eth.contract(address=addrs[name], abi=abi)

    # Auto-discover ALL MockTornado* deployed contracts (main + multi-denom).
    # Each is keyed on-chain by its DENOMINATION() constant, which we read
    # with an eth_call to avoid having to parse the deployment key names.
    tornado_pools: dict[int, "web3.contract.Contract"] = {}
    for key, addr in addrs.items():
        if not key.startswith("MockTornado"):
            continue
        ct = w3.eth.contract(address=addr, abi=tornado_abi)
        try:
            denom = int(ct.functions.DENOMINATION().call())
        except Exception as e:   # noqa: BLE001
            print(f"[runner] WARNING: {key} ({addr}) has no DENOMINATION(): {e}",
                  file=sys.stderr)
            continue
        tornado_pools[denom] = ct

    contracts = {
        "usdt": load("MockUSDT"),
        "pool": load("MockUniswapV2Pool"),
        "tornado": load("MockTornado"),   # default / 1 ETH — legacy alias
        "bridge": load("MockBridge"),
        "tornado_pools": tornado_pools,
    }
    if tornado_pools:
        denoms_eth = sorted(d / 1e18 for d in tornado_pools.keys())
        print(f"[runner] tornado pools discovered: {denoms_eth} ETH",
              file=sys.stderr)
    return contracts


def load_tornado_deploy_block() -> int | None:
    """Read the recorded tornado deploy block from deployments/sepolia.json.

    Needed to seed `mixer_events_from_block` so that the client-side
    Merkle tree reconstruction sees every historical Deposit (not just
    the ones in the current campaign window). Returns None if the field
    is absent — callers should fall back to a safe pre-deployment floor.
    """
    if not DEPLOYMENTS_JSON.exists():
        return None
    deployment = json.loads(DEPLOYMENTS_JSON.read_text())
    val = deployment.get("tornado_deploy_block")
    return int(val) if val is not None else None


def _raw_tx(signed):
    return getattr(signed, "raw_transaction", None) or signed.rawTransaction


def fund_alice_from_deployer(w3: Web3, deployer: str, deployer_key: str,
                             eth_amount: float) -> tuple[str, str]:
    """Generate a fresh alice wallet + fund from deployer with `eth_amount` ETH."""
    acct = w3.eth.account.create()
    alice, alice_key = acct.address, acct.key.hex()

    value_wei = int(eth_amount * 10**18)
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas") or w3.eth.gas_price
    priority = w3.to_wei(MIN_GAS_PRICE_GWEI, "gwei")
    max_fee = base_fee * 2 + priority

    tx = {
        "from": deployer,
        "to": alice,
        "value": value_wei,
        "nonce": w3.eth.get_transaction_count(deployer),
        "gas": 21_000,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority,
        "chainId": w3.eth.chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, private_key=deployer_key)
    tx_hash = w3.eth.send_raw_transaction(_raw_tx(signed))
    print(f"[funding] alice={alice} funded with {eth_amount} ETH "
          f"(tx {tx_hash.hex()})", file=sys.stderr)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"alice funding tx reverted: {tx_hash.hex()}")

    return alice, alice_key


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", required=True, choices=list(SCENARIOS.keys()))
    parser.add_argument("--amount", type=float, default=None,
                        help="Amount to launder (default from scenario)")
    parser.add_argument("--model", default="haiku",
                        help="LLM model: haiku|sonnet|opus (default haiku)")
    parser.add_argument("--out", default="results/sepolia_campaign",
                        help="Output directory (default results/sepolia_campaign/)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Seed for reproducibility (default random)")
    parser.add_argument("--alice-funding-eth", type=float, default=None,
                        help=("ETH to fund alice with. Default = --amount "
                              "(exact loot, NO gas padding — realistic: "
                              "real thieves don't get extra gas from the "
                              "victim). Override only for controlled tests."))
    parser.add_argument("--max-iterations", type=int, default=60)
    parser.add_argument("--sub-agent-max-iterations", type=int, default=40)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument(
        "--num-funders", type=int, default=5,
        help="Intermediate funder pool size. 0 disables (deployer fund directly).",
    )
    parser.add_argument(
        "--funder-eth", type=float, default=None,
        help=(
            "Legacy override: fixed ETH per funder (all identical, "
            "count from --num-funders). Default None → tier-based "
            "sizing from aml.attackers.funder_sizing."
        ),
    )
    parser.add_argument(
        "--campaign-ts", type=str, default=None,
        help=(
            "Freeze the price oracle to a specific date (YYYY-MM-DD or "
            "ISO 8601). Default: now(UTC) clamped to the last cached day. "
            "Sepolia is wall-clock real, so the default reflects true "
            "market conditions — but if the cache is stale, run "
            "`python scripts/download_prices.py` first to pull today's "
            "prices before the campaign starts."
        ),
    )
    args = parser.parse_args()

    scenario = SCENARIOS[args.scenario]
    amount = args.amount if args.amount is not None else scenario.default_amount
    seed = args.seed if args.seed is not None else random.randint(1, 10**9)
    random.seed(seed)

    # Auto-refresh the CoinGecko cache at start-up so every Sepolia
    # campaign uses today's spot price. On refresh failure the helper
    # falls back to the stale cache with a warning (never crashes the
    # run). Sepolia is wall-clock real — running under stale prices
    # would corrupt the market_context anchor passed to the LLM.
    if args.campaign_ts is None:
        oracle = ensure_fresh_prices(PRICE_CACHE)
    else:
        # Explicit --campaign-ts means the user wants a reproducible
        # anchor; don't touch the cache in that case.
        oracle = PriceOracle(cache_dir=PRICE_CACHE)
    campaign_ts = resolve_campaign_ts(oracle, args.campaign_ts)
    print(
        f"[runner] oracle: campaign_ts={campaign_ts.isoformat()} "
        f"eth=${oracle.price('eth', campaign_ts):,.2f} "
        f"usdt=${oracle.price('usdt', campaign_ts):.4f}",
        file=sys.stderr,
    )

    # Alice funding = stolen amount, PERIOD. All gas (her outbound txs
    # AND the gas-seed dust of every burner created during the campaign
    # since Alice is the dispatcher's gas_payer_address) comes out of
    # this exact budget. Realism: a real hack pays every satoshi of gas
    # from stolen funds — there is no benevolent gas margin. If Alice
    # runs out, the LLM must adapt (fewer mixer deposits, smaller
    # denominations, less parallel routing). honest_recovery is
    # denominated against this same amount, so the metric reflects the
    # true economic cost of laundering including gas overhead.
    alice_funding = (
        args.alice_funding_eth if args.alice_funding_eth is not None
        else amount
    )

    rpc, deployer_key = load_sepolia_env()
    # Retry-enabled HTTP session survives transient RPC disconnects
    # (dropped keep-alive sockets, 429/5xx bursts) that would otherwise
    # kill a long-running campaign mid-flight.
    w3 = Web3(Web3.HTTPProvider(rpc, session=_make_retrying_session()))
    install_gas_floor_middleware(w3, MIN_GAS_PRICE_GWEI)

    if w3.eth.chain_id != 11155111:
        raise SystemExit(f"Wrong chain: expected Sepolia (11155111), got {w3.eth.chain_id}")

    deployer = w3.eth.account.from_key(deployer_key).address
    deployer_balance = w3.eth.get_balance(deployer) / 10**18

    if deployer_balance < alice_funding + 0.05:
        raise SystemExit(
            f"Deployer balance {deployer_balance:.4f} ETH insufficient — "
            f"need >= {alice_funding + 0.05:.2f} ETH (alice funding + 0.05 gas)."
        )

    contracts = load_deployed_contracts(w3)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    run_name = f"{timestamp}_{scenario.name}_seed{seed}_sepolia"
    out_dir = Path(args.out) / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=" * 70, file=sys.stderr)
    print(f"[runner] SEPOLIA CAMPAIGN — {run_name}", file=sys.stderr)
    print(f"  RPC:          {_redact_rpc(rpc)}", file=sys.stderr)
    print(f"  chain_id:     {w3.eth.chain_id}", file=sys.stderr)
    print(f"  deployer:     {deployer}", file=sys.stderr)
    print(f"  balance:      {deployer_balance:.4f} ETH", file=sys.stderr)
    print(f"  scenario:     {scenario.name}", file=sys.stderr)
    print(f"  amount:       {amount} {scenario.asset.upper()}", file=sys.stderr)
    print(f"  alice funds:  {alice_funding} ETH", file=sys.stderr)
    print(f"  model:        {args.model}", file=sys.stderr)
    print(f"  seed:         {seed}", file=sys.stderr)
    print(f"  out_dir:      {out_dir}", file=sys.stderr)
    print(f"  gas floor:    {MIN_GAS_PRICE_GWEI} gwei", file=sys.stderr)
    print(f"  contracts:", file=sys.stderr)
    for k, v in contracts.items():
        if hasattr(v, "address"):
            print(f"    {k:8s} {v.address}", file=sys.stderr)
        elif isinstance(v, dict):
            # tornado_pools: {denomination_wei: contract}
            for denom_wei, ct in sorted(v.items()):
                denom_eth = denom_wei / 1e18
                print(
                    f"    {k}[{denom_eth:g} ETH]  {ct.address}",
                    file=sys.stderr,
                )
    print(f"=" * 70, file=sys.stderr)

    start_wall = time.time()
    start_block = w3.eth.block_number
    print(f"[runner] campaign starting at block {start_block}", file=sys.stderr)

    # Fund alice
    alice, alice_key = fund_alice_from_deployer(w3, deployer, deployer_key, alice_funding)
    print(f"[runner] alice funded, starting Coordinator...", file=sys.stderr)

    # Build dispatcher with pre-deployed contracts. mixer_events_from_block
    # MUST cover the full history since tornado deploy — the mixer contract
    # is shared across campaigns, so a fresh withdraw needs to see every
    # historical Deposit to reconstruct the on-chain Merkle tree correctly.
    # A window that misses past deposits produces a stale local root and
    # `isKnownRoot()` rejects the proof (root cause of the 2026-08-12
    # seed200 mixer_withdraw failure). `_mixer_collect_leaves` in tools.py
    # paginates the scan in 500-block chunks so covering ~10k blocks of
    # history is cheap and stays under Alchemy's per-request limit.
    tornado_deploy_block = load_tornado_deploy_block()
    if tornado_deploy_block is None:
        # Fallback: safe floor before any of our Sepolia deploys.
        tornado_deploy_block = max(0, start_block - 20000)
    # Alchemy free tier caps eth_getLogs at 10 blocks/request — unusable
    # for scanning ~12k blocks of mixer history. publicnode.com allows
    # 10k-block ranges free, and we only use it for the paginated log
    # scan (tx sending etc. still go via Alchemy). Override via
    # SEPOLIA_LOGS_RPC_URL if you have a paid provider.
    logs_rpc_url = os.environ.get(
        "SEPOLIA_LOGS_RPC_URL",
        "https://ethereum-sepolia-rpc.publicnode.com",
    )
    usd_stolen_preview = oracle.usd_value(amount, scenario.asset, campaign_ts)
    # Multi-denom mixer wiring: pass the full pool dict if the scenario
    # uses the tornado mixer and deployments has multiple pools. Falls
    # back to the singular tornado_contract if only the 1 ETH pool
    # exists (legacy Sepolia deployments pre-multi-denom).
    tornado_kwargs = {}
    if scenario.needs_tornado:
        pools_dict = contracts.get("tornado_pools") or {}
        if len(pools_dict) >= 2:
            tornado_kwargs["tornado_pools"] = pools_dict
        else:
            tornado_kwargs["tornado_contract"] = contracts["tornado"]

    dispatcher = ToolDispatcher(
        w3=w3,
        usdt_contract=contracts["usdt"],
        wallets={deployer: deployer_key, alice: alice_key},
        pool_contract=contracts["pool"],
        **tornado_kwargs,
        mixer_events_from_block=tornado_deploy_block,
        logs_rpc_url=logs_rpc_url,
        laundering_target_usd=usd_stolen_preview,
        # Persist every mixer deposit note to disk immediately upon
        # confirmation. Safety net against the 2026-08-18 seed 500
        # incident where 9 ETH were locked in the mixer because the
        # sub-agent's context (holding the notes) was lost after the
        # Merkle-root-desync errors triggered a halt. Consumed by
        # scripts/mixer_recover.py for manual reclaim.
        notes_file=out_dir / "mixer_notes.jsonl",
        # Persist every burner private key to disk in the instant it is
        # generated (write-through wallet dict). Guarantees zero key-loss
        # even under process kill / power failure / OOM. The final
        # wallets_keys.json snapshot at end-of-run is a redundant summary;
        # this JSONL is the authoritative live log. Consumed by
        # scripts/sweep_sepolia.py.
        wallets_file=out_dir / "wallets_keys.jsonl",
        # Campaign-level cap on ETH locked in peel-chain sink wallets:
        # 5% of the laundering amount. The dispatcher's peel_chain refuses
        # calls whose worst-case projected loss would breach this budget,
        # so the LLM cannot accidentally strand more than 5% in sinks
        # even across multiple peel_chain invocations. Only applied when
        # the stolen asset is ETH (peel-budget is ETH-denominated).
        peel_budget_eth=(0.03 * amount) if scenario.asset == "eth" else None,
        # Alice pays for every internal gas-seed tx (realistic mode) so
        # honest_recovery reflects the true economic cost of laundering
        # from the criminal's own budget rather than a subsidised
        # infrastructure. Funder pool still exists as legacy fallback.
        gas_payer_address=alice,
    )
    # Multi-funder pool for gas obfuscation. On Sepolia we keep a smaller
    # bootstrap amount per funder to avoid burning deployer ETH — 0.2 ETH
    # Funder-pool sizing: tier-based lookup (aml.attackers.funder_sizing)
    # picks count and per-funder amounts from --amount. Legacy override:
    # --funder-eth + --num-funders (matches pre-2026-08-14 tests).
    if args.funder_eth is not None:
        funder_amounts = [args.funder_eth] * args.num_funders
    else:
        funder_amounts = allocate_funder_amounts(amount)
    funder_pool_total_eth = sum(funder_amounts)
    if funder_amounts:
        print(
            f"[runner] funder pool: {len(funder_amounts)} wallets, "
            f"{funder_pool_total_eth:.4f} ETH total "
            f"(${funder_pool_total_eth * oracle.price('eth', campaign_ts):,.2f}), "
            f"per-funder [{min(funder_amounts):.4f} .. "
            f"{max(funder_amounts):.4f}]",
            file=sys.stderr,
        )
        print(
            f"[runner] funder amounts: "
            f"{['%.4f' % x for x in funder_amounts]}",
            file=sys.stderr,
        )
        dispatcher.bootstrap_funder_pool(amounts_eth=funder_amounts)
    bootstrap_attacker_addrs = sorted(dispatcher.wallets.keys())

    # Adaptive sub-agent iteration cap: scales with campaign scale so
    # small tests (1.5 ETH) don't pay for iterations they won't use and
    # large Anvil campaigns (100+ ETH) don't cascade into 4+ Integration
    # delegations. Formula: min(150, max(25, int(20 + amount * 1.2))).
    # CLI --sub-agent-max-iterations always overrides.
    if args.sub_agent_max_iterations == 40:  # unchanged default
        adaptive_max = min(150, max(25, int(20 + amount * 1.2)))
    else:
        adaptive_max = args.sub_agent_max_iterations
    print(
        f"[runner] sub-agent max iterations (adaptive by amount "
        f"{amount} ETH): {adaptive_max}",
        file=sys.stderr,
    )

    # Run Coordinator
    coordinator = Coordinator(
        LLMClient(), dispatcher,
        model=args.model, sub_agent_model=args.model,
        max_iterations=args.max_iterations,
        sub_agent_max_iterations=adaptive_max,
    )
    prompt = (
        scenario.format_prompt(alice=alice, amount=amount)
        + "\n\n" + build_market_context(oracle, campaign_ts)
        + "\n\nTwo constraints when sizing legs from the USD thresholds "
        "above:\n"
        "(a) $999 is a HARD CAP per leg (sub-CTR compliance), NOT an "
        "efficiency target. The Layering system prompt requires "
        "TOPOLOGICAL VARIETY across burners (single-counterparty, "
        "hub-and-spoke, and chain-shaped mixes, with randomised amounts "
        "and edge-kind heterogeneity). MODERATE over-provisioning of "
        "burner count beyond the arithmetic minimum aids GNN "
        "evasion, not optional — a run that structures $10K into the "
        "theoretical minimum of ~11 burners is trivially learnable.\n"
        "(b) At end-of-campaign, aggressively consolidate remaining "
        "balances (dust in intermediate burners, gas-floor residues) "
        "before wrapping up. TARGET: total USD loss <5% including gas "
        "and pool slippage. Use reserve_eth=0 on final drains.\n\n"
        "The mock Uniswap pool trades at fixed 1 ETH = 2000 USDT for "
        "tractability but the FATF regime is USD, not pool-rate."
    )
    # Incremental transcript persistence: flush each sub-agent's transcript
    # to disk the moment it completes, so a mid-campaign crash (LLM hang,
    # RPC retention window, keyboard interrupt) does NOT lose the phases
    # that already finished. Complements the final sub_agent_transcripts.json
    # written at the end. See TFM §8.9.8 for the seed 502/503 loss that
    # motivated this.
    incremental_path = out_dir / "sub_agents_incremental.jsonl"

    def _flush_sub_agent(payload: dict) -> None:
        with incremental_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass

    result = coordinator.run(
        prompt,
        max_tokens=args.max_tokens,
        sub_agent_max_tokens=args.max_tokens,
        on_sub_agent_complete=_flush_sub_agent,
    )

    end_block = w3.eth.block_number
    wall_clock = time.time() - start_wall
    print(f"[runner] campaign done in {wall_clock:.1f}s "
          f"({wall_clock/60:.1f} min), stop={result.stopped_reason}, "
          f"cost=${result.cost_usd:.4f}, end block={end_block}",
          file=sys.stderr)

    # Post-campaign anti-strand rescue: any wallet stuck holding USDT
    # without gas gets topped up from the funder pool. Result goes into
    # campaign metadata so the thesis can cite empirical evidence.
    print("[runner] scanning for stranded wallets...", file=sys.stderr)
    strand_report = dispatcher.rescue_stranded_wallets()
    print(f"[runner] anti-strand: {strand_report['stranded_before']} found, "
          f"{strand_report['rescued']} rescued, "
          f"{strand_report['stranded_after']} still stranded", file=sys.stderr)
    if strand_report["stranded_after"] > 0:
        print(f"[runner] WARNING — {strand_report['stranded_after']} wallet(s) "
              f"could not be rescued: {strand_report['stranded_addresses']}",
              file=sys.stderr)

    # Sweep funder pool back to deployer so campaigns start+end at ~0.
    # True gas cost = initial pool - amount swept back.
    sweep_report = dispatcher.sweep_funder_pool(destination=deployer)
    gas_burned_by_funders_eth = (
        funder_pool_total_eth - sweep_report["total_returned_eth"]
    )
    print(
        f"[runner] funder sweep: {sweep_report['swept']}/"
        f"{sweep_report['num_funders']} returned "
        f"{sweep_report['total_returned_eth']:.4f} ETH to deployer. "
        f"True gas burned: {gas_burned_by_funders_eth:.4f} ETH.",
        file=sys.stderr,
    )

    # Post-run analysis
    all_attacker_addrs = sorted(dispatcher.wallets.keys())
    clean_exit_entries = list(dispatcher.registered_clean_exits)
    clean_exit_addrs = [e["address"] for e in clean_exit_entries]
    clean_exit_addr_set = set(clean_exit_addrs)
    new_burner_addrs = sorted(
        set(all_attacker_addrs) - set(bootstrap_attacker_addrs) - clean_exit_addr_set
    )

    gas_seed_wei = int(_DEFAULT_GAS_RESERVE_ETH * 10**18)
    clean_exit_records = []
    for entry in clean_exit_entries:
        addr = entry["address"]
        eth_final_wei = w3.eth.get_balance(addr)
        eth_received = max(0.0, (eth_final_wei - gas_seed_wei) / 10**18)
        usdt_final = contracts["usdt"].functions.balanceOf(addr).call()
        rec = {
            "address": addr,
            "exchange_platform": entry["exchange_platform"],
            "eth_received": eth_received,
            "usdt_received": usdt_final / 10**6,
        }
        if "note" in entry:
            rec["note"] = entry["note"]
        clean_exit_records.append(rec)

    addresses = {
        "attacker_wallets": all_attacker_addrs,
        "bootstrap_attackers": bootstrap_attacker_addrs,
        "burners_generated_during_campaign": new_burner_addrs,
        "source_wallet": alice,
        "clean_exit_wallets": clean_exit_addrs,
        "clean_exits_funded": [
            r["address"] for r in clean_exit_records
            if r["eth_received"] > 0 or r["usdt_received"] > 0
        ],
        "clean_exit_per_address": clean_exit_records,
        "operator_wallet": deployer,
        "contracts": {
            "usdt": contracts["usdt"].address,
            "pool": contracts["pool"].address,
            "tornado": contracts["tornado"].address,
            "bridge": contracts["bridge"].address,
        },
    }

    # Extract trace via block range (Sepolia: extract from start_block to end_block)
    print(f"[runner] extracting chain trace from block {start_block} to {end_block}...",
          file=sys.stderr)
    trace = extract_chain_trace(
        w3, end_block,
        known_contracts={
            "usdt": contracts["usdt"],
            "pool": contracts["pool"],
            "tornado": contracts["tornado"] if scenario.needs_tornado else None,
        },
        start_block=start_block,
    )
    print(f"[runner] {len(trace)} txs traced", file=sys.stderr)

    eth_price = oracle.price("eth", campaign_ts)
    usdt_price = oracle.price("usdt", campaign_ts)
    usd_stolen = oracle.usd_value(amount, scenario.asset, campaign_ts)
    usd_operating_capital = funder_pool_total_eth * eth_price
    usd_total_attacker_capital = usd_stolen + usd_operating_capital
    usd_gas_burned_by_funders = gas_burned_by_funders_eth * eth_price

    # Nominal (raw pool-priced) recovery — legacy metric. Overestimates
    # because MockUniswapV2Pool has no arbitrageurs, so its ETH/USDT
    # ratio drifts as the campaign runs. A run that swaps ETH -> USDT
    # against a distorted pool can extract more USDT than a real Uniswap
    # would ever give, inflating this figure to > 100 %.
    usd_to_exits = sum(
        r["eth_received"] * eth_price + r["usdt_received"] * usdt_price
        for r in clean_exit_records
    )
    recovery_pct_of_stolen = (100.0 * usd_to_exits / usd_stolen) if usd_stolen else 0.0
    recovery_pct_of_capital = (
        100.0 * usd_to_exits / usd_total_attacker_capital
        if usd_total_attacker_capital else 0.0
    )

    # HONEST recovery — mock-pool-distortion-adjusted. USDT is converted
    # to ETH-equivalent at the REAL market rate (CoinGecko oracle), then
    # CAPPED by the amount of ETH the campaign actually pushed into the
    # AMM pool. Any USDT beyond that cap is pool-exploitation, not real
    # laundering, and does NOT count as recovered value.
    eth_swapped_into_pool = 0.0
    for run in result.sub_agent_runs:
        for tc in run.tool_calls:
            if tc.get("name") != "swap_eth_for_usdt":
                continue
            out = tc.get("output") or {}
            if isinstance(out, dict) and not tc.get("is_error"):
                eth_swapped_into_pool += float(out.get("eth_paid") or 0)
    # ETH-equivalent of USDT at market rate (ignore mock pool ratio)
    eth_at_exits = sum(r["eth_received"] for r in clean_exit_records)
    usdt_at_exits = sum(r["usdt_received"] for r in clean_exit_records)
    usdt_as_eth_market = (usdt_at_exits * usdt_price / eth_price
                          if eth_price else 0.0)
    # Cap USDT-derived ETH by what was actually swapped in — anything
    # beyond that came from pool distortion, not from Alice's ETH.
    usdt_as_eth_capped = min(usdt_as_eth_market, eth_swapped_into_pool)
    honest_recovery_eth = eth_at_exits + usdt_as_eth_capped
    honest_recovery_usd = honest_recovery_eth * eth_price
    honest_recovery_pct = (100.0 * honest_recovery_usd / usd_stolen
                           if usd_stolen else 0.0)
    # Cap display at 100% — anything above is either operating-capital
    # sweep (documented separately) or mock-pool artifact.
    honest_recovery_pct_capped = min(honest_recovery_pct, 100.0)

    # FULL ETH RECONCILIATION — every wei accounted for.
    # Query on-chain balances of every campaign wallet NOW to see where
    # the ETH physically sits post-run. This is the ground truth for
    # "how much of alice_funding is where" and complements the
    # honest_recovery cap by exposing gas cost + burner residuals.
    def _bal_eth(addr: str) -> float:
        try:
            return w3.eth.get_balance(addr) / 1e18
        except Exception:
            return 0.0

    reconc_alice_now = _bal_eth(alice)
    reconc_exits_eth_now = sum(_bal_eth(a) for a in clean_exit_addrs)
    # Sum of ETH still sitting in ANY wallet the dispatcher registered
    # EXCEPT the deployer (chain infra) and alice/exits (counted above).
    campaign_wallets = set(dispatcher.wallets.keys())
    campaign_wallets.discard(deployer)
    campaign_wallets.discard(alice)
    for a in clean_exit_addrs:
        campaign_wallets.discard(a)
    reconc_burners_eth_now = sum(_bal_eth(a) for a in campaign_wallets)
    # ETH physically inside MockTornado + MockUniswapV2Pool contracts
    # attributable to THIS campaign: hard to attribute precisely without
    # tracing every deposit/withdraw, so we report the delta only via
    # accounting: alice_in - (alice_now + exits_eth_now + burners_now
    # + gas_burned + honest_recovery_eth).
    reconc_alice_in = float(alice_funding)
    reconc_accounted = (
        reconc_alice_now + reconc_exits_eth_now + reconc_burners_eth_now
    )
    # Gas burned = alice_funding - (still-recoverable ETH + honest_recovery)
    # The residual "went somewhere": mixer contract (recoverable via
    # mixer_recover.py), pool contract (via reverse-swap), or gas (lost).
    reconc_residual = reconc_alice_in - reconc_accounted - honest_recovery_eth

    meta = {
        "run_name": run_name,
        "scenario": scenario.name,
        "asset": scenario.asset,
        "amount": amount,
        "seed": seed,
        "model": args.model,
        "timestamp_utc": timestamp,
        "wall_clock_seconds": wall_clock,
        "chain_id": w3.eth.chain_id,
        "chain_name": "sepolia",
        "start_block": start_block,
        "end_block": end_block,
        "alice_funding_eth": alice_funding,
        "anti_strand": strand_report,
        "gas_floor_gwei": MIN_GAS_PRICE_GWEI,
        "funder_pool": {
            "num_funders": len(funder_amounts),
            "amounts_eth": funder_amounts,
            "total_eth": funder_pool_total_eth,
            "swept_back_eth": sweep_report["total_returned_eth"],
            "refill_events": dispatcher._funder_refill_events,
            "gas_burned_eth": gas_burned_by_funders_eth,
        },
        "oracle": {
            "campaign_ts_iso": campaign_ts.isoformat(),
            "eth_usd": eth_price,
            "usdt_usd": usdt_price,
            "trx_usd": oracle.price("trx", campaign_ts),
            "usd_stolen": usd_stolen,
            "usd_operating_capital": usd_operating_capital,
            "usd_total_attacker_capital": usd_total_attacker_capital,
            "usd_gas_burned_by_funders": usd_gas_burned_by_funders,
            "usd_to_clean_exits": usd_to_exits,
            "recovery_pct_of_stolen": recovery_pct_of_stolen,
            "recovery_pct_of_capital": recovery_pct_of_capital,
            # HONEST metric — mock-pool-adjusted, market-priced, capped.
            "honest_recovery": {
                "eth_swapped_into_pool": eth_swapped_into_pool,
                "eth_at_exits_direct": eth_at_exits,
                "usdt_at_exits_raw": usdt_at_exits,
                "usdt_as_eth_market_rate": usdt_as_eth_market,
                "usdt_as_eth_capped_by_swap_input": usdt_as_eth_capped,
                "honest_recovery_eth": honest_recovery_eth,
                "honest_recovery_usd": honest_recovery_usd,
                "honest_recovery_pct": honest_recovery_pct,
            },
            # FULL ETH reconciliation from on-chain balances.
            "eth_reconciliation": {
                "alice_funded_in": reconc_alice_in,
                "alice_balance_now": reconc_alice_now,
                "exits_eth_now": reconc_exits_eth_now,
                "burners_eth_now_recoverable": reconc_burners_eth_now,
                "honest_recovery_eth": honest_recovery_eth,
                "residual_eth_locked_or_burned": reconc_residual,
                "note": (
                    "residual = alice_in - (alice_now + exits_now + "
                    "burners_now) - honest_recovery. Splits between: (a) "
                    "ETH stuck in MockTornado contract (recoverable via "
                    "scripts/mixer_recover.py using persisted notes), "
                    "(b) ETH pushed to MockUniswapV2Pool via swap "
                    "(recoverable via reverse-swap in sweep_sepolia.py), "
                    "and (c) gas paid to validators (permanently lost)."
                ),
            },
        },
        "args": vars(args),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    (out_dir / "addresses.json").write_text(json.dumps(addresses, indent=2))

    # SECRETS — save private keys of every wallet the dispatcher generated,
    # so scripts/sweep_sepolia.py can later reclaim any residual ETH/USDT
    # stranded on those wallets. This file MUST NOT be committed —
    # gitignored via .gitignore (`wallets_keys.json`).
    wallets_keys = {
        "run_name": run_name,
        "chain_id": w3.eth.chain_id,
        "deployer": deployer,
        "wallets": dict(dispatcher.wallets),
        "warning": (
            "This file contains private keys. Even though these are Sepolia "
            "testnet wallets with no real-money value, NEVER commit this file "
            "to Git and NEVER paste its contents into chat, logs, or "
            "screenshots. Use scripts/sweep_sepolia.py to reclaim residual "
            "funds back to the deployer, then delete this file."
        ),
    }
    (out_dir / "wallets_keys.json").write_text(
        json.dumps(wallets_keys, indent=2, default=str)
    )
    print(f"[runner] wallets_keys.json saved ({len(dispatcher.wallets)} keys) "
          f"— gitignored; use scripts/sweep_sepolia.py to reclaim funds",
          file=sys.stderr)

    campaign_dict = {
        "successful": result.successful,
        "stopped_reason": result.stopped_reason,
        "iterations": result.iterations,
        "cost_usd": result.cost_usd,
        "total_tool_calls": result.total_tool_calls,
        "final_text": result.final_text,
        "delegations": result.delegations,
    }
    (out_dir / "campaign.json").write_text(
        json.dumps(campaign_dict, indent=2, default=str)
    )

    # Full sub-agent transcripts — belt-and-suspenders redundancy for the
    # notes_file mixer persistence. If _mixer_deposit's file-write silently
    # fails (see its except:pass), the deposit_note still lives inside the
    # sub-agent's tool_calls output here. scripts/mixer_recover.py falls
    # back to scanning this file when mixer_notes.jsonl is missing.
    transcripts = []
    for i, run in enumerate(result.sub_agent_runs):
        role = (result.delegations[i].get("role")
                if i < len(result.delegations) else None)
        transcripts.append({
            "index": i,
            "role": role,
            "status": run.status,
            "summary": run.summary,
            "key_facts": run.key_facts,
            "iterations": run.iterations,
            "cost_usd": run.cost_usd,
            "stopped_reason": run.stopped_reason,
            "tool_calls": run.tool_calls,
        })
    (out_dir / "sub_agent_transcripts.json").write_text(
        json.dumps(transcripts, indent=2, default=str)
    )

    with (out_dir / "chain_trace.jsonl").open("w") as f:
        for tx in trace:
            f.write(json.dumps(jsonable(tx)) + "\n")

    # Human-readable summary
    summary_lines = [
        f"Sepolia campaign — {run_name}",
        f"Chain: Sepolia (11155111)",
        f"Scenario: {scenario.name} ({scenario.description[:80]}...)",
        f"Amount: {amount} {scenario.asset.upper()}",
        f"Wall clock: {wall_clock/60:.1f} min",
        f"Blocks: {start_block} -> {end_block} ({end_block - start_block} blocks)",
        f"LLM cost: ${result.cost_usd:.4f} ({args.model})",
        f"Tx traced: {len(trace)}",
        f"Attacker wallets: {len(all_attacker_addrs)}",
        f"Burners generated: {len(new_burner_addrs)}",
        f"Clean exits registered: {len(clean_exit_addrs)}",
        f"Clean exits funded: {sum(1 for r in clean_exit_records if r['eth_received'] > 0 or r['usdt_received'] > 0)}",
        f"Stolen (USD equiv): ${usd_stolen:,.2f}  "
        f"(@ {campaign_ts.strftime('%Y-%m-%d')}: ETH=${eth_price:,.2f})",
        f"Operating capital:  ${usd_operating_capital:,.2f}  "
        f"(funder pool {funder_pool_total_eth:.4f} ETH)",
        f"Total attacker cap: ${usd_total_attacker_capital:,.2f}",
        f"To exits (nominal USD, mock-pool priced): ${usd_to_exits:,.2f}",
        f"  Nominal recovery of stolen:  {recovery_pct_of_stolen:.1f}%  "
        f"(inflated if pool ratio distorted)",
        f"",
        f"HONEST recovery (mock-pool-adjusted, capped by ETH swapped in):",
        f"  ETH swapped into pool:     {eth_swapped_into_pool:.4f} ETH",
        f"  ETH direct at exits:       {eth_at_exits:.4f} ETH",
        f"  USDT at exits (raw):       {usdt_at_exits:,.2f} MockUSDT",
        f"  USDT -> ETH @ market rate: {usdt_as_eth_market:.4f} ETH  "
        f"(market: {eth_price:,.0f} USD/ETH)",
        f"  USDT -> ETH (capped):      {usdt_as_eth_capped:.4f} ETH  "
        f"(cap = swapped-in ETH)",
        f"  Honest recovery:           {honest_recovery_eth:.4f} ETH "
        f"= ${honest_recovery_usd:,.2f}",
        f"  Honest recovery %:         {honest_recovery_pct:.1f}%  "
        f"(capped display: {honest_recovery_pct_capped:.1f}%)",
        f"",
        f"ETH reconciliation (on-chain balances NOW, every wei accounted for):",
        f"  IN — alice funded from deployer:  {reconc_alice_in:.4f} ETH",
        f"  OUT — honest_recovery (at exits): {honest_recovery_eth:.4f} ETH",
        f"  OUT — alice residual:             {reconc_alice_now:.4f} ETH  (recoverable)",
        f"  OUT — exit wallets ETH dust:      {reconc_exits_eth_now:.4f} ETH  (recoverable)",
        f"  OUT — burners residual:           {reconc_burners_eth_now:.4f} ETH  (recoverable via sweep_sepolia.py)",
        f"  OUT — locked/burned (mixer+pool+gas): {reconc_residual:.4f} ETH",
        f"          - mixer: recoverable via mixer_recover.py",
        f"          - pool:  recoverable via sweep_sepolia.py --reverse-swap",
        f"          - gas:   PERMANENTLY LOST to validators",
        f"",
        f"ECONOMIC EFFICIENCY (real hacker perspective — no double-counting):",
        f"  ETH direct at exits (non-gas-seed):  {eth_at_exits:.4f} ETH  "
        f"= ${eth_at_exits * eth_price:,.2f}",
        f"  USDT at exits (raw balance):         {usdt_at_exits:,.2f} USDT  "
        f"= ${usdt_at_exits * usdt_price:,.2f} @ market",
        f"  USDT valued at pool-swapped ETH cap: {usdt_as_eth_capped:.4f} ETH  "
        f"(prevents mock-pool ratio distortion inflating the metric)",
        f"  ",
        f"  delivered_to_exits_pct (capped):     {honest_recovery_pct:.1f}%  "
        f"({honest_recovery_eth:.4f} ETH = ${honest_recovery_usd:,.2f} / "
        f"${usd_stolen:,.2f} stolen)",
        f"  attacker_recoverable_pct (residual): "
        f"{100.0 * (reconc_alice_now + reconc_burners_eth_now) * eth_price / max(usd_stolen, 1e-9):.1f}%  "
        f"(alice_residual + burners_residual — reachable via sweep)",
        f"  total_attacker_controlled_pct:       "
        f"{100.0 * (honest_recovery_eth + reconc_alice_now + reconc_burners_eth_now) * eth_price / max(usd_stolen, 1e-9):.1f}%  "
        f"(delivered + still-recoverable, NO double-count with exit ETH dust)",
        f"  economically_lost_pct:               "
        f"{max(0.0, 100.0 - (100.0 * (honest_recovery_eth + reconc_alice_now + reconc_burners_eth_now) * eth_price / max(usd_stolen, 1e-9))):.1f}%  "
        f"(gas + pool slippage — permanent economic loss)",
        f"  intentional_dust_pct:                "
        f"{100.0 * reconc_burners_eth_now * eth_price / max(usd_stolen, 1e-9):.1f}%  "
        f"(residuals left in burners by design — real Lazarus ops leave 2-5%)",
        f"Coordinator stopped: {result.stopped_reason}",
        f"",
        f"Etherscan links:",
        f"  Alice: https://sepolia.etherscan.io/address/{alice}",
        f"  Deployer: https://sepolia.etherscan.io/address/{deployer}",
    ]
    (out_dir / "summary.txt").write_text("\n".join(summary_lines))

    print(f"\n[runner] artifacts written to {out_dir}", file=sys.stderr)
    print("\n".join(summary_lines))


if __name__ == "__main__":
    main()
