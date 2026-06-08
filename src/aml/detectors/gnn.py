"""GCN baseline detector — graph neural network on the combined graph.

The "Weber-style" GNN baseline. Kipf & Welling's GCN (2017) is the
architecture Weber et al. (2019) applied to AML detection on Elliptic;
reproducing it here on the simulated dataset gives the standard
comparison point the thesis's multi-agent detector must beat — and is
the model the multi-agent attacker is specifically supposed to evade.

NOTE on naming — there are two GCN-related classes in this package:

  - detectors.gcn:GCN          (Week 1-2) — raw torch.nn.Module used
                                in the original Elliptic Bitcoin
                                baseline experiments. Takes a feature
                                tensor + edge_index, returns logits.
                                Doesn't implement the Detector ABC.

  - detectors.gnn:GCNDetector  (Week 7, this file) — wraps a GCN
                                model in the unified Detector ABC
                                with a transductive fit/predict loop
                                + feature extraction + class-imbalance
                                handling. Used in the simulated-data
                                evaluation alongside Louvain and
                                MultiAgentDetector.

If you're writing eval code, use GCNDetector. If you're reproducing
the Elliptic baseline numbers from Weber et al., use the raw GCN.

How it decides (one paragraph):
    Each address gets a feature vector — degree, log-sum of value
    moved in/out, unique counterparty count, plus the count of edges
    of each kind (transfer_eth, transfer_usdt, swap, mixer_deposit,
    mixer_withdraw). The GCN learns to aggregate each node's features
    with its neighbours' (and their neighbours', via stacked
    convolutions) and project to a binary attacker/benign probability.
    Trained with masked BCE on the labelled training subset; predicted
    over the full graph in one forward pass (transductive setup).

Same Detector ABC as LouvainDetector — fit / predict / predict_proba.
Evaluation harness from PR #36 works unchanged.

Optional dependency: PyTorch + PyTorch Geometric. The module imports
cleanly without them; instantiating GCNDetector raises ImportError
with install instructions. The feature extractor (extract_features)
is plain numpy + networkx and works regardless — useful for the
multi-agent detector PR (which may reuse the same features over a
non-PyG model).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np

from aml.detectors.baselines import Detector


try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch_geometric.nn import GCNConv
    _PYG_AVAILABLE = True
except ImportError:   # pragma: no cover — exercised by skip markers in tests
    _PYG_AVAILABLE = False


# Edge kinds emitted by aml.detectors.graph.to_networkx. Order matters —
# fixes the feature-column layout, which gets baked into trained
# weights. New edge kinds added later must go AT THE END.
EDGE_KINDS: tuple[str, ...] = (
    "transfer_eth", "transfer_usdt", "swap",
    "mixer_deposit", "mixer_withdraw",
)

# Per-node feature columns in fixed order. Same caveat as EDGE_KINDS —
# don't reorder without retraining. Keeping the layout explicit so a
# detector loaded from a saved checkpoint can sanity-check the schema.
FEATURE_NAMES: tuple[str, ...] = (
    "in_degree", "out_degree", "total_degree",
    "log_eth_in", "log_eth_out", "log_usdt_in", "log_usdt_out",
    "log_unique_in", "log_unique_out",
) + tuple(f"{k}_in" for k in EDGE_KINDS) + tuple(f"{k}_out" for k in EDGE_KINDS)

FEATURE_DIM: int = len(FEATURE_NAMES)


def extract_features(
    graph: nx.MultiDiGraph, node_order: list[str],
) -> np.ndarray:
    """Build a (N, FEATURE_DIM) float32 feature matrix.

    Features are computed PURELY from the visible chain trace — no
    peeking at node labels. Per node:
        - in/out/total degree
        - log(1+sum) of ETH and USDT value moved in/out
        - log(1+count) of unique counterparties in/out
        - per edge-kind in/out counts (log1p'd to compress the
          distribution; raw counts can span several orders of
          magnitude in a busy graph)

    Rows are in `node_order` order. Addresses not in `graph` get
    all-zero rows.
    """
    n = len(node_order)
    X = np.zeros((n, FEATURE_DIM), dtype=np.float32)
    if n == 0:
        return X

    node_to_idx = {addr: i for i, addr in enumerate(node_order)}
    kind_idx = {k: i for i, k in enumerate(EDGE_KINDS)}

    in_eth = np.zeros(n, dtype=np.float64)
    out_eth = np.zeros(n, dtype=np.float64)
    in_usdt = np.zeros(n, dtype=np.float64)
    out_usdt = np.zeros(n, dtype=np.float64)
    in_counterparties: list[set] = [set() for _ in range(n)]
    out_counterparties: list[set] = [set() for _ in range(n)]
    in_kind_counts = np.zeros((n, len(EDGE_KINDS)), dtype=np.float64)
    out_kind_counts = np.zeros((n, len(EDGE_KINDS)), dtype=np.float64)

    for u, v, data in graph.edges(data=True):
        if u not in node_to_idx or v not in node_to_idx:
            continue
        ui, vi = node_to_idx[u], node_to_idx[v]
        out_counterparties[ui].add(v)
        in_counterparties[vi].add(u)

        asset = data.get("asset")
        value = data.get("value", 0.0) or 0.0
        if asset == "ETH":
            out_eth[ui] += value
            in_eth[vi] += value
        elif asset == "USDT":
            out_usdt[ui] += value
            in_usdt[vi] += value

        kind = data.get("kind")
        if kind in kind_idx:
            ki = kind_idx[kind]
            out_kind_counts[ui, ki] += 1
            in_kind_counts[vi, ki] += 1

    in_deg = np.array(
        [graph.in_degree(a) if a in graph else 0 for a in node_order],
        dtype=np.float64,
    )
    out_deg = np.array(
        [graph.out_degree(a) if a in graph else 0 for a in node_order],
        dtype=np.float64,
    )

    X[:, 0] = in_deg
    X[:, 1] = out_deg
    X[:, 2] = in_deg + out_deg
    X[:, 3] = np.log1p(in_eth)
    X[:, 4] = np.log1p(out_eth)
    X[:, 5] = np.log1p(in_usdt)
    X[:, 6] = np.log1p(out_usdt)
    X[:, 7] = np.log1p([len(s) for s in in_counterparties])
    X[:, 8] = np.log1p([len(s) for s in out_counterparties])

    base = 9
    X[:, base:base + len(EDGE_KINDS)] = np.log1p(in_kind_counts)
    X[:, base + len(EDGE_KINDS):] = np.log1p(out_kind_counts)
    return X


# --- model + detector (PyG-only) ----------------------------------------


if _PYG_AVAILABLE:
    class _GCNModel(nn.Module):
        """Stacked GCN convolutions → linear binary-logit head.

        Small on purpose — 2 layers, 32 hidden dim by default. Larger
        models overfit our small simulated graphs; the value of a GCN
        baseline is to set the bar with the standard architecture, not
        to push performance with hyperparameter search.
        """

        def __init__(self, in_dim: int, hidden_dim: int,
                     num_layers: int, dropout: float):
            super().__init__()
            self.convs = nn.ModuleList()
            self.convs.append(GCNConv(in_dim, hidden_dim))
            for _ in range(num_layers - 1):
                self.convs.append(GCNConv(hidden_dim, hidden_dim))
            self.head = nn.Linear(hidden_dim, 1)
            self.dropout = dropout

        def forward(self, x, edge_index):
            for conv in self.convs:
                x = conv(x, edge_index)
                x = F.relu(x)
                x = F.dropout(x, p=self.dropout, training=self.training)
            return self.head(x).squeeze(-1)   # (N,) logits


@dataclass
class GCNDetector(Detector):
    """GCN binary classifier — same Detector interface as LouvainDetector.

    Args:
        hidden_dim: GCN hidden width (default 32).
        num_layers: stacked GCN layers (default 2).
        epochs:     training epochs (default 100).
        lr:         Adam learning rate (default 0.01).
        weight_decay: Adam L2 (default 5e-4).
        dropout:    per-layer dropout (default 0.1).
        seed:       torch + numpy RNG seed.
    """

    hidden_dim: int = 32
    num_layers: int = 2
    epochs: int = 100
    lr: float = 0.01
    weight_decay: float = 5e-4
    dropout: float = 0.1
    seed: int = 0

    model: Any = None
    node_order: list[str] = field(default_factory=list)
    node_to_idx: dict[str, int] = field(default_factory=dict)
    feature_mean: Any = None    # np.ndarray (FEATURE_DIM,)
    feature_std: Any = None
    _is_fitted: bool = False
    _logits: Any = None         # cached forward output (torch.Tensor)

    def __post_init__(self):
        if not _PYG_AVAILABLE:
            raise ImportError(
                "GCNDetector requires PyTorch + PyTorch Geometric. "
                "Install with: pip install torch torch-geometric"
            )

    def fit(
        self, graph: nx.MultiDiGraph, train_labels: dict[str, int],
    ) -> "GCNDetector":
        """Train the GCN. Transductive: features are computed over the
        FULL graph; loss is masked to the training-labelled nodes
        only; predictions are read off the same forward pass."""
        # Reset state so successive fits don't carry old weights.
        self.model = None
        self.node_order = []
        self.node_to_idx = {}
        self._logits = None
        self._is_fitted = True

        # Seed RNGs for reproducibility.
        torch.manual_seed(self.seed)
        if torch.cuda.is_available():   # pragma: no cover — CI typically CPU
            torch.cuda.manual_seed_all(self.seed)
        np.random.seed(self.seed)

        if graph.number_of_nodes() == 0:
            return self   # nothing to learn; predict_proba returns 0.5

        # Canonical node ordering (sorted → reproducible across runs).
        self.node_order = sorted(graph.nodes())
        self.node_to_idx = {a: i for i, a in enumerate(self.node_order)}

        # Build feature matrix and standardise (fit scaler on training
        # nodes only to avoid label leakage through statistics).
        X = extract_features(graph, self.node_order)
        train_idx = [
            self.node_to_idx[a] for a in self.node_order if a in train_labels
        ]
        if train_idx:
            self.feature_mean = X[train_idx].mean(axis=0)
            std = X[train_idx].std(axis=0)
            self.feature_std = np.where(std > 1e-9, std, 1.0)
        else:
            self.feature_mean = np.zeros(FEATURE_DIM, dtype=np.float32)
            self.feature_std = np.ones(FEATURE_DIM, dtype=np.float32)
        X_std = (X - self.feature_mean) / self.feature_std

        # Build PyG-compatible edge_index. Combined graph is a
        # MultiDiGraph but GCN needs an undirected simple graph;
        # collapse parallel edges and add reverse direction.
        edge_pairs = [
            (self.node_to_idx[u], self.node_to_idx[v])
            for u, v in graph.edges()
            if u in self.node_to_idx and v in self.node_to_idx
        ]
        if edge_pairs:
            ei = torch.tensor(edge_pairs, dtype=torch.long).t().contiguous()
            edge_index = torch.cat([ei, ei.flip(0)], dim=1)
        else:
            edge_index = torch.zeros((2, 0), dtype=torch.long)

        x = torch.tensor(X_std, dtype=torch.float32)
        n = len(self.node_order)
        y = torch.zeros(n, dtype=torch.float32)
        train_mask = torch.zeros(n, dtype=torch.bool)
        for addr, label in train_labels.items():
            if addr in self.node_to_idx:
                idx = self.node_to_idx[addr]
                y[idx] = float(label)
                train_mask[idx] = True

        self.model = _GCNModel(
            FEATURE_DIM, self.hidden_dim, self.num_layers, self.dropout,
        )

        if not train_mask.any():
            # No labels match graph nodes → can't supervise. Cache an
            # untrained forward pass so predict_proba is deterministic.
            self.model.eval()
            with torch.no_grad():
                self._logits = self.model(x, edge_index)
            return self

        # Class-imbalance handling: re-weight the positive class so
        # rare attacker nodes don't get swamped by benign in the loss.
        n_pos = float(y[train_mask].sum().item())
        n_neg = float(train_mask.sum().item()) - n_pos
        pos_weight = torch.tensor(
            [n_neg / n_pos if n_pos > 0 else 1.0], dtype=torch.float32,
        )

        optimizer = torch.optim.Adam(
            self.model.parameters(), lr=self.lr, weight_decay=self.weight_decay,
        )
        self.model.train()
        for _ in range(self.epochs):
            optimizer.zero_grad()
            logits = self.model(x, edge_index)
            loss = F.binary_cross_entropy_with_logits(
                logits[train_mask], y[train_mask], pos_weight=pos_weight,
            )
            loss.backward()
            optimizer.step()

        self.model.eval()
        with torch.no_grad():
            self._logits = self.model(x, edge_index)
        return self

    def predict_proba(self, nodes: list[str]) -> list[float]:
        self._check_fitted()
        if self._logits is None:
            # No graph was fitted → uninformative prior.
            return [0.5] * len(nodes)
        probas = torch.sigmoid(self._logits).cpu().numpy()
        return [
            float(probas[self.node_to_idx[a]]) if a in self.node_to_idx else 0.5
            for a in nodes
        ]

    def predict(self, nodes: list[str]) -> list[int]:
        return [int(p >= 0.5) for p in self.predict_proba(nodes)]

    def _check_fitted(self) -> None:
        if not self._is_fitted:
            raise RuntimeError(
                "GCNDetector not fitted yet — call .fit(graph, train_labels) first."
            )
