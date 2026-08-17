"""Debug LLM defender JSON parse issue — capture raw response, diagnose."""
from __future__ import annotations

import pickle
import re
import time
from pathlib import Path

from aml.attackers.llm_client import LLMClient
from aml.detectors.baselines import PerExchangeDetector, derive_binary_labels
from aml.detectors.dataset import partial_visibility_split
from aml.detectors.gnn import GCNDetector, extract_features
from aml.detectors.multi_agent import (
    _LLM_COORDINATOR_SYSTEM_PROMPT,
    _build_llm_user_prompt,
    _parse_llm_clusters,
)
from aml.utils.env import load_dotenv_if_present


PKL = Path.home() / "aml-results" / "batch_2026-06-26" / "dataset.pkl"
OUT_TXT = Path.home() / "aml-thesis" / "results" / "llm_debug_raw_response.txt"

load_dotenv_if_present()

print("Loading pickle...")
with PKL.open("rb") as f:
    d = pickle.load(f)
combined = d["combined"]
bin_labels = derive_binary_labels(combined.node_labels)
train_labels = {a: bin_labels[a] for a in bin_labels}

print("Splitting views...")
views = partial_visibility_split(combined, num_exchanges=3, seed=42)

print("Training per-view GCN classifiers...")
per_ex = PerExchangeDetector(
    detector_factory=lambda: GCNDetector(seed=42, epochs=50),
).fit_per_view(views, train_labels)

print("Extracting top-30 flagged per exchange...")
per_exchange_flagged = {}
for view in views:
    node_order = sorted(view.visible_addresses)
    if not node_order:
        per_exchange_flagged[view.name] = []
        continue
    X = extract_features(view.visible_subgraph, node_order)
    local_probas = per_ex.predict_proba(node_order)
    entries = [
        (addr, X[i], float(local_probas[i]))
        for i, addr in enumerate(node_order)
        if local_probas[i] >= 0.5
    ]
    entries.sort(key=lambda x: -x[2])
    entries = entries[:30]
    per_exchange_flagged[view.name] = entries
    print(f"  {view.name}: {len(entries)} top-flagged")

user_prompt = _build_llm_user_prompt(per_exchange_flagged)
print(f"\nUser prompt length: {len(user_prompt)} chars")

all_flagged = {a for entries in per_exchange_flagged.values() for a, _, _ in entries}
print(f"Total flagged: {len(all_flagged)}")

print("\nCalling Haiku...")
client = LLMClient()
t0 = time.time()
result = client.complete(
    prompt=user_prompt,
    system=_LLM_COORDINATOR_SYSTEM_PROMPT,
    model="haiku",
    max_tokens=8192,
)
elapsed = time.time() - t0
print(f"Response received in {elapsed:.1f}s")
print(f"Input tokens: {result.input_tokens}, Output tokens: {result.output_tokens}")
print(f"Cost: ${result.cost_usd:.4f}")

# Save raw response
OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
OUT_TXT.write_text(result.text)
print(f"\nRaw response saved to {OUT_TXT}")
print(f"Response length: {len(result.text)} chars")

# Try current parser
print("\n=== Trying current parser ===")
clusters, reasoning = _parse_llm_clusters(result.text, all_flagged)
print(f"Clusters parsed: {len(clusters)} addresses assigned")
print(f"Reasoning length: {len(reasoning)} chars")

# Diagnose: show first/last 300 chars of response
print("\n=== Response first 500 chars ===")
print(result.text[:500])
print("\n=== Response last 500 chars ===")
print(result.text[-500:])

# Try to find where JSON block is
print("\n=== JSON boundary detection ===")
first_brace = result.text.find("{")
last_brace = result.text.rfind("}")
print(f"First {{ at char {first_brace}, last }} at char {last_brace}")

if first_brace >= 0 and last_brace > first_brace:
    candidate = result.text[first_brace:last_brace + 1]
    print(f"Candidate JSON length: {len(candidate)} chars")

    # Try json.loads directly
    import json
    try:
        parsed = json.loads(candidate)
        print("✓ Direct parse SUCCESS")
        print(f"  Keys: {list(parsed.keys())}")
        if "actor_clusters" in parsed:
            print(f"  Clusters: {len(parsed['actor_clusters'])}")
    except json.JSONDecodeError as e:
        print(f"✗ Direct parse FAILED: {e}")
        print(f"  Error near char {e.pos}, line {e.lineno}, col {e.colno}")
        # Show context around error
        start = max(0, e.pos - 100)
        end = min(len(candidate), e.pos + 100)
        print(f"  Context: ...{candidate[start:end]}...")

# Also try markdown fence
print("\n=== Markdown fence detection ===")
fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", result.text, re.DOTALL)
if fence_match:
    print(f"✓ Found markdown fence, {len(fence_match.group(1))} chars")
else:
    print("✗ No markdown fence")
