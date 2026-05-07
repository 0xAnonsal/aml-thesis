# AML Multi-Agent Thesis

Multi-agent LLM systems for cryptocurrency anti-money laundering research:

- **Red team** — an adversarial multi-agent launderer that generates typologically realistic attacks on simulated Ethereum and Tron environments
- **Blue team** — a multi-agent collaborative detector that operates over partial graph views (one agent per simulated exchange)

See [ROADMAP.md](ROADMAP.md) for the full proposal: problem statement, novelty claims, methodology, datasets, evaluation metrics, and 12-week timeline.

## Status

Week 3 — Ethereum simulator: Anvil + mock contracts.

## Setup

```bash
# 1. Create conda env
conda env create -f environment.yml
conda activate aml-thesis

# 2. Install the aml package in editable mode
pip install -e .

# 3. Install Foundry (anvil + forge + cast)
curl -L https://foundry.paradigm.xyz | bash
source ~/.bashrc   # or restart shell
foundryup
forge --version && anvil --version

# 4. Configure secrets
cp .env.example .env
# Edit .env to add: ANTHROPIC_API_KEY, ETHERSCAN_API_KEY, TRONGRID_API_KEY

# 5. Verify GPU (optional — needed for GNN baselines)
python -c "import torch; print('CUDA:', torch.cuda.is_available())"
```

## Run baselines and end-to-end smoke checks

```bash
# GNN baselines on Elliptic (week 2)
python scripts/download_elliptic.py
python scripts/train_baseline.py --model gcn
python scripts/train_baseline.py --model gat --dropout 0.2 --lr 5e-4

# Historical price cache (week 2)
python scripts/download_prices.py
pytest tests/test_price_oracle.py

# Ethereum simulator smoke (week 3)
forge build
python scripts/deploy_eth_mocks.py
pytest tests/test_anvil_usdt.py
```

## Project layout

```
contracts/     Solidity mock contracts (Foundry-compiled)
src/aml/
  chains/      Ethereum and Tron chain abstractions (Anvil manager etc.)
  detectors/   GNN baselines: GCN, GAT, EvolveGCN (the "victim" models)
  attackers/   LLM multi-agent launderer (Coordinator + Placement + Layering + Integration)
  defenders/   LLM multi-agent collaborative detector
  env/         Simulators / shared services (PriceOracle etc.)
  utils/
data/          Datasets (mostly gitignored — see ROADMAP §3.1; data/prices/ is tracked)
experiments/   YAML run configs
notebooks/     Exploratory analysis
results/       Experiment outputs (gitignored except summary jsons)
scripts/       CLI entry points
tests/
foundry.toml   Foundry config
```

## Ethics & scope

All experiments use synthetic data and forked / local test chains. **No real funds on mainnet, ever.** Tornado Cash interactions use locally deployed mock contracts on a forked chain — never the live deployed (OFAC-sanctioned) contracts. The MockUSDT contract is a hand-written research artifact, no relation to real Tether, deployed only on local Anvil. See [ROADMAP §6](ROADMAP.md) for full ethics scope.
