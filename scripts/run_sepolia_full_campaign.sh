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

set -uo pipefail   # NO -e — we WANT to keep going if a scenario refuses
                   # (Sonnet observed to refuse mid-run 2026-08-11, this
                   # is a valid data point for §5.6.4 not a failure)

MODEL="${1:-sonnet}"
OUT_DIR="results/sepolia_campaign"

echo "=========================================================="
echo "SEPOLIA FULL CAMPAIGN — 3 scenarios, model=$MODEL"
echo "=========================================================="

# Ensure conda env is active (source it inside subshells too)
source ~/miniconda3/etc/profile.d/conda.sh
conda activate aml-thesis

INTER_SCENARIO_DELAY=60   # seconds — let mempool clear + avoid RPC rate-limit

# Helper: find the run-dir for a scenario+seed pair. The runner writes to
# {OUT_DIR}/{timestamp}_{scenario}_seed{N}_sepolia/ — we glob for the most
# recent match. Sweep back to deployer after each scenario to recycle ETH+USDT
# so subsequent scenarios don't hit a low deployer balance.
sweep_last_run() {
    local pattern="$1"    # e.g. "*defi-exploit_seed42_sepolia"
    local run_dir
    run_dir=$(ls -td "$OUT_DIR"/${pattern} 2>/dev/null | head -1)
    if [ -z "$run_dir" ]; then
        echo "[sweep] no run-dir found matching $pattern — skipping"
        return
    fi
    if [ ! -f "$run_dir/wallets_keys.json" ]; then
        echo "[sweep] $run_dir has no wallets_keys.json — skipping"
        return
    fi
    echo "[sweep] running sweep_sepolia.py on $run_dir"
    python scripts/sweep_sepolia.py --run-dir "$run_dir" \
        || echo "[!] Sweep of $run_dir failed (funds may still be reclaimable manually)"
    # Delete the private-keys file after sweep — no need to keep secrets around
    rm -f "$run_dir/wallets_keys.json"
}

# Scenario 1: defi-exploit (mixer fixed post-2026-08-11)
echo ""
echo "[1/3] defi-exploit (3 ETH)..."
python scripts/run_sepolia_campaign.py \
    --scenario defi-exploit \
    --amount 3.0 \
    --model "$MODEL" \
    --seed 42 \
    --out "$OUT_DIR" \
    || echo "[!] Scenario 1 exited with error (may be refusal — see log)"

echo ""
echo "[wait] sleeping ${INTER_SCENARIO_DELAY}s before sweep..."
sleep "$INTER_SCENARIO_DELAY"
sweep_last_run "*defi-exploit_seed42_sepolia"

# Scenario 2: stablecoin-scam — USDT only, no mixer
echo ""
echo "[2/3] stablecoin-scam (8000 USDT)..."
python scripts/run_sepolia_campaign.py \
    --scenario stablecoin-scam \
    --amount 8000.0 \
    --model "$MODEL" \
    --seed 43 \
    --alice-funding-eth 0.5 \
    --out "$OUT_DIR" \
    || echo "[!] Scenario 2 exited with error (may be refusal — see log)"

echo ""
echo "[wait] sleeping ${INTER_SCENARIO_DELAY}s before sweep..."
sleep "$INTER_SCENARIO_DELAY"
sweep_last_run "*stablecoin-scam_seed43_sepolia"

# Scenario 3: ransomware-cashout — heavy mixer use (tests the mixer fix)
echo ""
echo "[3/3] ransomware-cashout (5 ETH)..."
python scripts/run_sepolia_campaign.py \
    --scenario ransomware-cashout \
    --amount 5.0 \
    --model "$MODEL" \
    --seed 44 \
    --out "$OUT_DIR" \
    || echo "[!] Scenario 3 exited with error (may be refusal — see log)"

echo ""
echo "[wait] sleeping ${INTER_SCENARIO_DELAY}s before final sweep..."
sleep "$INTER_SCENARIO_DELAY"
sweep_last_run "*ransomware-cashout_seed44_sepolia"

echo ""
echo "=========================================================="
echo "ALL 3 SCENARIOS COMPLETED — outputs in $OUT_DIR"
echo "=========================================================="
ls -la "$OUT_DIR"
