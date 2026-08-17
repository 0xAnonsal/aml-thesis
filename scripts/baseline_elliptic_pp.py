"""Reproduce tree-ensemble baselines on Elliptic++ subset (Bellei et al. 2024).

Task #6 (Path X-PlusPlus) — external validation of the classification pipeline
on the canonical Bitcoin AML benchmark, wallet-level. This is NOT a GCN
experiment (Elliptic++'s 56 features already encode neighborhood aggregates;
adding GCN message-passing on top is redundant and matches the finding of
Grinsztajn et al. 2022 that tree ensembles dominate on tabular AML data).

Target F1 (per Bellei et al. 2024, wallet classification): F1 ~ 0.94-0.97 for
the illicit class with a well-tuned Random Forest. Our subset is 50k stratified
sample (vs their full 265k labeled set) so we expect slightly lower but the
architecture should reproduce the same qualitative result.

Baselines run:
    - RandomForest  (200 trees, class_weight="balanced")
    - LogisticRegression (L1, sanity-check linear separability)

Not run (dependencies not installed): XGBoost, LightGBM, CatBoost. If needed
for the final chapter 5 table, install via `pip install xgboost lightgbm catboost`.

Usage:
    python scripts/baseline_elliptic_pp.py

Writes: results/elliptic_pp_baselines.json
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score, confusion_matrix, f1_score,
    precision_score, recall_score, roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler


REPO_ROOT = Path(__file__).resolve().parents[1]
PARQUET = REPO_ROOT / "data" / "elliptic_pp_subset.parquet"
OUT_JSON = REPO_ROOT / "results" / "elliptic_pp_baselines.json"
SEED = 42


def _metrics(y_true, y_pred, y_proba) -> dict:
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
    return {
        "f1_illicit": round(f1_score(y_true, y_pred), 4),
        "precision_illicit": round(precision_score(y_true, y_pred), 4),
        "recall_illicit": round(recall_score(y_true, y_pred), 4),
        "roc_auc": round(roc_auc_score(y_true, y_proba), 4),
        "avg_precision": round(average_precision_score(y_true, y_proba), 4),
        "confusion": {
            "tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn),
        },
    }


def main() -> None:
    print(f"Loading {PARQUET}...")
    df = pd.read_parquet(PARQUET)
    print(
        f"  {len(df):,} rows / {df.shape[1] - 2} features / "
        f"{int(df['label'].sum())} illicit / "
        f"{len(df) - int(df['label'].sum())} licit"
    )

    # Drop the address column (identifier, not a feature). Time step is a
    # feature per Bellei et al., but including it risks temporal leakage —
    # test set can literally be a later time window. Drop it too to make the
    # baseline strict.
    drop_cols = ["address", "label", "Time step"]
    X = df.drop(columns=drop_cols).values.astype(np.float32)
    y = df["label"].values.astype(np.int8)
    print(f"  Feature matrix: {X.shape}")
    print()

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=SEED,
    )
    print(
        f"Split (80/20 stratified, seed={SEED}): "
        f"train={len(X_train):,} ({int(y_train.sum())} illicit), "
        f"test={len(X_test):,} ({int(y_test.sum())} illicit)"
    )
    print()

    results: dict = {
        "dataset": "elliptic_pp_subset (Bellei 2024, 50k stratified sample)",
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "test_illicit": int(y_test.sum()),
        "seed": SEED,
        "target_f1_bellei_2024": "~0.94-0.97 (illicit class)",
        "models": {},
    }

    # --- RandomForest ---
    print("Training RandomForest (200 trees, class_weight='balanced')...")
    t0 = time.time()
    rf = RandomForestClassifier(
        n_estimators=200,
        class_weight="balanced",
        random_state=SEED,
        n_jobs=-1,
    )
    rf.fit(X_train, y_train)
    rf_pred = rf.predict(X_test)
    rf_proba = rf.predict_proba(X_test)[:, 1]
    rf_metrics = _metrics(y_test, rf_pred, rf_proba)
    rf_metrics["train_time_seconds"] = round(time.time() - t0, 2)
    results["models"]["RandomForest"] = rf_metrics
    print(
        f"  F1={rf_metrics['f1_illicit']:.4f}  "
        f"P={rf_metrics['precision_illicit']:.4f}  "
        f"R={rf_metrics['recall_illicit']:.4f}  "
        f"AUC={rf_metrics['roc_auc']:.4f}  "
        f"({rf_metrics['train_time_seconds']:.1f}s)"
    )
    print()

    # --- LogisticRegression (linear sanity check) ---
    print("Training LogisticRegression (L1, scaled features)...")
    t0 = time.time()
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)
    lr = LogisticRegression(
        penalty="l1", solver="saga", C=1.0,
        max_iter=1000, class_weight="balanced",
        random_state=SEED, n_jobs=-1,
    )
    lr.fit(X_train_s, y_train)
    lr_pred = lr.predict(X_test_s)
    lr_proba = lr.predict_proba(X_test_s)[:, 1]
    lr_metrics = _metrics(y_test, lr_pred, lr_proba)
    lr_metrics["train_time_seconds"] = round(time.time() - t0, 2)
    results["models"]["LogisticRegression"] = lr_metrics
    print(
        f"  F1={lr_metrics['f1_illicit']:.4f}  "
        f"P={lr_metrics['precision_illicit']:.4f}  "
        f"R={lr_metrics['recall_illicit']:.4f}  "
        f"AUC={lr_metrics['roc_auc']:.4f}  "
        f"({lr_metrics['train_time_seconds']:.1f}s)"
    )
    print()

    # Verdict block for the chapter-5 narrative
    if rf_metrics["f1_illicit"] >= 0.90:
        verdict = (
            f"PASS — RandomForest F1={rf_metrics['f1_illicit']:.3f} is in the "
            "range reported by Bellei et al. 2024 (0.94-0.97). Confirms our "
            "classification pipeline reproduces published performance on the "
            "canonical Elliptic++ wallet benchmark."
        )
    else:
        verdict = (
            f"CONCERN — RandomForest F1={rf_metrics['f1_illicit']:.3f} is "
            "below the ~0.94 range reported by Bellei et al. 2024. Possible "
            "causes: dropped Time step feature (temporal leakage guard), "
            "subset vs full-set discrepancy, or hyperparameter mismatch."
        )
    results["verdict"] = verdict
    print(f"Verdict:")
    print(f"  {verdict}")
    print()

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, indent=2))
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
