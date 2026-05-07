"""2-layer GAT baseline for transaction classification on Elliptic.

Multi-head graph attention used as a second victim detector alongside the GCN.
Reference: Veličković et al. 2018, "Graph Attention Networks".

Default shape (heads=4, hidden=64) is small enough to fit comfortably on a 4 GB
GPU when run on the full Elliptic graph.
"""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F
from torch_geometric.nn import GATConv


class GAT(nn.Module):
    def __init__(
        self,
        in_dim: int,
        hidden_dim: int = 64,
        out_dim: int = 2,
        heads: int = 4,
        dropout: float = 0.5,
    ):
        super().__init__()
        self.conv1 = GATConv(in_dim, hidden_dim, heads=heads, dropout=dropout)
        self.conv2 = GATConv(hidden_dim * heads, out_dim, heads=1, concat=False, dropout=dropout)
        self.dropout = dropout

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = self.conv1(x, edge_index)
        x = F.elu(x)
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = self.conv2(x, edge_index)
        return x
