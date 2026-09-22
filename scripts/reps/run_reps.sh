#!/bin/bash
# Repeat the LLM-defender evaluations (P1-71/73 sweep, feature ablation, held-out, multi-campaign) to report mean ± std.
# Usage: bash scripts/reps/run_reps.sh haiku|sonnet   (writes results/reps/rep{2,3}_*.json; rep1 = existing results/*.json)
stream=$1
set -a; source /home/anon/aml-thesis/.env; set +a
source ~/miniconda3/etc/profile.d/conda.sh; conda activate aml-thesis
cd /home/anon/aml-thesis; S=scripts/reps
for REP in 2 3; do
  export REP
  echo "===== REP $REP ($stream) $(date) ====="
  for ds in sepolia_800 sepolia_802 sepolia_803 anvil_830 anvil_850; do python $S/p171_posthoc.py $ds $stream 2>&1 | tail -4; done
  if [ "$stream" = haiku ]; then
    python $S/feature_ablation_eval.py sepolia_803 haiku 2>&1 | tail -4
    python $S/p171_heldout_eval.py anvil_900 haiku 2>&1 | tail -4
    python $S/p171_heldout_eval.py anvil_901 haiku 2>&1 | tail -4
    python $S/multi_campaign_loco_eval.py haiku 2>&1 | tail -4
    python $S/campaign_id_loco_eval.py haiku 2>&1 | tail -4
  fi
done
echo "===== DONE $stream $(date) ====="
