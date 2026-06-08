"""Placeholder — defender code lives in aml.detectors, not here.

Originally this package was intended to hold the "blue team" detector
code (mirroring `attackers/`). After scope refinement in Week 7 the
detector ensemble landed in `aml.detectors` instead, alongside the
Elliptic baselines from Week 1-2.

This package is kept (rather than deleted) so any external code that
imports `from aml import defenders` doesn't break; the import succeeds
and exposes nothing.

See aml.detectors for:
    LouvainDetector       (community-detection baseline)
    GCNDetector           (Weber-style GNN baseline)
    MultiAgentDetector    (thesis novelty — cross-exchange actor clustering)
"""
