"""Evaluation metrics for the AML detectors.

A small wrapper around the standard scikit-learn binary-classification
metrics (precision, recall, F1, ROC-AUC) tailored to the AML setting:
the **attacker class is the minority**, so per-class metrics are more
informative than the macro-averages. We also surface the confusion
matrix counts directly because tutors of cybersecurity backgrounds
usually want to see TP / FP / TN / FN, not just F1.

Public API:
    DetectorMetrics       — dataclass holding the result of one evaluation
    evaluate(y_true, y_pred, y_proba=None) -> DetectorMetrics
    pretty_print(metrics) -> str

Designed to be detector-agnostic — call site is:
    pred = detector.predict(test_nodes)
    proba = detector.predict_proba(test_nodes)
    metrics = evaluate(true_labels, pred, proba)
    print(pretty_print(metrics))
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DetectorMetrics:
    """Binary classification metrics for an AML detector.

    Fields:
        n_total            — total predictions
        n_positive_true    — how many of those are attacker (class 1)
        n_positive_pred    — how many were predicted attacker
        tp / fp / tn / fn  — confusion matrix counts
        precision          — TP / (TP + FP); fraction of flagged that ARE attacker
        recall             — TP / (TP + FN); fraction of attackers we caught
        f1                 — harmonic mean of precision + recall
        accuracy           — (TP + TN) / total  (less useful with class imbalance)
        roc_auc            — Area under the ROC curve; None if probas not given
        per_class          — dict with 'attacker' and 'benign' sub-metrics
    """
    n_total: int
    n_positive_true: int
    n_positive_pred: int
    tp: int
    fp: int
    tn: int
    fn: int
    precision: float
    recall: float
    f1: float
    accuracy: float
    roc_auc: float | None = None
    per_class: dict = field(default_factory=dict)


def evaluate(
    y_true: list[int],
    y_pred: list[int],
    y_proba: list[float] | None = None,
) -> DetectorMetrics:
    """Compute binary metrics. Attacker = class 1.

    Args:
        y_true: ground-truth labels (0 = benign, 1 = attacker).
        y_pred: detector predictions.
        y_proba: optional per-prediction probability of class 1; if
            provided, ROC-AUC is computed.

    Returns DetectorMetrics. Handles edge cases (empty input, single-
    class input, no positives) without crashing; ROC-AUC becomes None
    when undefined.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"y_true ({len(y_true)}) and y_pred ({len(y_pred)}) length mismatch"
        )
    if y_proba is not None and len(y_proba) != len(y_true):
        raise ValueError(
            f"y_proba ({len(y_proba)}) and y_true ({len(y_true)}) length mismatch"
        )

    n = len(y_true)
    n_pos_true = sum(1 for y in y_true if y == 1)
    n_pos_pred = sum(1 for y in y_pred if y == 1)

    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0 else 0.0
    )
    accuracy = (tp + tn) / n if n > 0 else 0.0

    # Per-class breakdown — symmetric, using the "negative class" too.
    benign_tp = tn
    benign_fp = fn
    benign_fn = fp
    benign_precision = benign_tp / (benign_tp + benign_fp) if (benign_tp + benign_fp) > 0 else 0.0
    benign_recall = benign_tp / (benign_tp + benign_fn) if (benign_tp + benign_fn) > 0 else 0.0
    benign_f1 = (
        2 * benign_precision * benign_recall / (benign_precision + benign_recall)
        if (benign_precision + benign_recall) > 0 else 0.0
    )

    per_class = {
        "attacker": {"precision": precision, "recall": recall, "f1": f1,
                     "support": n_pos_true},
        "benign":   {"precision": benign_precision, "recall": benign_recall,
                     "f1": benign_f1, "support": n - n_pos_true},
    }

    roc_auc = None
    if y_proba is not None:
        roc_auc = _roc_auc(y_true, y_proba)

    return DetectorMetrics(
        n_total=n, n_positive_true=n_pos_true, n_positive_pred=n_pos_pred,
        tp=tp, fp=fp, tn=tn, fn=fn,
        precision=precision, recall=recall, f1=f1, accuracy=accuracy,
        roc_auc=roc_auc, per_class=per_class,
    )


def _roc_auc(y_true: list[int], y_proba: list[float]) -> float | None:
    """Mann-Whitney U / Wilcoxon-rank-sum formulation of ROC-AUC.

    Equivalent to sklearn.metrics.roc_auc_score for binary tasks,
    implemented inline to avoid a sklearn dependency just for this.
    Returns None when AUC is undefined (single-class y_true).
    """
    n_pos = sum(1 for y in y_true if y == 1)
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None

    # Pair each (y, proba) and sort by proba.
    indexed = sorted(enumerate(y_proba), key=lambda x: x[1])
    # Average rank for tied probas — standard for AUC.
    ranks = [0.0] * len(indexed)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0   # ranks are 1-indexed
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1

    sum_pos_ranks = sum(
        ranks[idx_in_sorted]
        for idx_in_sorted, (orig_idx, _) in enumerate(indexed)
        if y_true[orig_idx] == 1
    )
    auc = (sum_pos_ranks - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return auc


def pretty_print(m: DetectorMetrics) -> str:
    """Return a multi-line human-readable summary of a DetectorMetrics."""
    lines = [
        f"n_total = {m.n_total}  ({m.n_positive_true} attackers, "
        f"{m.n_total - m.n_positive_true} benign)",
        "",
        "Confusion matrix:",
        f"                    pred attacker   pred benign",
        f"  true attacker     {m.tp:>13d}   {m.fn:>11d}",
        f"  true benign       {m.fp:>13d}   {m.tn:>11d}",
        "",
        f"Attacker class:  precision={m.precision:.3f}  recall={m.recall:.3f}  "
        f"f1={m.f1:.3f}",
        f"Benign   class:  precision={m.per_class['benign']['precision']:.3f}  "
        f"recall={m.per_class['benign']['recall']:.3f}  "
        f"f1={m.per_class['benign']['f1']:.3f}",
        f"Accuracy = {m.accuracy:.3f}",
    ]
    if m.roc_auc is not None:
        lines.append(f"ROC-AUC  = {m.roc_auc:.3f}")
    return "\n".join(lines)
