"""Multi-agent collaborative detector — the thesis's novelty claim.

Each "exchange" trains its own local detector on its partial view of
the combined transaction graph (Louvain or GCN — configurable). Then
all exchanges share per-address FEATURE FINGERPRINTS (just the
numeric vectors from gnn.extract_features, not the raw graph data) to
a coordinator that builds a cross-exchange similarity graph. Connected
components in that similarity graph = SUSPECTED SAME-ACTOR groups.

This is the locked-scope novelty: actor-level smurfing detection
under partial visibility. No single exchange can recover the full
laundering actor; the multi-agent detector recovers them by
combining local predictions with cross-exchange embedding similarity.

Public surface:

  MultiAgentDetector(detector_factory, similarity_threshold=0.95)
    .fit_per_view(views, train_labels) -> self
    .predict(nodes)                    -> list[int]     (binary)
    .predict_proba(nodes)              -> list[float]   (binary)
    .predict_actors(nodes)             -> list[int]     (cluster ids; -1 = unknown)
    .actor_clusters                    -> dict[address, cluster_id]

  cluster_by_similarity(features, threshold) -> dict[address, cluster_id]
    Standalone clustering primitive — exposed for testing and reuse.

  true_actor_clusters(addresses_dict, run_kinds) -> dict[address, int]
    Build the ground-truth actor map from per-address run membership.
    Each attacker run = one actor (its source + burners + funded exits).
    Benign users are singletons (each its own "actor").

  adjusted_rand_index(true_labels, pred_labels) -> float
    Standalone ARI implementation (no sklearn dep) for evaluating
    the clustering output against ground truth.

  actor_clustering_metrics(true_clusters, pred_clusters) -> dict
    Bundled metrics (n_clusters, ARI, homogeneity, completeness)
    for the actor-clustering output.

The binary attacker classification still uses the same Detector ABC,
so the eval harness from #36 works unchanged on the per-node side.
The new ARI + clustering metrics are exposed separately because they
score a fundamentally different output type (cluster assignments,
not binary labels).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

import networkx as nx
import numpy as np

from aml.detectors.baselines import Detector, PerExchangeDetector
from aml.detectors.gnn import FEATURE_DIM, FEATURE_NAMES, extract_features


# Sentinel for "this address has no cluster assignment" — used in
# predict_actors when an address isn't visible to any exchange the
# detector was fitted on.
NO_CLUSTER: int = -1


# --- standalone clustering primitive -------------------------------------


def cluster_by_similarity(
    features: dict[str, np.ndarray],
    *,
    threshold: float = 0.95,
) -> dict[str, int]:
    """Cluster addresses by cosine-similarity-of-features connectivity.

    Build a similarity graph: edge between two addresses if their
    feature vectors have cosine similarity >= threshold. Connected
    components = clusters. Returns {address: cluster_id}.

    O(N^2) in number of addresses — fine for the thesis scale (a few
    hundred per dataset); would need approximate-NN for production.

    Special cases:
      - empty input → {}
      - zero-norm features (all-zero) → that address is its own
        singleton (cosine undefined; we treat as dissimilar to all)
    """
    addresses = list(features.keys())
    if not addresses:
        return {}

    # Normalise features to unit norm so cosine = dot product. Handle
    # zero-norm vectors (isolated nodes with no edges) by leaving them
    # at zero — they'll have similarity 0 to everything else.
    matrix = np.array([features[a] for a in addresses], dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    safe_norms = np.where(norms > 1e-9, norms, 1.0)   # avoid div-by-zero
    matrix = matrix / safe_norms                       # divide all rows safely
    matrix = np.where(norms > 1e-9, matrix, 0.0)       # zero out the zero-norm rows

    # Build similarity graph
    g = nx.Graph()
    g.add_nodes_from(addresses)
    n = len(addresses)
    for i in range(n):
        for j in range(i + 1, n):
            sim = float(matrix[i] @ matrix[j])
            if sim >= threshold:
                g.add_edge(addresses[i], addresses[j])

    # Connected components → cluster ids
    clusters: dict[str, int] = {}
    for cid, component in enumerate(nx.connected_components(g)):
        for addr in component:
            clusters[addr] = cid
    return clusters


# --- detector ------------------------------------------------------------


@dataclass
class MultiAgentDetector(Detector):
    """Collaborative detector: per-exchange local + cross-exchange clustering.

    The binary classification (predict / predict_proba) is identical to
    PerExchangeDetector with the same factory. The novelty is
    predict_actors() which exposes the cross-exchange actor clustering
    computed at fit time.

    Args:
        detector_factory: zero-arg callable returning a fresh local
            Detector (e.g. lambda: GCNDetector(epochs=100, seed=0)).
            Used to instantiate one detector per ExchangeView.
        similarity_threshold: cosine-similarity cutoff for actor
            clustering. Higher = fewer, tighter clusters. Default
            0.95 is conservative; tune empirically per dataset.
        prior: fallback probability for addresses visible to no
            exchange. Default 0.5.
    """

    detector_factory: Callable[[], Detector]
    similarity_threshold: float = 0.95
    prior: float = 0.5

    # populated by fit_per_view
    _binary: Any = None                     # PerExchangeDetector
    actor_clusters: dict[str, int] = field(default_factory=dict)

    def fit_per_view(
        self, views, train_labels: dict[str, int],
    ) -> "MultiAgentDetector":
        """Train per-exchange detectors AND compute cross-exchange clusters."""
        # Phase 1: train binary classifiers per exchange (reuses #36).
        self._binary = PerExchangeDetector(
            detector_factory=self.detector_factory, prior=self.prior,
        ).fit_per_view(views, train_labels)

        # Phase 2: cross-exchange actor clustering on shared features.
        # Each exchange computes features for its addresses; coordinator
        # aggregates and clusters. The aggregation step is the
        # "privacy-respecting" sharing — fingerprints only, no raw graph.
        all_features: dict[str, np.ndarray] = {}
        for view in views:
            node_order = sorted(view.visible_addresses)
            X = extract_features(view.visible_subgraph, node_order)
            for i, addr in enumerate(node_order):
                # Address may appear in multiple exchanges (shared
                # contracts). Average the feature vectors across
                # exchanges in that case — the contract is what it is
                # regardless of which exchange sees it, so averaging
                # is appropriate.
                if addr in all_features:
                    all_features[addr] = (all_features[addr] + X[i]) / 2.0
                else:
                    all_features[addr] = X[i].astype(np.float64)

        # Restrict clustering to addresses the binary classifier
        # predicts as POSITIVE. Rationale: benign users share generic
        # transaction-shape features (transfers + swaps + low degree)
        # which, under cosine-similarity + connected-components
        # clustering, collapse all of them into a single giant
        # component. That over-clustering wrecks ARI on the actor
        # task because the truth is "N benign singletons + a small
        # number of attacker actors each with several wallets". By
        # only clustering predicted-positives, the similarity graph
        # is restricted to the wallets we actually want to group into
        # actor identities. Benigns (and false-negative attackers)
        # get singleton actor ids — which is the correct structure
        # for benigns and a downstream cost of the binary classifier
        # missing a positive for false negatives.
        all_addrs = sorted(all_features.keys())
        binary_preds = self._binary.predict(all_addrs) if all_addrs else []
        positive_features = {
            addr: all_features[addr]
            for addr, pred in zip(all_addrs, binary_preds)
            if pred == 1
        }

        clusters_on_positives = cluster_by_similarity(
            positive_features, threshold=self.similarity_threshold,
        )

        # Compose final actor_clusters: positives get their cluster id
        # from cosine-similarity grouping; negatives get a fresh
        # singleton id each. Preserves the truth-compatible
        # "one-actor-per-benign-user" structure.
        next_singleton_id = (
            max(clusters_on_positives.values()) + 1
            if clusters_on_positives else 0
        )
        final_clusters: dict[str, int] = dict(clusters_on_positives)
        for addr in all_addrs:
            if addr not in final_clusters:
                final_clusters[addr] = next_singleton_id
                next_singleton_id += 1

        self.actor_clusters = final_clusters
        return self

    # Detector ABC interface — same behaviour as PerExchangeDetector.
    def fit(self, graph, train_labels):
        raise RuntimeError(
            "Use fit_per_view(views, train_labels) — MultiAgentDetector "
            "needs the list of ExchangeView objects."
        )

    def predict(self, nodes: list[str]) -> list[int]:
        if self._binary is None:
            raise RuntimeError("MultiAgentDetector not fitted — call fit_per_view first.")
        return self._binary.predict(nodes)

    def predict_proba(self, nodes: list[str]) -> list[float]:
        if self._binary is None:
            raise RuntimeError("MultiAgentDetector not fitted — call fit_per_view first.")
        return self._binary.predict_proba(nodes)

    def predict_actors(self, nodes: list[str]) -> list[int]:
        """Return suspected actor cluster id per node. NO_CLUSTER if unknown."""
        if self._binary is None:
            raise RuntimeError("MultiAgentDetector not fitted — call fit_per_view first.")
        return [self.actor_clusters.get(a, NO_CLUSTER) for a in nodes]


# --- ground-truth actor map ---------------------------------------------


def true_actor_clusters(
    runs: list,                                # list[RunData] from dataset.combine_runs
) -> dict[str, int]:
    """Build the ground-truth address → actor_id map from RunData list.

    One ACTOR per attacker run — the attacker's source wallet, every
    burner the agent generated in that run, plus the funded clean exits
    that received its money. Benign users are individual actors (each
    their own cluster). Contracts and infrastructure are excluded.

    This is what predict_actors() is evaluated against via the
    Adjusted Rand Index.
    """
    next_actor_id = 0
    out: dict[str, int] = {}

    for run in runs:
        if run.kind == "attacker":
            addrs = run.addresses
            members: set[str] = set()
            src = addrs.get("source_wallet")
            if src:
                members.add(src)
            members.update(addrs.get("burners_generated_during_campaign") or [])
            members.update(addrs.get("clean_exits_funded") or [])
            for addr in members:
                out[addr] = next_actor_id
            next_actor_id += 1
        elif run.kind == "benign":
            # Each benign user is its own actor (no shared identity).
            for u in run.addresses.get("benign_users") or []:
                out[u] = next_actor_id
                next_actor_id += 1
    return out


# --- evaluation: clustering metrics --------------------------------------


def adjusted_rand_index(
    y_true: list[int], y_pred: list[int],
) -> float:
    """Adjusted Rand Index between two clusterings.

    Returns a value in [-1, 1]: 1.0 = identical clusterings, 0.0 =
    random labelling, < 0 = worse than random. Standard reference:
    Hubert & Arabie (1985). Implementation is the contingency-table /
    Counter-pair formula; no sklearn dependency.

    Returns 1.0 for a trivial perfect case (n <= 1).
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"y_true ({len(y_true)}) and y_pred ({len(y_pred)}) length mismatch"
        )
    n = len(y_true)
    if n <= 1:
        return 1.0   # trivially identical

    contingency: Counter = Counter(zip(y_true, y_pred))
    true_counts: Counter = Counter(y_true)
    pred_counts: Counter = Counter(y_pred)

    def _comb2(x: int) -> int:
        return x * (x - 1) // 2

    sum_c = sum(_comb2(v) for v in contingency.values())
    sum_t = sum(_comb2(v) for v in true_counts.values())
    sum_p = sum(_comb2(v) for v in pred_counts.values())

    total = _comb2(n)
    if total == 0:
        return 1.0

    expected = (sum_t * sum_p) / total
    max_index = (sum_t + sum_p) / 2.0
    if max_index == expected:
        # Degenerate case (all in one cluster on one side) — treat as 1.0
        return 1.0
    return (sum_c - expected) / (max_index - expected)


def actor_clustering_metrics(
    true_clusters: dict[str, int],
    pred_clusters: dict[str, int],
) -> dict:
    """Compare predicted actor clusters against the ground-truth map.

    Returns:
        n_addresses          — addresses with both true and pred labels
        n_clusters_true      — number of true actors over the common set
        n_clusters_pred      — number of predicted clusters
        ari                  — Adjusted Rand Index (or None if undefined)
        homogeneity          — fraction of predicted clusters that are
                               pure (single true actor)
        completeness         — fraction of true actors that are entirely
                               in one predicted cluster
    """
    common = sorted(set(true_clusters) & set(pred_clusters))
    if not common:
        return {
            "n_addresses": 0,
            "n_clusters_true": 0,
            "n_clusters_pred": 0,
            "ari": None,
            "homogeneity": None,
            "completeness": None,
        }

    y_true = [true_clusters[a] for a in common]
    y_pred = [pred_clusters[a] for a in common]

    # Homogeneity: of the predicted clusters, how many are pure?
    pred_to_trues: dict[int, set] = {}
    for p, t in zip(y_pred, y_true):
        pred_to_trues.setdefault(p, set()).add(t)
    pure_pred = sum(1 for trues in pred_to_trues.values() if len(trues) == 1)
    homogeneity = pure_pred / len(pred_to_trues) if pred_to_trues else None

    # Completeness: of the true actors, how many are entirely in one
    # predicted cluster?
    true_to_preds: dict[int, set] = {}
    for t, p in zip(y_true, y_pred):
        true_to_preds.setdefault(t, set()).add(p)
    complete_true = sum(1 for preds in true_to_preds.values() if len(preds) == 1)
    completeness = complete_true / len(true_to_preds) if true_to_preds else None

    return {
        "n_addresses": len(common),
        "n_clusters_true": len(set(y_true)),
        "n_clusters_pred": len(set(y_pred)),
        "ari": adjusted_rand_index(y_true, y_pred),
        "homogeneity": homogeneity,
        "completeness": completeness,
    }


# --- LLM Defender Coordinator -------------------------------------------
# Chema-validated architecture (2026-07-15): ML filter → LLM agent.
# Parity with attacker Coordinator (Opus over sub-agents Placement/Layering/
# Integration). Same shape, opposite goal: instead of orchestrating laundering
# stages, orchestrate cross-exchange actor identification.
#
# Model choices (locked 2026-07-18, ~$0.42 for 420-campaign bulk eval):
#   - Haiku 4.5 for bulk experiments
#   - Sonnet 4.6 for headline runs (~$0.28 for 20 attacker campaigns)
#   - Opus 4.7 for qualitative demo (~$0.36 for 5 campaigns)


_LLM_COORDINATOR_SYSTEM_PROMPT = """You are an anti-money-laundering (AML) coordinator for a federation of cryptocurrency exchanges. Each exchange has independently flagged addresses as suspicious based on its local partial view of the on-chain graph.

Your task: **identify distinct CAMPAIGNS / real-world actors** — group addresses that were controlled and operated by the SAME criminal enterprise as part of ONE coordinated laundering operation.

You DO NOT see raw transaction data. You only receive, per address:
  1. Which exchange flagged it
  2. A 19-dimensional feature fingerprint (degree, log-volume, unique counterparties, per-edge-kind counts including mixer_deposit/withdraw)
  3. The exchange's local confidence score

CAMPAIGN STRUCTURE — CRITICAL:
- Each attacker campaign typically produces **5 to 30 addresses** (source wallet, burners, clean exits)
- A single flagged batch may contain **10 to 25 distinct campaigns** running in parallel
- **Prefer many mid-sized clusters (5-15 addresses each) over few huge behavioral archetypes**
- DO NOT lump all "mixer users" into one cluster — different campaigns use mixers independently
- DO NOT lump all "swap-heavy" addresses together — that's a behavioral pattern, not an actor identity

REASONING PRINCIPLES:
- Similar fingerprints across exchanges → possible same actor (using multiple KYC identities)
- IDENTICAL fingerprints on same exchange → same campaign's siblings (burners)
- Group by *specific* volume magnitude + edge-count similarity, not just "both use mixer"
- Cross-exchange grouping is valuable but SHOULD NOT be your only clustering signal
- Aim for a cluster count in the range 10-25 to match typical batch structure
- Singletons should be RARE — only for truly unique fingerprints; most addresses belong to some coordinated campaign

OUTPUT (strict JSON, single valid JSON object, no other text):
{
  "actor_clusters": {
    "0": ["0x...", "0x..."],
    "1": ["0x..."],
    "2": ["0x...", "0x...", "0x..."]
  },
  "overall_reasoning": "one paragraph naming the main clusters and the archetype/signal that groups them"
}

The compact `{cluster_id → [addresses]}` map format is REQUIRED — do not include per-cluster reasoning in the JSON (put everything in `overall_reasoning`). This keeps output tractable for large batches.

Every input address MUST appear in exactly one cluster. Cluster IDs must be integer strings starting from "0". Aim for 10-25 total clusters — fewer means you're lumping distinct actors, more means you're over-splitting one campaign."""


def _format_address_for_llm(
    addr: str, exchange: str, fingerprint: np.ndarray, local_conf: float,
) -> str:
    """Compact human-readable fingerprint for the LLM prompt.

    Trades exhaustive precision (all 19 features) for prompt brevity —
    we surface the top-signal features by name+value so the LLM can
    reason about them without wading through a raw float dump.

    IMPORTANT: uses FULL address (not truncated). Earlier version used
    `addr[:10]...addr[-4:]` for readability, but the LLM then returned
    those abbreviated forms in its JSON output, which the parser could
    not match against the full-address ground-truth set (→ 0 clusters
    parsed, fallback triggered). Full addresses in the prompt cost ~30
    extra tokens each vs the abbreviated form, but the LLM's output is
    then directly usable without post-processing.
    """
    # Round to 2 decimals to keep the prompt short; the LLM doesn't
    # need 6-decimal precision to reason about relative magnitudes.
    features_str = ", ".join(
        f"{name}={fingerprint[i]:.2f}"
        for i, name in enumerate(FEATURE_NAMES)
        if fingerprint[i] > 0.01   # skip zero-valued features to save tokens
    ) or "(all features near zero — likely isolated node)"
    return (
        f"- {addr} "
        f"[exchange={exchange}, local_conf={local_conf:.2f}]\n"
        f"    features: {features_str}"
    )


def _build_llm_user_prompt(
    per_exchange_flagged: dict[str, list[tuple[str, np.ndarray, float]]],
) -> str:
    """Assemble the user-turn prompt from per-exchange flagged addresses.

    per_exchange_flagged: {exchange_name → [(address, fingerprint, local_conf), ...]}
    """
    parts = ["Federated flagged addresses (each exchange sees only its own users):\n"]
    for ex_name in sorted(per_exchange_flagged.keys()):
        entries = per_exchange_flagged[ex_name]
        if not entries:
            continue
        parts.append(f"\n### {ex_name} ({len(entries)} flagged)")
        for addr, fingerprint, conf in entries:
            parts.append(_format_address_for_llm(addr, ex_name, fingerprint, conf))

    total = sum(len(v) for v in per_exchange_flagged.values())
    parts.append(
        f"\nIdentify actor clusters across the {total} flagged addresses above. "
        "Return strict JSON only."
    )
    return "\n".join(parts)


def _parse_llm_clusters(
    llm_text: str, valid_addresses: set[str],
) -> tuple[dict[str, int], str]:
    """Parse the LLM's JSON output into {address → cluster_id} + reasoning.

    Robust to LLM sometimes wrapping JSON in markdown fences or adding
    prose before/after. Returns ({}, "") on unrecoverable parse errors —
    caller should fall back to cosine similarity as safety net.
    """
    import json
    import re

    # Strip markdown fences if present
    text = llm_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        # Find first { to last } — tolerates prose before/after
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}, ""

    raw_clusters = parsed.get("actor_clusters", [])
    reasoning = parsed.get("overall_reasoning", "")

    address_to_cluster: dict[str, int] = {}

    # Format A: compact dict {cluster_id: [addresses]} — preferred format
    if isinstance(raw_clusters, dict):
        for cid_key, addresses in raw_clusters.items():
            try:
                cid = int(cid_key)
            except (TypeError, ValueError):
                continue
            if not isinstance(addresses, list):
                continue
            for a in addresses:
                if a in valid_addresses:
                    address_to_cluster[a] = cid
    # Format B: verbose list [{cluster_id, addresses, reasoning}] — legacy
    elif isinstance(raw_clusters, list):
        for entry in raw_clusters:
            if not isinstance(entry, dict):
                continue
            cid = entry.get("cluster_id")
            addresses = entry.get("addresses", [])
            if not isinstance(cid, int):
                continue
            for a in addresses:
                if a in valid_addresses:
                    address_to_cluster[a] = cid

    return address_to_cluster, reasoning


@dataclass
class LLMDefenderCoordinator(Detector):
    """LLM-driven cross-exchange coordinator — parity with attacker Coordinator.

    Same two-phase structure as MultiAgentDetector:
      Phase 1: per-exchange ML classifier (via PerExchangeDetector) flags
               suspicious addresses locally.
      Phase 2 (this class's novelty): instead of cosine-similarity clustering
               of fingerprints, an LLM Coordinator receives per-exchange
               flagged-address fingerprints and reasons about which cross-
               exchange addresses belong to the same actor.

    Chema-validated (2026-07-15): "ML filter → LLM agent" is the right shape.

    Args:
        detector_factory: zero-arg callable → fresh local Detector per view
            (same shape as MultiAgentDetector.detector_factory).
        llm_model: 'haiku' for bulk (~$0.42 for 420 campaigns), 'sonnet'
            for headline runs, 'opus' for qualitative demo.
        llm_max_tokens: response cap. 4096 is safe for coordinator outputs
            (~500-1500 tokens typical).
        fallback_similarity_threshold: if the LLM output is unparseable,
            fall back to cosine-similarity clustering at this threshold
            (matches MultiAgentDetector default).
        llm_client: optional injection for testing (pass a mock that
            responds with a known JSON payload).

    State after fit_per_view:
        _binary: fitted PerExchangeDetector (for predict / predict_proba)
        actor_clusters: {address → cluster_id}, from LLM reasoning
        llm_reasoning: the LLM's textual explanation (useful for chapter 5
                       qualitative analysis)
        usage: dict with prompt/completion tokens + cost estimate
        llm_output_used_fallback: True if the LLM parse failed and we fell
                                   back to cosine clustering
    """

    detector_factory: Callable[[], Detector]
    llm_model: str = "sonnet"
    llm_max_tokens: int = 8192
    fallback_similarity_threshold: float = 0.95
    llm_client: Any = None                            # LLMClient, or mock for tests
    prior: float = 0.5
    # Top-K flagged addresses per exchange sent to the LLM. Rationale:
    # ~650 attacker addresses across all exchanges would produce a ~78k-token
    # prompt AND a >16k-token output (way over max_tokens caps). Realistically,
    # a Coordinator would triage the TOP suspects, not review every flag.
    # 60 per exchange × 3 exchanges = 180 addresses → prompt ~20k tokens,
    # output ~8k tokens. Fits Sonnet 4.6's 200k context / 8192 max_tokens.
    # Bumped from 30 (2026-07-19): 30-per-exchange only surfaced ~14% of the
    # 649 attackers, forcing over-generalisation. 60 covers ~28% — better
    # signal for campaign-level clustering.
    top_k_flagged_per_exchange: int = 60

    # populated by fit_per_view
    _binary: Any = None
    actor_clusters: dict[str, int] = field(default_factory=dict)
    llm_reasoning: str = ""
    usage: dict = field(default_factory=dict)
    llm_output_used_fallback: bool = False

    def fit_per_view(
        self, views, train_labels: dict[str, int],
    ) -> "LLMDefenderCoordinator":
        """Train per-view local detectors, then have the LLM cluster the flags."""
        # Phase 1: per-exchange binary classification (reuses PR #36)
        self._binary = PerExchangeDetector(
            detector_factory=self.detector_factory, prior=self.prior,
        ).fit_per_view(views, train_labels)

        # Phase 2: assemble per-exchange flagged addresses + fingerprints
        per_exchange_flagged: dict[str, list[tuple[str, np.ndarray, float]]] = {}
        for view in views:
            node_order = sorted(view.visible_addresses)
            if not node_order:
                per_exchange_flagged[view.name] = []
                continue
            X = extract_features(view.visible_subgraph, node_order)
            # Get per-address predictions from this view's local model
            local_probas = self._binary.predict_proba(node_order)
            entries: list[tuple[str, np.ndarray, float]] = []
            for i, addr in enumerate(node_order):
                if local_probas[i] >= 0.5:   # locally flagged as suspicious
                    entries.append((addr, X[i], float(local_probas[i])))
            # Keep only the TOP-K by local confidence so the LLM prompt stays
            # tractable. LLM Coordinator receives the highest-suspicion cases
            # from each exchange for cross-institution reasoning.
            if self.top_k_flagged_per_exchange > 0:
                entries.sort(key=lambda x: -x[2])   # descending confidence
                entries = entries[:self.top_k_flagged_per_exchange]
            per_exchange_flagged[view.name] = entries

        total_flagged = sum(len(v) for v in per_exchange_flagged.values())
        if total_flagged == 0:
            # No addresses flagged locally by any exchange → nothing to cluster
            self.actor_clusters = {}
            self.llm_reasoning = "(no addresses flagged by any local detector)"
            self.usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            return self

        # Phase 3: call LLM coordinator
        if self.llm_client is None:
            # Lazy-import to keep this module importable without anthropic SDK.
            from aml.attackers.llm_client import LLMClient
            self.llm_client = LLMClient()

        user_prompt = _build_llm_user_prompt(per_exchange_flagged)
        all_flagged_addrs = {
            a for entries in per_exchange_flagged.values() for a, _, _ in entries
        }

        try:
            result = self.llm_client.complete(
                prompt=user_prompt,
                system=_LLM_COORDINATOR_SYSTEM_PROMPT,
                model=self.llm_model,
                max_tokens=self.llm_max_tokens,
            )
            llm_text = result.text if hasattr(result, "text") else str(result)
            address_to_cluster, reasoning = _parse_llm_clusters(
                llm_text, all_flagged_addrs,
            )
            self.usage = {
                "input_tokens": getattr(result, "input_tokens", 0),
                "output_tokens": getattr(result, "output_tokens", 0),
                "cost_usd": getattr(result, "cost_usd", 0.0),
                "model": self.llm_model,
            }
        except Exception as e:   # noqa: BLE001
            # Network / API failure → fall back to cosine clustering rather
            # than blow up the whole detector. Log the error for debugging
            # but keep the pipeline running.
            self.llm_reasoning = f"(LLM call failed: {e}; used cosine fallback)"
            self.llm_output_used_fallback = True
            address_to_cluster = {}
            reasoning = ""

        # Fallback: if LLM didn't return usable clustering, use cosine
        if not address_to_cluster:
            self.llm_output_used_fallback = True
            fallback_features = {
                a: fp
                for entries in per_exchange_flagged.values()
                for a, fp, _ in entries
            }
            address_to_cluster = cluster_by_similarity(
                fallback_features, threshold=self.fallback_similarity_threshold,
            )
            if not reasoning:
                reasoning = (
                    "(LLM output unparseable — fell back to cosine-similarity "
                    "clustering with threshold "
                    f"{self.fallback_similarity_threshold})"
                )

        self.llm_reasoning = reasoning

        # Every FLAGGED address must have a cluster. Anything the LLM
        # didn't assign gets its own singleton (safety net for LLM
        # incompleteness).
        next_id = (
            max(address_to_cluster.values()) + 1
            if address_to_cluster else 0
        )
        for a in sorted(all_flagged_addrs):
            if a not in address_to_cluster:
                address_to_cluster[a] = next_id
                next_id += 1

        # Un-flagged addresses (not returned by any local classifier)
        # also get singleton IDs — same convention as MultiAgentDetector.
        all_addrs = set()
        for view in views:
            all_addrs.update(view.visible_addresses)
        for a in sorted(all_addrs - all_flagged_addrs):
            address_to_cluster[a] = next_id
            next_id += 1

        self.actor_clusters = address_to_cluster
        return self

    def fit(self, graph, train_labels):
        raise RuntimeError(
            "Use fit_per_view(views, train_labels) — LLMDefenderCoordinator "
            "needs the list of ExchangeView objects (mirrors MultiAgentDetector)."
        )

    def predict(self, nodes: list[str]) -> list[int]:
        if self._binary is None:
            raise RuntimeError(
                "LLMDefenderCoordinator not fitted — call fit_per_view first."
            )
        return self._binary.predict(nodes)

    def predict_proba(self, nodes: list[str]) -> list[float]:
        if self._binary is None:
            raise RuntimeError(
                "LLMDefenderCoordinator not fitted — call fit_per_view first."
            )
        return self._binary.predict_proba(nodes)

    def predict_actors(self, nodes: list[str]) -> list[int]:
        """Per-address actor cluster id (LLM-inferred; NO_CLUSTER if unseen)."""
        return [self.actor_clusters.get(a, NO_CLUSTER) for a in nodes]
