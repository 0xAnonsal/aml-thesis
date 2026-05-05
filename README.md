# AML Multi-Agent Thesis

Multi-agent LLM systems for cryptocurrency anti-money laundering research:

- **Red team** — an adversarial multi-agent launderer that generates typologically realistic attacks on simulated Ethereum and Tron environments
- **Blue team** — a multi-agent collaborative detector that operates over partial graph views (one agent per simulated exchange)

See [ROADMAP.md](ROADMAP.md) for the full proposal: problem statement, novelty claims, methodology, datasets, evaluation metrics, and 12-week timeline.

## Status

Week 1 — environment setup.

## Setup

```bash
# 1. Create conda env
conda env create -f environment.yml
conda activate aml-thesis

# 2. Configure secrets
cp .env.example .env
# Edit .env to add: ANTHROPIC_API_KEY, ETHERSCAN_API_KEY, TRONGRID_API_KEY

# 3. Verify GPU
python -c "import torch; print('CUDA:', torch.cuda.is_available())"
```

## Project layout

```
src/aml/
  chains/      Ethereum and Tron chain abstractions
  detectors/   GNN baselines: GCN, GAT, EvolveGCN (the "victim" models)
  attackers/   LLM multi-agent launderer (Coordinator + Placement + Layering + Integration)
  defenders/   LLM multi-agent collaborative detector
  env/         Simulators / environments
  utils/
data/          Datasets (gitignored — see ROADMAP §3.1)
experiments/   YAML run configs
notebooks/     Exploratory analysis
results/       Experiment outputs (gitignored except summaries)
scripts/       CLI entry points
tests/
```

## Ethics & scope

All experiments use synthetic data and forked / local test chains. **No real funds on mainnet, ever.** Tornado Cash interactions use locally deployed mock contracts on a forked chain — never the live deployed (OFAC-sanctioned) contracts. See [ROADMAP §6](ROADMAP.md) for full ethics scope.
