"""Download and cache the Elliptic Bitcoin transaction dataset via PyTorch Geometric.

Usage:
    python scripts/download_elliptic.py
"""
from pathlib import Path

from torch_geometric.datasets import EllipticBitcoinDataset

ROOT = Path(__file__).resolve().parents[1] / "data" / "elliptic"


def main():
    print(f"Downloading Elliptic Bitcoin dataset to: {ROOT}")
    ROOT.mkdir(parents=True, exist_ok=True)
    ds = EllipticBitcoinDataset(root=str(ROOT))
    data = ds[0]
    print(f"\nLoaded: {data}")
    print(f"  Nodes:           {data.num_nodes:,}")
    print(f"  Edges:           {data.num_edges:,}")
    print(f"  Node features:   {data.num_node_features}")
    print(f"  Classes:         {ds.num_classes}")
    print(f"  Train mask (t<=34, labeled): {int(data.train_mask.sum()):,} nodes")
    print(f"  Test mask  (t>34, labeled):  {int(data.test_mask.sum()):,} nodes")
    train_y = data.y[data.train_mask]
    test_y = data.y[data.test_mask]
    print(f"  Train balance:   licit={int((train_y == 0).sum()):,}  illicit={int((train_y == 1).sum()):,}")
    print(f"  Test  balance:   licit={int((test_y == 0).sum()):,}  illicit={int((test_y == 1).sum()):,}")


if __name__ == "__main__":
    main()
