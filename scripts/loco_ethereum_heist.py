"""LOCO-by-hack cross-validation on EthereumHeist (Wu 2023) real dataset.

User (2026-07-18) rightfully suspected the F1=0.986 from
`train_ethereum_heist.py` was inflated by memorization — same pattern as
the simulation audit (F1 0.97 → 0.42 under LOCO). Concrete suspicions:
  1. UpbitHack is 95% of the graph — random split trains + tests on same
     hack, model learns Lazarus-specific fingerprint.
  2. `log_hack_count` feature trivially correlates with heist label.
  3. Random 80/20 lets 2nd-hop counterparties leak between splits.

This script implements the fair evaluation:
  - 23 folds, one per hack
  - Each fold: hold out ALL addresses that appear ONLY in that hack
    (addresses shared with other hacks stay in train — realistic since
    heuristically service providers span multiple hacks)
  - Train RandomForest on the other 22 hacks, predict on held-out
  - Report per-fold F1 and mean ± std

Same feature set as train_ethereum_heist.py (8 features). We also run a
version WITHOUT the log_hack_count feature to isolate its contribution.

Output: results/loco_ethereum_heist.json

Usage:
    python scripts/loco_ethereum_heist.py
"""
from __future__ import annotations

import json
import pickle
import statistics
import time
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    confusion_matrix, f1_score, precision_score,
    recall_score, roc_auc_score,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PKL = REPO_ROOT / "data" / "ethereum_heist_combined.pkl"
OUT_JSON = REPO_ROOT / "results" / "loco_ethereum_heist.json"
SEED = 42


def extract_features(g, nodes, hack_membership, include_hack_count=True):
    """Same as train_ethereum_heist but optionally drops log_hack_count."""
    n = len(nodes)
    n_feat = 8 if include_hack_count else 7
    X = np.zeros((n, n_feat), dtype=np.float32)
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
    if include_hack_count:
        X[:, 7] = np.log1p([len(hack_membership.get(a, set())) for a in nodes])
    return X


def _metrics(y_true, y_pred, y_proba):
    if len(y_true) == 0 or len(set(y_true)) < 2:
        return None
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "f1": round(f1_score(y_true, y_pred, zero_division=0), 4),
        "precision": round(precision_score(y_true, y_pred, zero_division=0), 4),
        "recall": round(recall_score(y_true, y_pred, zero_division=0), 4),
        "auc": round(roc_auc_score(y_true, y_proba), 4) if len(set(y_true)) == 2 else None,
        "tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn),
    }


def loco_fold(g, node_labels, hack_membership, held_out_hack, all_labeled_nodes,
              include_hack_count=True, sample_benign_per_fold=15000):
    """One LOCO fold: train on 22 hacks, evaluate on the held-out hack."""
    # Held-out addresses: those whose hack membership is EXACTLY {held_out_hack}
    held_out_nodes = {a for a in all_labeled_nodes
                      if hack_membership.get(a) == {held_out_hack}}
    train_nodes_all = [a for a in all_labeled_nodes if a not in held_out_nodes]

    if not held_out_nodes:
        return None

    # Balance train set: keep all heist + sample equivalent benign to avoid
    # UpbitHack size domination
    train_heist = [a for a in train_nodes_all if node_labels[a] == 1]
    train_benign_all = [a for a in train_nodes_all if node_labels[a] == 0]
    rng = np.random.default_rng(SEED)
    if len(train_benign_all) > sample_benign_per_fold:
        train_benign = rng.choice(train_benign_all, size=sample_benign_per_fold, replace=False).tolist()
    else:
        train_benign = train_benign_all
    train_nodes = train_heist + train_benign
    test_nodes = sorted(held_out_nodes)

    y_train = np.array([node_labels[a] for a in train_nodes], dtype=np.int8)
    y_test = np.array([node_labels[a] for a in test_nodes], dtype=np.int8)

    X_train = extract_features(g, train_nodes, hack_membership, include_hack_count)
    X_test = extract_features(g, test_nodes, hack_membership, include_hack_count)

    rf = RandomForestClassifier(n_estimators=100, class_weight="balanced",
                                random_state=SEED, n_jobs=-1)
    rf.fit(X_train, y_train)
    pred = rf.predict(X_test)
    proba = rf.predict_proba(X_test)[:, 1] if hasattr(rf, "predict_proba") else pred.astype(float)
    m = _metrics(y_test.tolist(), pred.tolist(), proba.tolist())
    if m is None:
        return None
    m["n_test"] = int(len(test_nodes))
    m["n_test_heist"] = int(y_test.sum())
    m["n_train"] = int(len(train_nodes))
    return m


def main():
    print(f"Loading {PKL}...")
    with PKL.open("rb") as f:
        d = pickle.load(f)
    g = d["graph"]
    node_labels = d["node_labels"]
    hack_membership = d["hack_membership"]
    hacks = d["hacks"]
    all_labeled_nodes = [a for a in node_labels if g.has_node(a)]
    print(f"  {g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges")
    print(f"  {len(hacks)} hacks / {len(all_labeled_nodes):,} labeled nodes")
    print()

    for scheme, use_hack_count in [
        ("with_hack_count_feature", True),
        ("WITHOUT_hack_count_feature", False),
    ]:
        print(f"=== SCHEME: {scheme} ===")
        per_fold_results = []
        t_start = time.time()
        for i, held_out in enumerate(hacks, 1):
            t0 = time.time()
            m = loco_fold(g, node_labels, hack_membership, held_out,
                          all_labeled_nodes, include_hack_count=use_hack_count)
            if m is None:
                print(f"  [{i:2d}/{len(hacks)}] {held_out:32s}  SKIPPED (no test data)")
                per_fold_results.append({"hack": held_out, "skipped": True})
                continue
            per_fold_results.append({"hack": held_out, **m})
            print(f"  [{i:2d}/{len(hacks)}] {held_out:32s}  "
                  f"F1={m['f1']:.4f}  P={m['precision']:.4f}  R={m['recall']:.4f}  "
                  f"AUC={m['auc'] if m['auc'] is not None else 'N/A'}  "
                  f"(test_n={m['n_test']}, pos={m['n_test_heist']}, {time.time()-t0:.1f}s)")

        valid = [f for f in per_fold_results if "skipped" not in f]
        f1s = [f["f1"] for f in valid]
        mean_f1 = statistics.mean(f1s) if f1s else 0
        std_f1 = statistics.stdev(f1s) if len(f1s) > 1 else 0
        print()
        print(f"  MEAN F1 across {len(valid)} folds: {mean_f1:.4f} ± {std_f1:.4f}")
        print(f"  MIN: {min(f1s):.4f}  MAX: {max(f1s):.4f}")
        print(f"  Total time: {time.time() - t_start:.1f}s")
        print()

        # Save results
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        if not OUT_JSON.exists():
            existing = {}
        else:
            existing = json.loads(OUT_JSON.read_text())
        existing[scheme] = {
            "loco_f1_mean": round(mean_f1, 4),
            "loco_f1_std": round(std_f1, 4),
            "loco_f1_min": round(min(f1s), 4) if f1s else None,
            "loco_f1_max": round(max(f1s), 4) if f1s else None,
            "n_folds_valid": len(valid),
            "per_fold": per_fold_results,
        }
        OUT_JSON.write_text(json.dumps(existing, indent=2))

    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
