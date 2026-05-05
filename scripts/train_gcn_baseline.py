"""Train the GCN baseline on Elliptic and report F1 on the illicit class.

Reproduces Weber et al. 2019 GCN baseline.
Run after `python scripts/download_elliptic.py`.

Usage:
    python scripts/train_gcn_baseline.py
"""
from __future__ import annotations

import time
from pathlib import Path

import torch
from sklearn.metrics import classification_report, f1_score
from torch.nn import functional as F
from torch_geometric.datasets import EllipticBitcoinDataset

from aml.detectors.gcn import GCN

ROOT = Path(__file__).resolve().parents[1] / "data" / "elliptic"
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
EPOCHS = 200
HIDDEN = 100
LR = 1e-3
WEIGHT_DECAY = 5e-4
SEED = 42


def main():
    torch.manual_seed(SEED)
    print(f"Device: {DEVICE}")
    if DEVICE.type == "cuda":
        gpu_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"GPU:    {torch.cuda.get_device_name(0)}  ({gpu_gb:.1f} GB)")

    ds = EllipticBitcoinDataset(root=str(ROOT))
    data = ds[0].to(DEVICE)
    print(f"Data:   {data}")

    model = GCN(in_dim=data.num_node_features, hidden_dim=HIDDEN, out_dim=ds.num_classes).to(DEVICE)
    opt = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)

    counts = torch.bincount(data.y[data.train_mask])
    weight = (counts.sum().float() / (counts.float() * len(counts))).to(DEVICE)
    print(f"Class counts (train): licit={counts[0].item()}  illicit={counts[1].item()}")
    print(f"Class weights:        {[round(w, 3) for w in weight.tolist()]}\n")

    best_f1, best_epoch = 0.0, 0
    pred_test, y_test = None, None
    t0 = time.time()

    for epoch in range(1, EPOCHS + 1):
        model.train()
        opt.zero_grad()
        logits = model(data.x, data.edge_index)
        loss = F.cross_entropy(logits[data.train_mask], data.y[data.train_mask], weight=weight)
        loss.backward()
        opt.step()

        if epoch % 10 == 0 or epoch == EPOCHS:
            model.eval()
            with torch.no_grad():
                logits = model(data.x, data.edge_index)
                pred_test = logits[data.test_mask].argmax(dim=1).cpu()
                y_test = data.y[data.test_mask].cpu()
                f1_illicit = f1_score(y_test, pred_test, pos_label=1, average="binary")
                if f1_illicit > best_f1:
                    best_f1, best_epoch = f1_illicit, epoch
                print(
                    f"epoch {epoch:3d}  loss {loss.item():.4f}  "
                    f"test F1(illicit) {f1_illicit:.4f}  best {best_f1:.4f}@{best_epoch}"
                )

    print(f"\nDone in {time.time() - t0:.1f}s")
    print(f"Best F1(illicit) = {best_f1:.4f} at epoch {best_epoch}\n")
    print("Classification report on test mask (final epoch predictions):")
    print(classification_report(y_test, pred_test, target_names=["licit", "illicit"], digits=4))


if __name__ == "__main__":
    main()
