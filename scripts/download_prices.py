"""Download historical USD prices for ETH, TRX, USDT from CoinGecko.

Uses CoinGecko Demo API (requires COINGECKO_API_KEY in .env or environment).
Demo tier allows ~365 days of historical daily prices.

Usage:
    python scripts/download_prices.py
    python scripts/download_prices.py --start 2026-01-01 --end 2026-08-13
    python scripts/download_prices.py --assets eth trx
"""
from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "data" / "prices"
ENV_FILE = REPO_ROOT / ".env"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart/range"
COIN_IDS = {"eth": "ethereum", "trx": "tron", "usdt": "tether"}
DEFAULT_START = "2026-01-01"
RATE_LIMIT_SLEEP = 2.5  # Demo tier ~30 req/min


def load_api_key() -> str:
    key = os.environ.get("COINGECKO_API_KEY")
    if key:
        return key.strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("COINGECKO_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(
        f"COINGECKO_API_KEY not found. Add it to {ENV_FILE} or export it."
    )


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--start", default=DEFAULT_START, help="ISO date (UTC), earliest price to fetch"
    )
    p.add_argument(
        "--end", default=None, help="ISO date (UTC), defaults to now"
    )
    p.add_argument(
        "--assets",
        nargs="+",
        default=sorted(COIN_IDS.keys()),
        choices=sorted(COIN_IDS.keys()),
    )
    p.add_argument("--output-dir", type=Path, default=CACHE_DIR)
    return p.parse_args()


def to_unix(iso_date: str) -> int:
    return int(
        datetime.strptime(iso_date, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def fetch(
    coin_id: str, start_unix: int, end_unix: int, api_key: str
) -> list[tuple[int, float]]:
    url = COINGECKO_URL.format(coin_id=coin_id)
    params = {"vs_currency": "usd", "from": start_unix, "to": end_unix}
    headers = {"x-cg-demo-api-key": api_key}
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    prices = payload.get("prices", [])  # list of [timestamp_ms, price_usd]
    return [(int(ts_ms / 1000), float(price)) for ts_ms, price in prices]


def write_csv(rows: list[tuple[int, float]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_unix", "price_usd"])
        for ts, price in rows:
            w.writerow([ts, price])


def main():
    args = parse_args()
    api_key = load_api_key()
    start_unix = to_unix(args.start)
    end_unix = (
        to_unix(args.end)
        if args.end
        else int(datetime.now(tz=timezone.utc).timestamp())
    )

    print(f"Fetching prices: {args.start} -> {args.end or 'now'}")
    print(f"Assets: {args.assets}")
    print(f"Output: {args.output_dir}")
    print(f"API key: {api_key[:6]}...{api_key[-4:]} (loaded)\n")

    for asset in args.assets:
        coin_id = COIN_IDS[asset]
        print(f"[{asset}] CoinGecko id={coin_id}")
        rows = fetch(coin_id, start_unix, end_unix, api_key)
        if not rows:
            print(f"  WARN: no rows returned for {asset}")
            continue
        csv_path = args.output_dir / f"{asset}.csv"
        write_csv(rows, csv_path)
        first_ts = datetime.fromtimestamp(rows[0][0], tz=timezone.utc)
        last_ts = datetime.fromtimestamp(rows[-1][0], tz=timezone.utc)
        print(f"  rows:  {len(rows):,}")
        print(f"  range: {first_ts.date()} -> {last_ts.date()}")
        print(f"  wrote: {csv_path}\n")
        time.sleep(RATE_LIMIT_SLEEP)


if __name__ == "__main__":
    main()
