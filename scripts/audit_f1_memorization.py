"""Audit script: does the F1=0.971 headline result reflect real detection,
or is the GCN memorizing / relying on trivial feature-label leakage?

User raised the concern (2026-07-18) — with only 35 campaigns (20 attacker +
15 benign), F1=0.971 is suspiciously high. Before writing chapter 5, we need
to know if the number holds up under stricter methodological scrutiny.

Four diagnostic checks:

  CHECK 1 — Address overlap between train/test splits
      Non-contract attacker + benign addresses should be DISJOINT between
      train and test. If any overlap exists, the model has literally seen
      the same address label in both splits.

  CHECK 2 — Feature-label mutual information per feature
      For each of the 19 features, compute the mean of that feature for
      attackers vs benigns and the Cohen's d effect size. Any feature with
      near-perfect separation (|d| > 3.0 or attacker-mean vs benign-mean
      ratio > 100×) is a trivial label predictor — a linear model over
      that feature alone would beat any complex GCN.

  CHECK 3 — Leave-one-campaign-out cross-validation
      Fair cross-validation on such a small dataset. For each of the 35
      campaigns: use it as the sole test set, train on the other 34.
      Report mean ± std of F1 across all 35 folds. If mean is far below
      the seed=42 F1=0.971 headline, the headline is a lucky-split
      artefact — the real F1 is the LOCO number.

  CHECK 4 — Mixer-only-feature baseline
      Train a logistic regression using ONLY the 4 mixer features
      (mixer_deposit_in, mixer_deposit_out, mixer_withdraw_in,
      mixer_withdraw_out). If its F1 approaches the GCN's F1, the whole
      GCN reduces to a mixer detector — this is not "wrong" per the
      thesis's threat model but must be acknowledged (and would fail on
      the stablecoin-scam scenario where mixer features are all zero).

Usage:
    python scripts/audit_f1_memorization.py

Writes: results/audit_f1_memorization.json with all numbers
"""
from __future__ import annotations

import json
import math
import pickle
import statistics
import time
from pathlib import Path

import networkx as nx
import numpy as np

from aml.detectors.baselines import derive_binary_labels
from aml.detectors.dataset import train_val_test_split
from aml.detectors.eval import evaluate
from aml.detectors.gnn import FEATURE_NAMES, extract_features


REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PKL = Path.home() / "aml-results" / "batch_2026-06-26" / "dataset.pkl"
OUT_JSON = REPO_ROOT / "results" / "audit_f1_memorization.json"
SEED = 42


def _split_nodes_by_runs(
    g: nx.MultiDiGraph, train_runs: set[str], test_runs: set[str],
) -> tuple[set[str], set[str]]:
    """Same node-assignment logic as _train_detectors.py:
    nodes appearing in ANY test run → test; else if in ANY train run → train.
    """
    train_nodes: set[str] = set()
    test_nodes: set[str] = set()
    for addr, data in g.nodes(data=True):
        runs = set(data.get("runs", []))
        if runs & test_runs:
            test_nodes.add(addr)
        elif runs & train_runs:
            train_nodes.add(addr)
    return train_nodes, test_nodes


# --- CHECK 1 -------------------------------------------------------------


def check_address_overlap(combined) -> dict:
    """Address-level overlap between train / test splits."""
    g = combined.graph
    all_runs = combined.all_run_names
    train_runs, _, test_runs = train_val_test_split(
        all_runs, ratios=(0.7, 0.0, 0.3), seed=SEED,
    )
    train_nodes, test_nodes = _split_nodes_by_runs(
        g, set(train_runs), set(test_runs),
    )
    bin_labels = derive_binary_labels(combined.node_labels)
    train_labeled = {a for a in train_nodes if a in bin_labels}
    test_labeled = {a for a in test_nodes if a in bin_labels}
    overlap = train_labeled & test_labeled

    return {
        "train_labeled_count": len(train_labeled),
        "test_labeled_count": len(test_labeled),
        "overlap_count": len(overlap),
        "overlap_addresses_sample": sorted(overlap)[:10],
        "verdict": (
            "PASS — no address overlap between splits"
            if len(overlap) == 0
            else f"FAIL — {len(overlap)} addresses appear in both splits"
        ),
    }


# --- CHECK 2 -------------------------------------------------------------


def check_feature_label_leakage(combined) -> dict:
    """Per-feature separation between attacker and benign classes.

    Cohen's d = (mean_pos - mean_neg) / pooled_std.
    A feature with |d| > 3.0 is a near-perfect separator and can drive the
    classifier alone.
    """
    g = combined.graph
    bin_labels = derive_binary_labels(combined.node_labels)
    node_order = sorted(bin_labels.keys())
    y = np.array([bin_labels[a] for a in node_order], dtype=np.int8)
    X = extract_features(g, node_order)

    per_feature: list[dict] = []
    for i, name in enumerate(FEATURE_NAMES):
        pos_vals = X[y == 1, i]
        neg_vals = X[y == 0, i]
        mean_pos = float(pos_vals.mean()) if len(pos_vals) else 0.0
        mean_neg = float(neg_vals.mean()) if len(neg_vals) else 0.0
        var_pos = float(pos_vals.var(ddof=1)) if len(pos_vals) > 1 else 0.0
        var_neg = float(neg_vals.var(ddof=1)) if len(neg_vals) > 1 else 0.0
        pooled_std = math.sqrt((var_pos + var_neg) / 2) if (var_pos + var_neg) > 0 else 0.0
        cohens_d = (mean_pos - mean_neg) / pooled_std if pooled_std > 0 else 0.0
        pos_gt_zero = float((pos_vals > 0).mean()) if len(pos_vals) else 0.0
        neg_gt_zero = float((neg_vals > 0).mean()) if len(neg_vals) else 0.0

        per_feature.append({
            "feature": name,
            "mean_attacker": round(mean_pos, 4),
            "mean_benign": round(mean_neg, 4),
            "cohens_d": round(cohens_d, 3),
            "attacker_nonzero_fraction": round(pos_gt_zero, 3),
            "benign_nonzero_fraction": round(neg_gt_zero, 3),
            "trivial_separator": abs(cohens_d) > 3.0,
        })

    trivial = [f for f in per_feature if f["trivial_separator"]]
    return {
        "per_feature": per_feature,
        "n_trivial_separators": len(trivial),
        "trivial_features": [f["feature"] for f in trivial],
        "verdict": (
            "PASS — no trivially-separating features"
            if len(trivial) == 0
            else f"WARN — {len(trivial)} features have Cohen's d > 3.0 "
                 "(near-perfect separation)"
        ),
    }


# --- CHECK 3 -------------------------------------------------------------


def _fit_predict_gcn(
    combined, train_runs: set[str], test_runs: set[str],
) -> tuple[list[int], list[int], list[float]]:
    """Train GCN on train_runs, predict on test_runs. Returns
    (y_true, y_pred, y_proba) over labeled test nodes only."""
    from aml.detectors.gnn import GCNDetector

    g = combined.graph
    train_nodes, test_nodes = _split_nodes_by_runs(
        g, train_runs, test_runs,
    )
    bin_labels = derive_binary_labels(combined.node_labels)
    train_labels = {a: bin_labels[a] for a in train_nodes if a in bin_labels}
    test_labels = {a: bin_labels[a] for a in test_nodes if a in bin_labels}
    test_addrs = sorted(test_labels.keys())
    y_true = [test_labels[a] for a in test_addrs]

    det = GCNDetector(seed=SEED)
    det.fit(g, train_labels)
    proba = det.predict_proba(test_addrs)
    pred = [1 if p >= 0.5 else 0 for p in proba]
    return y_true, pred, proba


def check_leave_one_campaign_out(combined) -> dict:
    """LOCO CV: for each campaign, use it as sole test set."""
    all_runs = combined.all_run_names
    n_runs = len(all_runs)
    print(f"    Running {n_runs}-fold LOCO CV (each fold ~1s)...")

    per_fold: list[dict] = []
    t0 = time.time()
    for i, held_out in enumerate(all_runs):
        train_runs = set(all_runs) - {held_out}
        test_runs = {held_out}
        try:
            y_true, y_pred, y_proba = _fit_predict_gcn(
                combined, train_runs, test_runs,
            )
        except Exception as e:   # noqa: BLE001
            per_fold.append({
                "fold": i, "held_out": held_out, "error": str(e),
            })
            continue

        if len(y_true) == 0 or (sum(y_true) == 0 and sum(1 - t for t in y_true) == 0):
            # No labeled nodes in this fold's test set — skip
            per_fold.append({
                "fold": i, "held_out": held_out, "skipped": True,
                "n_labeled_test": 0,
            })
            continue

        m = evaluate(y_true, y_pred, y_proba)
        per_fold.append({
            "fold": i,
            "held_out": held_out,
            "n_labeled_test": len(y_true),
            "n_positive_test": sum(y_true),
            "f1": round(m.f1, 4),
            "precision": round(m.precision, 4),
            "recall": round(m.recall, 4),
            "auc": (
                round(m.roc_auc, 4) if m.roc_auc is not None else None
            ),
        })

        if (i + 1) % 5 == 0:
            elapsed = time.time() - t0
            print(f"      {i + 1}/{n_runs} folds done ({elapsed:.1f}s)")

    valid_folds = [
        f for f in per_fold
        if "error" not in f and not f.get("skipped")
    ]
    f1s = [f["f1"] for f in valid_folds]
    mean_f1 = statistics.mean(f1s) if f1s else 0.0
    std_f1 = statistics.stdev(f1s) if len(f1s) > 1 else 0.0

    return {
        "n_folds_total": n_runs,
        "n_folds_valid": len(valid_folds),
        "n_folds_skipped": len(per_fold) - len(valid_folds),
        "loco_f1_mean": round(mean_f1, 4),
        "loco_f1_std": round(std_f1, 4),
        "loco_f1_min": round(min(f1s), 4) if f1s else None,
        "loco_f1_max": round(max(f1s), 4) if f1s else None,
        "headline_f1_seed42": 0.967,  # GCN from RESULTS_FINAL.txt
        "gap": round(0.967 - mean_f1, 4),
        "per_fold": per_fold,
        "verdict": (
            f"OK — LOCO mean F1={mean_f1:.3f} close to headline "
            f"F1=0.967 (gap {abs(0.967 - mean_f1):.3f})"
            if abs(0.967 - mean_f1) < 0.05
            else f"CONCERN — LOCO mean F1={mean_f1:.3f} much lower "
                 f"than headline F1=0.967 (gap {0.967 - mean_f1:.3f}) "
                 "→ seed=42 headline may be inflated by a lucky split"
        ),
    }


# --- CHECK 4 -------------------------------------------------------------


def check_mixer_only_baseline(combined) -> dict:
    """Logistic regression using ONLY the 4 mixer feature columns.

    If this simple model beats the GCN, the GCN's F1 is not encoding
    graph structure — it's just detecting "used the mixer".
    """
    g = combined.graph
    bin_labels = derive_binary_labels(combined.node_labels)
    all_runs = combined.all_run_names
    train_runs, _, test_runs = train_val_test_split(
        all_runs, ratios=(0.7, 0.0, 0.3), seed=SEED,
    )
    train_nodes, test_nodes = _split_nodes_by_runs(
        g, set(train_runs), set(test_runs),
    )
    train_addrs = sorted(a for a in train_nodes if a in bin_labels)
    test_addrs = sorted(a for a in test_nodes if a in bin_labels)
    y_train = np.array([bin_labels[a] for a in train_addrs])
    y_test = np.array([bin_labels[a] for a in test_addrs])

    X_train = extract_features(g, train_addrs)
    X_test = extract_features(g, test_addrs)

    mixer_col_idx = [
        i for i, n in enumerate(FEATURE_NAMES)
        if n.startswith("mixer_deposit") or n.startswith("mixer_withdraw")
    ]
    X_train_mx = X_train[:, mixer_col_idx]
    X_test_mx = X_test[:, mixer_col_idx]

    # Trivial linear model: predict positive if ANY mixer feature > 0.
    y_pred_any = (X_test_mx.sum(axis=1) > 0).astype(int).tolist()
    m_any = evaluate(y_test.tolist(), y_pred_any)

    return {
        "features_used": [FEATURE_NAMES[i] for i in mixer_col_idx],
        "rule": "predict attacker iff ANY mixer feature > 0",
        "f1": round(m_any.f1, 4),
        "precision": round(m_any.precision, 4),
        "recall": round(m_any.recall, 4),
        "headline_gcn_f1": 0.967,
        "gap": round(0.967 - m_any.f1, 4),
        "verdict": (
            f"CONCERN — trivial mixer-only rule gets F1={m_any.f1:.3f}, "
            f"close to GCN F1=0.967. The GCN adds little signal beyond "
            "'used the mixer'."
            if m_any.f1 > 0.85
            else f"OK — mixer-only F1={m_any.f1:.3f} is much lower than "
                 f"GCN F1=0.967. The GCN uses more than mixer features."
        ),
    }


# --- main ----------------------------------------------------------------


def main() -> None:
    print(f"Loading {DATASET_PKL}...")
    with DATASET_PKL.open("rb") as f:
        d = pickle.load(f)
    combined = d["combined"]
    print(
        f"  {combined.graph.number_of_nodes()} nodes / "
        f"{combined.graph.number_of_edges()} edges / "
        f"{len(combined.runs)} runs "
        f"({len(combined.attacker_run_names)} attacker + "
        f"{len(combined.benign_run_names)} benign)"
    )
    print()

    out: dict = {}

    print("CHECK 1: Address overlap between train/test splits")
    out["check_1_address_overlap"] = check_address_overlap(combined)
    print(f"  {out['check_1_address_overlap']['verdict']}")
    print()

    print("CHECK 2: Feature-label leakage (per-feature Cohen's d)")
    out["check_2_feature_leakage"] = check_feature_label_leakage(combined)
    print(f"  {out['check_2_feature_leakage']['verdict']}")
    for f in out["check_2_feature_leakage"]["per_feature"]:
        marker = "  ⚠️" if f["trivial_separator"] else "    "
        print(
            f"  {marker} {f['feature']:22s} d={f['cohens_d']:+7.2f}  "
            f"attacker_nz={f['attacker_nonzero_fraction']:.2f}  "
            f"benign_nz={f['benign_nonzero_fraction']:.2f}"
        )
    print()

    print("CHECK 3: Leave-one-campaign-out cross-validation (GCN)")
    out["check_3_loco_cv"] = check_leave_one_campaign_out(combined)
    print(f"  {out['check_3_loco_cv']['verdict']}")
    print(
        f"  LOCO F1: mean={out['check_3_loco_cv']['loco_f1_mean']:.3f}, "
        f"std={out['check_3_loco_cv']['loco_f1_std']:.3f}, "
        f"min={out['check_3_loco_cv']['loco_f1_min']:.3f}, "
        f"max={out['check_3_loco_cv']['loco_f1_max']:.3f}"
    )
    print()

    print("CHECK 4: Mixer-only trivial rule baseline")
    out["check_4_mixer_only"] = check_mixer_only_baseline(combined)
    print(f"  {out['check_4_mixer_only']['verdict']}")
    print(
        f"  Trivial rule F1: {out['check_4_mixer_only']['f1']:.3f}  "
        f"vs GCN F1: 0.967"
    )
    print()

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=2))
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
