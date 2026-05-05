"""2-layer GCN baseline for transaction classification on Elliptic.

Reproduces the architecture from Weber et al. 2019, "Anti-Money Laundering in Bitcoin".
This is the *victim* model that the multi-agent attacker will later try to evade.

Reference baseline (Weber et al. 2019):
    F1 (illicit) ~ 0.41 on Elliptic temporal split
Modern re-implementations with class weighting and longer training reach ~ 0.55-0.70.
"""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F
from torch_geometric.nn import GCNConv


class GCN(nn.Module):
    def __init__(self, in_dim: int, hidden_dim: int = 100, out_dim: int = 2, dropout: float = 0.5):
        super().__init__()
        self.conv1 = GCNConv(in_dim, hidden_dim)
        self.conv2 = GCNConv(hidden_dim, out_dim)
        self.dropout = dropout

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        x = self.conv1(x, edge_index)
        x = F.relu(x)
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = self.conv2(x, edge_index)
        return x
