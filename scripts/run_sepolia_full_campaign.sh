#!/usr/bin/env bash
# Full Sepolia campaign — 3 scenarios sequentially.
#
# Task #7 of the TFM plan: run defi-exploit + stablecoin-scam +
# ransomware-cashout back to back on the deployed Sepolia contracts.
#
# Wall-clock estimate: 6-8h total (2-3h per scenario).
# LLM cost estimate: $1.50-3 (Sonnet) or $0.30-0.60 (Haiku).
# Sepolia ETH cost estimate:
#   defi-exploit (3 ETH):        ~3.5 ETH consumed
#   stablecoin-scam (8000 USDT): ~0.5 ETH gas only (USDT is minted)
#   ransomware-cashout (5 ETH):  ~5.5 ETH consumed
#   Total: ~9.5 ETH  (deployer starts with 11.3, ends with ~1.8)
#
# Runs the 3 scenarios in the sensible order: smaller first so if one
# blows up we haven't wasted the budget yet.
#
# Usage:
#   bash scripts/run_sepolia_full_campaign.sh [MODEL]
#
# MODEL defaults to "haiku". Use "sonnet" for headline quality (~5x cost).

set -euo pipefail

MODEL="${1:-haiku}"
OUT_DIR="results/sepolia_campaign"

echo "=========================================================="
echo "SEPOLIA FULL CAMPAIGN — 3 scenarios, model=$MODEL"
echo "=========================================================="

# Ensure conda env is active (source it inside subshells too)
source ~/miniconda3/etc/profile.d/conda.sh
conda activate aml-thesis

# Scenario 1: defi-exploit — smallest, safest, run first as smoke test
echo ""
echo "[1/3] defi-exploit (3 ETH)..."
python scripts/run_sepolia_campaign.py \
    --scenario defi-exploit \
    --amount 3.0 \
    --model "$MODEL" \
    --seed 42 \
    --out "$OUT_DIR"

# Scenario 2: stablecoin-scam — USDT only, minimal ETH cost
echo ""
echo "[2/3] stablecoin-scam (8000 USDT)..."
python scripts/run_sepolia_campaign.py \
    --scenario stablecoin-scam \
    --amount 8000.0 \
    --model "$MODEL" \
    --seed 43 \
    --alice-funding-eth 0.3 \
    --out "$OUT_DIR"

# Scenario 3: ransomware-cashout — largest, run last
echo ""
echo "[3/3] ransomware-cashout (5 ETH)..."
python scripts/run_sepolia_campaign.py \
    --scenario ransomware-cashout \
    --amount 5.0 \
    --model "$MODEL" \
    --seed 44 \
    --out "$OUT_DIR"

echo ""
echo "=========================================================="
echo "ALL 3 SCENARIOS COMPLETED — outputs in $OUT_DIR"
echo "=========================================================="
ls -la "$OUT_DIR"
