"""Verifica que partial_visibility_split_by_platform funciona con benignos + CEX.

Comprueba que las hot wallets de exchange bootstrapped por run_benign
(anadidas en PR #53) son correctamente asignadas a su plataforma
correspondiente por el split N=7 platform-aware.

Uso:
    python scripts/test_platform_split.py [--benign-dir /path] [--limit 20]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from aml.detectors.dataset import (
    combine_runs, partial_visibility_split_by_platform,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benign-dir",
        default="/home/anon/aml-results/batch_2026-06-26/benign",
        help="Directorio con campanas benignas",
    )
    parser.add_argument(
        "--attacker-dir",
        default=None,
        help="Directorio opcional con campanas atacantes (para split mixto)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Numero de campanas a cargar (para tests rapidos)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )
    args = parser.parse_args()

    benign_runs = sorted(Path(args.benign_dir).iterdir())[: args.limit]
    all_runs = list(benign_runs)
    if args.attacker_dir:
        attacker_runs = sorted(Path(args.attacker_dir).iterdir())[: args.limit]
        all_runs = attacker_runs + benign_runs
        print(
            f"Cargando {len(attacker_runs)} atacantes + "
            f"{len(benign_runs)} benignos..."
        )
    else:
        print(f"Cargando {len(benign_runs)} campanas benignas...")

    combined = combine_runs(all_runs)
    print(f"\nGrafo combinado:")
    print(f"  Nodos: {combined.graph.number_of_nodes():,}")
    print(f"  Edges: {combined.graph.number_of_edges():,}")

    views = partial_visibility_split_by_platform(combined, seed=args.seed)

    print(f"\n{'-' * 70}")
    print("PARTITIONS N=7 (cada exchange ve su propio subgrafo)")
    print(f"{'-' * 70}")
    for v in views:
        n = v.visible_subgraph.number_of_nodes()
        e = v.visible_subgraph.number_of_edges()
        print(f"  {v.name:15s}: {n:>5,} nodos  {e:>6,} edges")

    print(f"\n{'-' * 70}")
    print("VERIFICACION: hot wallets etiquetadas por plataforma")
    print(f"{'-' * 70}")
    platform_wallet_map = {}
    for run in combined.runs:
        if run.kind == "benign":
            for platform, wallets in (
                run.addresses.get("exchange_wallets") or {}
            ).items():
                platform_wallet_map.setdefault(platform, set()).update(wallets)

    all_ok = True
    for view in views:
        if view.name in platform_wallet_map:
            expected = platform_wallet_map[view.name]
            visible = set(view.visible_subgraph.nodes()) & expected
            pct = 100 * len(visible) / len(expected) if expected else 0
            status = "OK" if pct == 100 else "WARN"
            if pct != 100:
                all_ok = False
            print(
                f"  {view.name:12s}: {len(visible)}/{len(expected)} hot "
                f"wallets visibles ({pct:.0f}%) [{status}]"
            )

    print(f"\n{'=' * 70}")
    if all_ok:
        print("Split funcionando: 100% de hot wallets aterrizan en su plataforma.")
    else:
        print("ATENCION: algunas hot wallets no van a su plataforma esperada.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
