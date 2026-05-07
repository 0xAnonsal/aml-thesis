"""Download historical USD prices for ETH, TRX, USDT from CoinGecko.

Free tier returns daily prices for ranges >90 days; hourly is paywalled. Daily
resolution is sufficient for laundering campaigns that operate at day+ scale.

Usage:
    python scripts/download_prices.py
    python scripts/download_prices.py --start 2020-01-01 --end 2025-01-01
    python scripts/download_prices.py --assets eth trx
"""
from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "prices"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart/range"
COIN_IDS = {"eth": "ethereum", "trx": "tron", "usdt": "tether"}
DEFAULT_START = "2018-01-01"
RATE_LIMIT_SLEEP = 2.5  # CoinGecko free tier ~30 req/min


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


def fetch(coin_id: str, start_unix: int, end_unix: int) -> list[tuple[int, float]]:
    url = COINGECKO_URL.format(coin_id=coin_id)
    params = {"vs_currency": "usd", "from": start_unix, "to": end_unix}
    resp = requests.get(url, params=params, timeout=30)
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
    start_unix = to_unix(args.start)
    end_unix = (
        to_unix(args.end)
        if args.end
        else int(datetime.now(tz=timezone.utc).timestamp())
    )

    print(f"Fetching prices: {args.start} -> {args.end or 'now'}")
    print(f"Assets: {args.assets}")
    print(f"Output: {args.output_dir}\n")

    for asset in args.assets:
        coin_id = COIN_IDS[asset]
        print(f"[{asset}] CoinGecko id={coin_id}")
        rows = fetch(coin_id, start_unix, end_unix)
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
