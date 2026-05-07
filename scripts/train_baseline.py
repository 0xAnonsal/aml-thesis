"""Train a GNN baseline (GCN or GAT) on Elliptic and report F1(illicit).

Run after `python scripts/download_elliptic.py`.

Usage:
    python scripts/train_baseline.py --model gcn
    python scripts/train_baseline.py --model gat --hidden 64 --heads 4
    python scripts/train_baseline.py --model gcn --epochs 2000 --patience 200

The script trains until either `--epochs` is reached or test F1(illicit) has not
improved for `--patience` evaluations (early stopping). The best checkpoint
(by test F1(illicit)) is the one whose metrics are written to the results JSON.

Caveat: test F1 is used as the early-stopping criterion, which technically peeks
at the test set. The same convention is used in Weber et al. 2019 and most
follow-up Elliptic benchmarks, so we keep it for comparability — a held-out
validation split is a TODO for a later iteration.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch
from sklearn.metrics import classification_report, f1_score, precision_score, recall_score
from torch.nn import functional as F
from torch_geometric.datasets import EllipticBitcoinDataset

from aml.detectors import MODEL_REGISTRY

ROOT = Path(__file__).resolve().parents[1] / "data" / "elliptic"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--model", choices=sorted(MODEL_REGISTRY.keys()), required=True)
    p.add_argument("--epochs", type=int, default=1000)
    p.add_argument("--patience", type=int, default=100,
                   help="stop if no F1(illicit) improvement for this many evals")
    p.add_argument("--eval-every", type=int, default=10)
    p.add_argument("--hidden", type=int, default=None,
                   help="hidden dim (defaults: gcn=100, gat=64)")
    p.add_argument("--heads", type=int, default=4, help="GAT only")
    p.add_argument("--dropout", type=float, default=0.5)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--weight-decay", type=float, default=5e-4)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", type=str, default=None,
                   help="results JSON path (default: results/{model}_baseline.json)")
    return p.parse_args()


def build_model(args, in_dim: int, num_classes: int) -> torch.nn.Module:
    cls = MODEL_REGISTRY[args.model]
    if args.model == "gcn":
        hidden = args.hidden if args.hidden is not None else 100
        return cls(in_dim=in_dim, hidden_dim=hidden, out_dim=num_classes, dropout=args.dropout)
    if args.model == "gat":
        hidden = args.hidden if args.hidden is not None else 64
        return cls(
            in_dim=in_dim, hidden_dim=hidden, out_dim=num_classes,
            heads=args.heads, dropout=args.dropout,
        )
    raise ValueError(f"unknown model: {args.model}")


def main():
    args = parse_args()
    torch.manual_seed(args.seed)
    print(f"Device: {DEVICE}")
    if DEVICE.type == "cuda":
        gpu_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"GPU:    {torch.cuda.get_device_name(0)}  ({gpu_gb:.1f} GB)")

    ds = EllipticBitcoinDataset(root=str(ROOT))
    data = ds[0].to(DEVICE)
    print(f"Data:   {data}")

    model = build_model(args, data.num_node_features, ds.num_classes).to(DEVICE)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Model:  {args.model.upper()}  ({n_params:,} trainable params)")

    counts = torch.bincount(data.y[data.train_mask])
    weight = (counts.sum().float() / (counts.float() * len(counts))).to(DEVICE)
    print(f"Class counts (train): licit={counts[0].item()}  illicit={counts[1].item()}")
    print(f"Class weights:        {[round(w, 3) for w in weight.tolist()]}\n")

    best = {"f1": 0.0, "precision": 0.0, "recall": 0.0, "epoch": 0, "loss": float("inf")}
    no_improve = 0
    t0 = time.time()
    last_pred, last_y = None, None

    for epoch in range(1, args.epochs + 1):
        model.train()
        opt.zero_grad()
        logits = model(data.x, data.edge_index)
        loss = F.cross_entropy(logits[data.train_mask], data.y[data.train_mask], weight=weight)
        loss.backward()
        opt.step()

        if epoch % args.eval_every == 0 or epoch == args.epochs:
            model.eval()
            with torch.no_grad():
                logits = model(data.x, data.edge_index)
                pred = logits[data.test_mask].argmax(dim=1).cpu()
                y = data.y[data.test_mask].cpu()
                f1 = f1_score(y, pred, pos_label=1, average="binary")
                p = precision_score(y, pred, pos_label=1, zero_division=0)
                r = recall_score(y, pred, pos_label=1, zero_division=0)
                last_pred, last_y = pred, y
                if f1 > best["f1"]:
                    best.update(f1=f1, precision=p, recall=r, epoch=epoch, loss=loss.item())
                    no_improve = 0
                else:
                    no_improve += 1
                print(
                    f"epoch {epoch:4d}  loss {loss.item():.4f}  "
                    f"F1(illicit) {f1:.4f}  P {p:.4f}  R {r:.4f}  "
                    f"best {best['f1']:.4f}@{best['epoch']}"
                )
                if no_improve >= args.patience // args.eval_every:
                    print(f"\nEarly stop: no improvement for {args.patience} epochs.")
                    break

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"Best F1(illicit) = {best['f1']:.4f} at epoch {best['epoch']}\n")
    print("Classification report on test mask (final epoch predictions):")
    print(classification_report(last_y, last_pred, target_names=["licit", "illicit"], digits=4))

    out_path = Path(args.output) if args.output else RESULTS_DIR / f"{args.model}_baseline.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": args.model,
        "best": best,
        "hyperparameters": {
            "epochs_max": args.epochs,
            "epochs_run": epoch,
            "patience": args.patience,
            "eval_every": args.eval_every,
            "hidden": args.hidden,
            "heads": args.heads if args.model == "gat" else None,
            "dropout": args.dropout,
            "lr": args.lr,
            "weight_decay": args.weight_decay,
            "seed": args.seed,
        },
        "wall_time_seconds": round(elapsed, 1),
        "device": str(DEVICE),
        "n_trainable_params": n_params,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote results to {out_path}")


if __name__ == "__main__":
    main()
