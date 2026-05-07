from .gat import GAT
from .gcn import GCN

__all__ = ["GCN", "GAT"]

MODEL_REGISTRY = {"gcn": GCN, "gat": GAT}
