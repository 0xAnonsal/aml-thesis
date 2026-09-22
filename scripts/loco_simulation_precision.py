"""Split 80/20 + LOCO on the simulated dataset with per-fold precision/recall
for Louvain, GCN and MultiAgent (cosine), all on the SAME pickle.

Complements `audit_f1_memorization.py` (GCN only, F1 only, 20-run pickle) and
`loco_simulation_3detectors.py` (35-run pickle). Two changes in the protocol:

  1. Attacker folds and benign folds are reported SEPARATELY. When a benign
     campaign is held out, the test set has no positives, so F1 is 0 by
     construction (0/0) and averaging it in drags the mean down without
     measuring anything. Attacker folds report P/R/F1; benign folds report
     the false-positive rate (fraction of held-out benign addresses flagged).
  2. Precision is persisted per fold, so the cost of Louvain's `prior=0.5`
     fallback (communities with no training label are flagged by default)
     can be quantified instead of assumed.

Output: results/loco_simulation_precision.json

Usage:
    python scripts/loco_simulation_precision.py [--pkl PATH] [--benign-folds 40]
                                                [--detectors Louvain,GCN,MultiAgent]
"""
from __future__ import annotations

import argparse
import json
import pickle
import random
import statistics
import time
from pathlib import Path

from aml.detectors.baselines import LouvainDetector, derive_binary_labels
from aml.detectors.dataset import train_val_test_split
from aml.detectors.gnn import GCNDetector
from aml.detectors.multi_agent import MultiAgentDetector

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PKL = Path.home() / "aml-results" / "batch_2026-06-26" / "dataset.pkl"
OUT_JSON = REPO_ROOT / "results" / "loco_simulation_precision.json"
SEED = 42


def _split_nodes_by_runs(g, train_runs, test_runs):
    train_nodes, test_nodes = set(), set()
    for addr, data in g.nodes(data=True):
        runs = set(data.get("runs", []))
        if runs & test_runs:
            test_nodes.add(addr)
        elif runs & train_runs:
            train_nodes.add(addr)
    return train_nodes, test_nodes


def _prf(y_true, y_pred):
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4)}


def _mean_std(xs):
    if not xs:
        return None, None
    return round(statistics.mean(xs), 4), (round(statistics.stdev(xs), 4) if len(xs) > 1 else 0.0)


def _make(name, views):
    if name == "Louvain":
        return LouvainDetector(seed=SEED)
    if name == "GCN":
        return GCNDetector(seed=SEED)
    if name == "MultiAgent":
        return MultiAgentDetector(detector_factory=lambda: GCNDetector(seed=SEED, epochs=50))
    raise ValueError(name)


def _fit_predict(name, det, g, views, train_labels, test_addrs):
    if name == "MultiAgent":
        det.fit_per_view(views, train_labels)
    else:
        det.fit(g, train_labels)
    if name == "Louvain":
        pred = det.predict(test_addrs)
        n_prior = sum(1 for a in test_addrs
                      if det.community_proba.get(det.community_index.get(a), det.prior) == det.prior)
    else:
        pred = [1 if p >= 0.5 else 0 for p in det.predict_proba(test_addrs)]
        n_prior = None
    return pred, n_prior


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkl", type=Path, default=DEFAULT_PKL)
    ap.add_argument("--benign-folds", type=int, default=40,
                    help="number of benign runs to hold out in LOCO (sampled with SEED); 0 = all")
    ap.add_argument("--detectors", default="Louvain,GCN,MultiAgent")
    args = ap.parse_args()
    detectors = [s.strip() for s in args.detectors.split(",") if s.strip()]

    with args.pkl.open("rb") as f:
        d = pickle.load(f)
    combined = d["combined"]
    views = d["views"]
    g = combined.graph
    all_runs = list(combined.all_run_names)
    attacker_runs = list(combined.attacker_run_names)
    benign_runs = [r for r in all_runs if r not in set(attacker_runs)]
    bin_labels = derive_binary_labels(combined.node_labels)
    print(f"{args.pkl}: {g.number_of_nodes()} nodes / {g.number_of_edges()} edges / "
          f"{len(all_runs)} runs ({len(attacker_runs)} attacker, {len(benign_runs)} benign) / "
          f"{len(bin_labels)} labelled, {sum(bin_labels.values())} positive")

    # ---------- 80/20 split by run (seed 42) ----------
    train_runs, _, test_runs = train_val_test_split(all_runs, ratios=(0.8, 0.0, 0.2), seed=SEED)
    train_nodes, test_nodes = _split_nodes_by_runs(g, set(train_runs), set(test_runs))
    train_labels = {a: bin_labels[a] for a in train_nodes if a in bin_labels}
    test_labels = {a: bin_labels[a] for a in test_nodes if a in bin_labels}
    test_addrs = sorted(test_labels)
    y_true = [test_labels[a] for a in test_addrs]
    split = {"train_runs": len(train_runs), "test_runs": len(test_runs),
             "train_attacker_runs": sum(r in set(attacker_runs) for r in train_runs),
             "test_attacker_runs": sum(r in set(attacker_runs) for r in test_runs),
             "train_labeled": len(train_labels), "train_positive": sum(train_labels.values()),
             "test_labeled": len(y_true), "test_positive": sum(y_true), "detectors": {}}
    print(f"split 80/20: {split['train_runs']} train runs ({split['train_attacker_runs']} attacker) / "
          f"{split['test_runs']} test runs ({split['test_attacker_runs']} attacker); "
          f"test labelled {split['test_labeled']} (pos {split['test_positive']})")
    for name in detectors:
        t0 = time.time()
        pred, n_prior = _fit_predict(name, _make(name, views), g, views, train_labels, test_addrs)
        m = _prf(y_true, pred)
        m["fit_time_s"] = round(time.time() - t0, 1)
        if n_prior is not None:
            m["n_test_in_unlabelled_community"] = n_prior
        split["detectors"][name] = m
        print(f"  {name:10s} F1={m['f1']:.3f} P={m['precision']:.3f} R={m['recall']:.3f} "
              f"(tp={m['tp']} fp={m['fp']} fn={m['fn']} tn={m['tn']}) {m['fit_time_s']}s")

    # ---------- LOCO ----------
    rng = random.Random(SEED)
    held = list(attacker_runs)
    held += benign_runs if args.benign_folds == 0 else rng.sample(benign_runs, min(args.benign_folds, len(benign_runs)))
    folds = []
    t0 = time.time()
    for i, held_out in enumerate(held):
        tr = set(all_runs) - {held_out}
        train_nodes, test_nodes = _split_nodes_by_runs(g, tr, {held_out})
        train_labels = {a: bin_labels[a] for a in train_nodes if a in bin_labels}
        test_labels = {a: bin_labels[a] for a in test_nodes if a in bin_labels}
        test_addrs = sorted(test_labels)
        y_true = [test_labels[a] for a in test_addrs]
        fold = {"fold": i, "held_out": held_out,
                "kind": "attacker" if held_out in set(attacker_runs) else "benign",
                "n_labeled_test": len(y_true), "n_positive_test": sum(y_true), "detectors": {}}
        if not test_addrs:
            fold["skipped"] = True
            folds.append(fold)
            continue
        for name in detectors:
            pred, n_prior = _fit_predict(name, _make(name, views), g, views, train_labels, test_addrs)
            m = _prf(y_true, pred)
            m["n_flagged"] = sum(pred)
            if n_prior is not None:
                m["n_test_in_unlabelled_community"] = n_prior
            if fold["kind"] == "benign":
                m["fpr"] = round(m["fp"] / len(y_true), 4)
            fold["detectors"][name] = m
        folds.append(fold)
        print(f"  [{i + 1:2d}/{len(held)}] {held_out[20:]:28s} {fold['kind']:8s} "
              + " ".join(f"{n}: F1={fold['detectors'][n]['f1']:.3f} P={fold['detectors'][n]['precision']:.3f} "
                         f"R={fold['detectors'][n]['recall']:.3f}" for n in detectors)
              + f"  ({time.time() - t0:.0f}s)", flush=True)

    summary = {}
    for name in detectors:
        att = [f["detectors"][name] for f in folds if f["kind"] == "attacker" and not f.get("skipped")]
        ben = [f["detectors"][name] for f in folds if f["kind"] == "benign" and not f.get("skipped")]
        s = {"n_attacker_folds": len(att), "n_benign_folds": len(ben)}
        for k in ("f1", "precision", "recall"):
            s[f"attacker_{k}_mean"], s[f"attacker_{k}_std"] = _mean_std([m[k] for m in att])
        s["attacker_f1_min"] = min(m["f1"] for m in att) if att else None
        s["attacker_f1_max"] = max(m["f1"] for m in att) if att else None
        tp = sum(m["tp"] for m in att); fp = sum(m["fp"] for m in att); fn = sum(m["fn"] for m in att)
        s["attacker_pooled_precision"] = round(tp / (tp + fp), 4) if tp + fp else None
        s["attacker_pooled_recall"] = round(tp / (tp + fn), 4) if tp + fn else None
        s["benign_fpr_mean"], s["benign_fpr_std"] = _mean_std([m["fpr"] for m in ben])
        s["benign_fp_total"] = sum(m["fp"] for m in ben)
        s["benign_n_total"] = sum(m["fp"] + m["tn"] for m in ben)
        allf = [m["f1"] for m in att] + [m["f1"] for m in ben]
        s["all_folds_f1_mean_INCLUDING_benign_zeros"] = round(statistics.mean(allf), 4) if allf else None
        s["delta_f1_vs_split"] = round(s["attacker_f1_mean"] - split["detectors"][name]["f1"], 4) if att else None
        summary[name] = s
        print(f"{name:10s} attacker folds: F1={s['attacker_f1_mean']}±{s['attacker_f1_std']} "
              f"P={s['attacker_precision_mean']} R={s['attacker_recall_mean']} "
              f"(pooled P={s['attacker_pooled_precision']}) | benign FPR={s['benign_fpr_mean']} "
              f"({s['benign_fp_total']}/{s['benign_n_total']}) | all-fold F1 incl. zeros="
              f"{s['all_folds_f1_mean_INCLUDING_benign_zeros']} | dF1 vs split={s['delta_f1_vs_split']}")

    out = {"dataset": str(args.pkl), "nodes": g.number_of_nodes(), "edges": g.number_of_edges(),
           "n_runs": len(all_runs), "n_attacker_runs": len(attacker_runs), "seed": SEED,
           "protocol": "split 80/20 by run (seed 42) + leave-one-run-out; attacker folds -> P/R/F1 on "
                       "held-out labelled nodes; benign folds -> FPR on held-out labelled benign nodes",
           "split_80_20": split, "loco_summary": summary, "loco_per_fold": folds}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=2))
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
