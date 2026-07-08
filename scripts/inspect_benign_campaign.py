"""Inspecciona UNA campana benigna en detalle.

Uso:
    python scripts/inspect_benign_campaign.py [--run /path/to/run]
    python scripts/inspect_benign_campaign.py [--index 0]

Si no se pasa --run, toma la primera campana del directorio por defecto.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        default="/home/anon/aml-results/batch_2026-06-26/benign",
        help="Directorio raiz con las campanas benignas",
    )
    parser.add_argument(
        "--run",
        default=None,
        help="Path directo a UNA campana (override --index)",
    )
    parser.add_argument(
        "--index",
        type=int,
        default=0,
        help="Indice (0-based) de la campana a inspeccionar dentro de --dir",
    )
    args = parser.parse_args()

    if args.run:
        run = Path(args.run)
    else:
        runs = sorted(Path(args.dir).iterdir())
        if args.index >= len(runs):
            raise SystemExit(
                f"Index {args.index} fuera de rango (hay {len(runs)} campanas)"
            )
        run = runs[args.index]

    print("=" * 70)
    print(f"INSPECCION DE UNA CAMPANA: {run.name}")
    print("=" * 70)

    addr = json.loads((run / "addresses.json").read_text())
    meta = json.loads((run / "meta.json").read_text())

    print(f"\nSeed: {meta.get('seed')}")
    print(f"Duracion: {meta.get('wall_clock_sec', 0):.1f}s")
    print(f"Bloques minados: {meta.get('end_block', 0):,}")

    print(f"\n{'-' * 70}")
    print("WALLETS EN ESTA CAMPANA")
    print(f"{'-' * 70}")
    print(f"Users retail: {len(addr['benign_users'])}")
    print(f"  Primeras 3 direcciones:")
    for u in addr["benign_users"][:3]:
        print(f"    {u}")

    print(f"\nHot wallets por exchange:")
    for platform, wallets in addr.get("exchange_wallets", {}).items():
        print(f"  {platform:10s}: {len(wallets)} hot wallets")
        for w in wallets:
            print(f"    {w}")

    print(f"\n{'-' * 70}")
    print("ACTIVITY MIX DE ESTA CAMPANA")
    print(f"{'-' * 70}")
    activities = meta.get("activity_counts", {})
    for k, v in sorted(activities.items(), key=lambda x: -x[1]):
        marker = "  <-- CEX" if k.startswith("exchange_") else ""
        print(f"  {k:22s}: {v:>3}{marker}")

    print(f"\n{'-' * 70}")
    print("LABELS EN addresses.json")
    print(f"{'-' * 70}")
    label_counter = Counter(addr["labels"].values())
    for label, count in label_counter.most_common():
        print(f"  {label:15s}: {count}")

    print(f"\n{'=' * 70}")
    print(f"Archivos en la campana: {run}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
