"""Train + evaluate baselines on EthereumHeist (Wu 2023) real ETH hack dataset.

Task #14 follow-up. Loads the combined 633k-node graph built by
`load_ethereum_heist.py` and trains:
  1. RandomForest baseline (fast, matches published tree-ensemble winners)
  2. LogisticRegression (linear sanity check)
  3. [Optional] Our GCNDetector on a subsampled subgraph

The graph is huge (633k nodes / 2.45M edges) — dominated by UpbitHack (95% of
data). We subsample: keep ALL heist-labeled nodes (~47k), sample matching
benign nodes (~50k) stratified — final training set ~100k nodes, tractable
on CPU without GPU.

Features per node (extracted from graph, no label peeking):
  - in_degree, out_degree, total_degree
  - log(1+sum) value in / out
  - log(1+count) unique counterparties in / out
  - log(1+count) num_hacks_involved (how many campaigns this addr touched)

8 features — smaller than her sim's 19 (which include mixer-specific counts
absent in this dataset). This is honest: EthereumHeist doesn't tag mixer
usage per transaction, so features are chain-agnostic.

Usage:
    python scripts/train_ethereum_heist.py
    python scripts/train_ethereum_heist.py --exclude-upbit  # smaller graph

Writes: results/ethereum_heist_baselines.json
"""
from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

import networkx as nx
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    confusion_matrix, f1_score, precision_score,
    recall_score, roc_auc_score,
)
from sklearn.model_selection import train_test_split


REPO_ROOT = Path(__file__).resolve().parents[1]
PKL = REPO_ROOT / "data" / "ethereum_heist_combined.pkl"
OUT_JSON = REPO_ROOT / "results" / "ethereum_heist_baselines.json"
SEED = 42
N_BENIGN_SAMPLE = 50_000   # sample this many benign nodes to keep training tractable


def extract_features(g: nx.MultiDiGraph, nodes: list[str],
                     hack_membership: dict[str, set[str]]) -> np.ndarray:
    """Compute 8 features per node: degrees + log-volumes + uniques + hack count."""
    n = len(nodes)
    X = np.zeros((n, 8), dtype=np.float32)
    idx = {a: i for i, a in enumerate(nodes)}
    idx_set = set(idx)

    in_val = np.zeros(n)
    out_val = np.zeros(n)
    in_cp: list[set] = [set() for _ in range(n)]
    out_cp: list[set] = [set() for _ in range(n)]

    for u, v, data in g.edges(data=True):
        if u not in idx_set or v not in idx_set:
            continue
        ui, vi = idx[u], idx[v]
        val = data.get("value", 0.0) or 0.0
        out_val[ui] += val
        in_val[vi] += val
        out_cp[ui].add(v)
        in_cp[vi].add(u)

    in_deg = np.array([g.in_degree(a) if g.has_node(a) else 0 for a in nodes], dtype=np.float64)
    out_deg = np.array([g.out_degree(a) if g.has_node(a) else 0 for a in nodes], dtype=np.float64)

    X[:, 0] = in_deg
    X[:, 1] = out_deg
    X[:, 2] = in_deg + out_deg
    X[:, 3] = np.log1p(in_val)
    X[:, 4] = np.log1p(out_val)
    X[:, 5] = np.log1p([len(s) for s in in_cp])
    X[:, 6] = np.log1p([len(s) for s in out_cp])
    X[:, 7] = np.log1p([len(hack_membership.get(a, set())) for a in nodes])
    return X


def _metrics(y_true, y_pred, y_proba) -> dict:
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
    return {
        "f1_heist": round(f1_score(y_true, y_pred), 4),
        "precision_heist": round(precision_score(y_true, y_pred), 4),
        "recall_heist": round(recall_score(y_true, y_pred), 4),
        "roc_auc": round(roc_auc_score(y_true, y_proba), 4),
        "confusion": {
            "tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exclude-upbit", action="store_true",
                        help="Drop UpbitHack (95% of data) for a faster/leaner run")
    args = parser.parse_args()

    print(f"Loading {PKL}...")
    t0 = time.time()
    with PKL.open("rb") as f:
        d = pickle.load(f)
    g = d["graph"]
    node_labels = d["node_labels"]
    hack_membership = d["hack_membership"]
    print(f"  Loaded in {time.time()-t0:.1f}s: "
          f"{g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges")

    # Optional: drop UpbitHack to make training much faster
    if args.exclude_upbit:
        print("  Excluding UpbitHack nodes/edges...")
        upbit_nodes = {a for a, hacks in hack_membership.items() if "UpbitHack" in hacks and len(hacks) == 1}
        g = g.subgraph(set(g.nodes()) - upbit_nodes).copy()
        node_labels = {a: v for a, v in node_labels.items() if a in g}
        print(f"  After exclude: {g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges")

    # Split labeled nodes: keep all heist, sample benign
    heist = [a for a, v in node_labels.items() if v == 1 and g.has_node(a)]
    benign = [a for a, v in node_labels.items() if v == 0 and g.has_node(a)]
    rng = np.random.default_rng(SEED)
    if len(benign) > N_BENIGN_SAMPLE:
        benign_sample = rng.choice(benign, size=N_BENIGN_SAMPLE, replace=False).tolist()
    else:
        benign_sample = benign
    training_nodes = heist + benign_sample
    y = np.array(
        [1] * len(heist) + [0] * len(benign_sample),
        dtype=np.int8,
    )
    print(f"  Training set: {len(training_nodes):,} nodes "
          f"({len(heist):,} heist + {len(benign_sample):,} benign)")

    print("Extracting 8 features per node...")
    t0 = time.time()
    X = extract_features(g, training_nodes, hack_membership)
    print(f"  Done in {time.time()-t0:.1f}s. Shape: {X.shape}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=SEED,
    )
    print(f"\nSplit (80/20 stratified, seed={SEED}): "
          f"train={len(X_train):,}, test={len(X_test):,} "
          f"(pos={int(y_test.sum())}, neg={len(y_test) - int(y_test.sum())})")
    print()

    results = {
        "dataset": "EthereumHeist (Wu et al. 2023, 23 real ETH hacks)",
        "n_nodes_graph": g.number_of_nodes(),
        "n_edges_graph": g.number_of_edges(),
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "n_test_positive": int(y_test.sum()),
        "seed": SEED,
        "exclude_upbit": args.exclude_upbit,
        "models": {},
    }

    # RandomForest
    print("Training RandomForest (200 trees, class_weight='balanced')...")
    t0 = time.time()
    rf = RandomForestClassifier(
        n_estimators=200, class_weight="balanced",
        random_state=SEED, n_jobs=-1,
    )
    rf.fit(X_train, y_train)
    rf_pred = rf.predict(X_test)
    rf_proba = rf.predict_proba(X_test)[:, 1]
    m = _metrics(y_test, rf_pred, rf_proba)
    m["train_time_seconds"] = round(time.time() - t0, 2)
    results["models"]["RandomForest"] = m
    print(f"  F1={m['f1_heist']:.4f}  P={m['precision_heist']:.4f}  "
          f"R={m['recall_heist']:.4f}  AUC={m['roc_auc']:.4f}  "
          f"({m['train_time_seconds']}s)")
    print()

    # LogisticRegression
    print("Training LogisticRegression...")
    t0 = time.time()
    lr = LogisticRegression(
        C=1.0, max_iter=2000, class_weight="balanced",
        random_state=SEED,
    )
    lr.fit(X_train, y_train)
    lr_pred = lr.predict(X_test)
    lr_proba = lr.predict_proba(X_test)[:, 1]
    m = _metrics(y_test, lr_pred, lr_proba)
    m["train_time_seconds"] = round(time.time() - t0, 2)
    results["models"]["LogisticRegression"] = m
    print(f"  F1={m['f1_heist']:.4f}  P={m['precision_heist']:.4f}  "
          f"R={m['recall_heist']:.4f}  AUC={m['roc_auc']:.4f}  "
          f"({m['train_time_seconds']}s)")
    print()

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, indent=2))
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
