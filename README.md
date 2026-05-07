# AML Multi-Agent Thesis

Multi-agent LLM systems for cryptocurrency anti-money laundering research:

- **Red team** — an adversarial multi-agent launderer that generates typologically realistic attacks on a simulated Ethereum environment, including stablecoin (USDT) flows and real ZK-mixer use
- **Blue team** — a multi-agent collaborative detector that performs actor-level clustering (related-wallet identification) under partial graph visibility — one agent per simulated exchange

See [ROADMAP.md](ROADMAP.md) for the full proposal: problem statement, novelty claims, methodology, datasets, evaluation metrics, and 12-week timeline.

## Status

Week 4 — ZK Tornado upgrade in progress.

## Setup

```bash
# 1. Create conda env
conda env create -f environment.yml
conda activate aml-thesis

# 2. Install the aml package in editable mode
pip install -e .

# 3. Install Foundry (anvil + forge + cast)
curl -L https://foundry.paradigm.xyz | bash
source ~/.bashrc
foundryup
forge --version && anvil --version

# 4. Install ZK proving stack (Node + snarkjs + circom)
bash scripts/install_zk_tools.sh
source ~/.bashrc                            # picks up nvm + circom on PATH

# 5. Run ZK trusted setup for the test circuit (~30s after the .ptau download)
bash scripts/setup_zk.sh

# 6. Configure secrets
cp .env.example .env
# Edit .env to add: ANTHROPIC_API_KEY, ETHERSCAN_API_KEY

# 7. Verify GPU (optional — needed for GNN baselines)
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
pytest tests/test_anvil_usdt.py tests/test_anvil_uniswap.py tests/test_anvil_tornado.py tests/test_anvil_bridge.py

# ZK toolchain smoke (week 4.1)
bash scripts/setup_zk.sh
pytest tests/test_zk_toolchain.py
```

## Project layout

```
contracts/     Solidity mock contracts (Foundry-compiled)
circuits/      circom circuits + snarkjs build artifacts (build/ gitignored)
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
