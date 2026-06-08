# AML Multi-Agent Thesis

Adversarial multi-agent LLM systems for cryptocurrency anti-money-laundering research:

- **Red team** — an LLM multi-agent launderer (Coordinator + Placement + Layering + Integration sub-agents, FATF-aligned) that drives a real EVM simulator. Uses real ZK Tornado mixer cycles, Uniswap-style swaps, USDT structuring, and sub-$999 fan-out to multiple "clean exit" wallets.
- **Blue team** — a multi-agent collaborative detector that performs actor-level clustering (related-wallet identification) under partial graph visibility — one agent per simulated exchange. Compared against community-detection (Louvain) and the "Weber-style" GCN published baseline.

See [ROADMAP.md](ROADMAP.md) for the full proposal: problem statement, novelty claims, methodology, datasets, evaluation metrics, and 12-week timeline.

## Status

**Week 7.5(c) — Detection-side ensemble done.** Full attacker + detector pipeline working end-to-end:

| Layer | Status |
|---|---|
| Anvil simulator + 4 mock contracts (USDT, Uniswap pool, Tornado mixer, bridge) | ✅ |
| Real ZK Tornado mixer (Groth16, MiMC, Merkle) — real proofs on the EVM | ✅ |
| Multi-agent FATF attacker — Coordinator + 3 sub-agents, 13 chain tools, gas-aware burners | ✅ |
| Validated end-to-end: 3 ETH laundering campaign through the ZK mixer | ✅ |
| Campaign runner + benign baseline generator (labelled trace artifacts) | ✅ |
| Run loader + transaction-graph extractor + thesis-ready figure renderer | ✅ |
| Dataset combiner + partial-visibility split | ✅ |
| Detector trio: Louvain + GCN (Weber-style) + multi-agent (thesis novelty) | ✅ |
| ~265 automated tests, all green | ✅ |

## Setup

```bash
# 1. Create conda env
conda env create -f environment.yml
conda activate aml-thesis

# 2. Install the aml package in editable mode (pulls core deps)
pip install -e ".[all]"   # or `pip install -e .` for the minimal install

# 3. Install Foundry (anvil + forge + cast)
curl -L https://foundry.paradigm.xyz | bash
source ~/.bashrc
foundryup
forge --version && anvil --version

# 4. Install ZK proving stack (Node + snarkjs + circom)
bash scripts/install_zk_tools.sh
source ~/.bashrc

# 5. Run ZK trusted setup for the withdraw circuit (~30s after .ptau download)
bash scripts/setup_zk.sh withdraw

# 6. Configure secrets — at minimum ANTHROPIC_API_KEY for the attacker CLI
cp .env.example .env
# Edit .env to add: ANTHROPIC_API_KEY=sk-ant-...
```

## Run the attacker (Week 6)

A full FATF-style ETH laundering campaign, autonomously driven by LLM agents:

```bash
# One campaign, ~3 min, ~$0.30 in Anthropic Haiku credits
python -m aml.attackers.run_campaign --scenario defi-exploit --seed 42 --out runs/
```

Output: a timestamped directory with the campaign transcript, the full chain trace (decoded ERC-20 / mixer / swap events), labelled addresses (attacker vs benign + funded vs unused exits), and a human-readable summary. See `--help` for all options.

## Run the benign baseline (Week 7.2)

Synthetic normal-activity trace generator — the negative class for detector training:

```bash
python -m aml.detectors.run_benign --num-users 20 --num-txs 100 --seed 42 --out runs/
```

Same output schema as the attacker. No mixer, no smurfing, no consolidation patterns — explicitly NOT the laundering behaviour the detector is supposed to flag.

## Extract graphs + render figures (Week 7.3)

```python
from pathlib import Path
from aml.detectors.graph import load_run, to_networkx, summarize_graph
from aml.detectors.viz import draw_run_graph

for run_dir in sorted(Path("runs").iterdir()):
    run = load_run(run_dir)
    g = to_networkx(run)
    print(run_dir.name, summarize_graph(g))
    draw_run_graph(run, f"figs/{run_dir.name}.png")
```

The attacker figure shows a red source wallet, orange burner ring, **bright red dashed mixer cycles** (the headline ZK contribution), and a green ring of clean-exit wallets. The benign figure has none of those features — just a dense pairwise mesh around the pool. See `figs/` after running.

## Train + evaluate the detectors (Week 7.4 + 7.5)

```python
from pathlib import Path
from aml.detectors.dataset import combine_runs, partial_visibility_split, train_val_test_split
from aml.detectors.baselines import LouvainDetector, derive_binary_labels
from aml.detectors.gnn import GCNDetector
from aml.detectors.multi_agent import MultiAgentDetector, true_actor_clusters, actor_clustering_metrics
from aml.detectors.eval import evaluate, pretty_print

# Combine all runs you've generated
ds = combine_runs(sorted(Path("runs").iterdir()))
views = partial_visibility_split(ds, num_exchanges=3, seed=0)
train_runs, val_runs, test_runs = train_val_test_split(ds.all_run_names, seed=0)

# Build training labels from the canonical address labels
all_labels = derive_binary_labels(ds.node_labels)
train_labels = {a: l for a, l in all_labels.items()
                if any(r in train_runs for r in ds.graph.nodes[a]["runs"])}

# Train + compare detectors
louvain = LouvainDetector(seed=0).fit(ds.graph, train_labels)
gcn     = GCNDetector(epochs=100, seed=0).fit(ds.graph, train_labels)
multi   = MultiAgentDetector(
    detector_factory=lambda: GCNDetector(epochs=100, seed=0),
).fit_per_view(views, train_labels)

# Binary detection metrics
test_nodes  = [a for a in all_labels if any(r in test_runs for r in ds.graph.nodes[a]["runs"])]
test_labels = [all_labels[a] for a in test_nodes]
for name, det in [("Louvain", louvain), ("GCN", gcn), ("MultiAgent", multi)]:
    print(name, pretty_print(evaluate(test_labels, det.predict(test_nodes), det.predict_proba(test_nodes))))

# Actor-clustering metric (the thesis novelty)
true_actors = true_actor_clusters(ds.runs)
print("MultiAgent actor clustering:", actor_clustering_metrics(true_actors, multi.actor_clusters))
```

## Run the test suite

```bash
# Structural tests only (no API, no chain, no torch) — ~10s
pytest tests/test_dataset.py tests/test_detectors_baselines.py tests/test_detectors_multi_agent.py -q

# Add chain-backed tests (needs Foundry installed)         — ~30s
pytest tests/test_tools.py tests/test_graph.py -q

# Add the headline live test (needs ANTHROPIC_API_KEY)     — ~3 min, ~$0.30
pytest tests/test_coordinator.py::test_coordinator_runs_full_eth_laundering_campaign -v

# Full sweep                                                — ~5 min
pytest tests/ -q
```

## Project layout

```
contracts/         Solidity mock contracts (Foundry-compiled)
circuits/          circom circuits + snarkjs build artifacts (build/ gitignored)
src/aml/
  chains/          Anvil manager + ETH deploy helpers + chain-trace extraction
  attackers/       LLM multi-agent launderer:
                     coordinator.py    — FATF orchestrator (delegate-only)
                     sub_agent.py      — tool-scoped FATF specialist
                     prompts.py        — Placement / Layering / Integration prompts
                     tools.py          — 13 on-chain tools (USDT, ETH, swaps, mixer)
                     scenarios.py      — campaign typologies (defi-exploit etc.)
                     run_campaign.py   — CLI to drive one campaign + dump artifacts
  detectors/       Both Elliptic baselines (Week 1-2) AND the simulated-data
                   evaluation pipeline (Week 7):
                     gcn.py, gat.py    — raw torch.nn.Module Elliptic baselines
                     graph.py          — chain_trace.jsonl → labelled MultiDiGraph
                     dataset.py        — combine N runs + partial-visibility split
                     viz.py            — thesis-figure renderer (matplotlib)
                     baselines.py      — Detector ABC + LouvainDetector
                     gnn.py            — GCNDetector (Detector ABC wrapper)
                     multi_agent.py    — MultiAgentDetector (thesis novelty)
                     eval.py           — binary metrics + ARI for clustering
                     run_benign.py     — CLI to generate benign baseline traces
  env/             PriceOracle and similar shared services
  utils/           Small stdlib-only helpers (env loader etc.)
data/              Datasets (mostly gitignored — see ROADMAP §3.1)
results/           Experiment outputs (gitignored except summary jsons)
scripts/           CLI entry points + ZK toolchain setup
tests/             ~265 automated tests
foundry.toml       Foundry config
```

## Ethics & scope

All experiments use synthetic data and forked / local test chains. **No real funds on mainnet, ever.** Tornado Cash interactions use locally deployed mock contracts on a local Anvil node — never the live deployed (OFAC-sanctioned) contracts. The MockUSDT contract is a hand-written research artifact, no relation to real Tether, deployed only on local Anvil. See [ROADMAP §6](ROADMAP.md) for the full ethics scope.
