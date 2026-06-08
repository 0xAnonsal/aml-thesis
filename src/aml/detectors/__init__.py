"""AML detector ensemble — Elliptic baselines + Week 7 detection layer.

Two generations of code live side-by-side here, with distinct API
contracts:

  - Week 1-2 (Elliptic): plain torch.nn.Module classifiers — GCN and
    GAT — used in the very first baseline experiments on the Elliptic
    Bitcoin dataset. See gcn.py and gat.py.

  - Week 7 (simulated dataset): a unified Detector ABC with
    fit / predict / predict_proba interface, plus three concrete
    implementations:
        LouvainDetector       — community-detection baseline
        GCNDetector           — graph-conv-network baseline (PyG)
        MultiAgentDetector    — the thesis novelty: per-exchange
                                local detectors + cross-exchange
                                actor-level clustering on shared
                                feature fingerprints

Naming caveat — gcn.py:GCN (Week 1-2) and gnn.py:GCNDetector (Week 7)
are different things despite similar names. The first is a raw model
class; the second wraps a GCN in the Detector ABC with a transductive
training loop. See each module's docstring for the relationship.
"""
from __future__ import annotations

# Week 1-2 Elliptic-era model classes. Kept exported so existing
# imports (`from aml.detectors import GCN, GAT`) don't break.
from .gat import GAT
from .gcn import GCN

# Week 7 Detector ABC + concrete classes that share the same
# fit/predict/predict_proba surface and evaluation harness.
from .baselines import (
    Detector,
    LouvainDetector,
    PerExchangeDetector,
    derive_binary_labels,
)
from .eval import DetectorMetrics, evaluate, pretty_print
from .multi_agent import (
    MultiAgentDetector,
    actor_clustering_metrics,
    adjusted_rand_index,
    cluster_by_similarity,
    true_actor_clusters,
)

# GCNDetector requires torch + torch_geometric. Import lazily so the
# rest of the package stays importable on hosts without those deps.
try:
    from .gnn import GCNDetector
    _HAVE_GCN_DETECTOR = True
except ImportError:   # pragma: no cover — torch typically present
    _HAVE_GCN_DETECTOR = False

# Name → class lookup for training scripts that pick a model by string.
# Legacy Week 1-2 baselines keep their lowercase keys; Week 7 detectors
# get distinct keys so the two namespaces don't collide.
MODEL_REGISTRY: dict[str, type] = {
    "gcn": GCN,                            # Week 1-2 Elliptic baseline
    "gat": GAT,                            # Week 1-2 Elliptic baseline
    "louvain": LouvainDetector,            # Week 7 community baseline
    "multi_agent": MultiAgentDetector,     # Week 7 thesis novelty
}
if _HAVE_GCN_DETECTOR:
    MODEL_REGISTRY["gcn_detector"] = GCNDetector   # Week 7 GCN baseline

__all__ = [
    # Legacy Elliptic-era models
    "GCN", "GAT",
    # Week 7 Detector ABC + concrete classes
    "Detector",
    "LouvainDetector",
    "PerExchangeDetector",
    "MultiAgentDetector",
    # Eval helpers
    "DetectorMetrics", "evaluate", "pretty_print",
    "derive_binary_labels",
    # Cross-exchange clustering helpers
    "cluster_by_similarity",
    "true_actor_clusters",
    "adjusted_rand_index",
    "actor_clustering_metrics",
    # Registry
    "MODEL_REGISTRY",
]
if _HAVE_GCN_DETECTOR:
    __all__.append("GCNDetector")
