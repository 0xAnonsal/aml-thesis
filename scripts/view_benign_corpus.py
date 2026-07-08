"""Dashboard resumen del corpus benigno con CEX interactions.

Uso:
    python scripts/view_benign_corpus.py [--dir /path/to/benign]

Reporta:
  - Numero total de campanas benignas
  - Users retail agregados y por-campana
  - Hot wallets CEX agregadas y por plataforma
  - Activity mix con % de CEX interactions
  - Verificacion de que el mix real matchea el diseno (20% CEX target)

Anadido en PR #53 (benign traffic interacts with CEX hot wallets).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        default="/home/anon/aml-results/batch_2026-06-26/benign",
        help="Directorio con las campanas benignas",
    )
    args = parser.parse_args()
    benign_dir = Path(args.dir)

    print("=" * 70)
    print("CORPUS BENIGNO — DASHBOARD")
    print("=" * 70)

    runs = sorted(benign_dir.iterdir())
    print(f"\nCampanas totales en {benign_dir}: {len(runs)}")

    total_users = 0
    total_hot_wallets = 0
    activity_totals: Counter = Counter()
    platform_wallet_counts: Counter = Counter()
    users_per_campaign = []
    hot_wallets_per_campaign = []

    for run in runs:
        addr_p = run / "addresses.json"
        meta_p = run / "meta.json"
        if not addr_p.exists() or not meta_p.exists():
            continue
        addr = json.loads(addr_p.read_text())
        meta = json.loads(meta_p.read_text())

        n_users = len(addr.get("benign_users", []))
        ew = addr.get("exchange_wallets", {})
        n_hot = sum(len(v) for v in ew.values())

        total_users += n_users
        total_hot_wallets += n_hot
        users_per_campaign.append(n_users)
        hot_wallets_per_campaign.append(n_hot)

        for platform, wallets in ew.items():
            platform_wallet_counts[platform] += len(wallets)

        for k, v in (meta.get("activity_counts") or {}).items():
            activity_totals[k] += v

    print(f"\n{'-' * 70}")
    print("USERS Y INFRAESTRUCTURA")
    print(f"{'-' * 70}")
    print(f"  Users retail totales:          {total_users:>10,}")
    if users_per_campaign:
        print(f"    media por campana:           {mean(users_per_campaign):>10.1f}")
        print(f"    mediana por campana:         {median(users_per_campaign):>10.1f}")
    print(f"  Hot wallets CEX totales:       {total_hot_wallets:>10,}")
    if hot_wallets_per_campaign:
        print(f"    media por campana:           {mean(hot_wallets_per_campaign):>10.1f}")
        print(f"    (deberia ser 21 = 3 hot x 7 exchanges)")

    print(f"\n{'-' * 70}")
    print(f"HOT WALLETS POR PLATAFORMA (agregado {len(runs)} campanas)")
    print(f"{'-' * 70}")
    for platform, count in sorted(platform_wallet_counts.items()):
        expected = 3 * len(runs)
        print(f"  {platform:12s}: {count:>6,}  (esperado: {expected:,})")

    print(f"\n{'-' * 70}")
    print(f"ACTIVITY MIX (agregado {len(runs)} campanas)")
    print(f"{'-' * 70}")
    total_activities = sum(activity_totals.values()) - activity_totals.get("skipped", 0)
    for kind, count in sorted(activity_totals.items(), key=lambda x: -x[1]):
        if kind == "skipped":
            continue
        pct = 100 * count / total_activities if total_activities else 0
        marker = "  <-- CEX" if kind.startswith("exchange_") else ""
        print(f"  {kind:22s}: {count:>7,}  ({pct:5.1f}%){marker}")

    cex_total = (
        activity_totals.get("exchange_deposit", 0)
        + activity_totals.get("exchange_withdrawal", 0)
    )
    cex_pct = 100 * cex_total / total_activities if total_activities else 0
    print(f"\n  CEX interactions totales: {cex_total:>7,}  ({cex_pct:5.1f}%) del trafico")
    verdict = "OK" if 18 < cex_pct < 22 else "DESVIACION"
    print(f"  Target design: 20.0%. Real: {cex_pct:.1f}%. {verdict}")

    print(f"\n{'=' * 70}")


if __name__ == "__main__":
    main()
