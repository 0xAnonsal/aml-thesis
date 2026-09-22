#!/bin/bash
# Repetition 3 of the Haiku stream, launched in parallel (reverse order so it never writes the same file as the rep-2 stream).
set -a; source /home/anon/aml-thesis/.env; set +a
source ~/miniconda3/etc/profile.d/conda.sh; conda activate aml-thesis
cd /home/anon/aml-thesis; S=scripts/reps; export REP=3
echo "===== REP 3 (haiku, parallel) $(date) ====="
python $S/campaign_id_loco_eval.py haiku 2>&1 | tail -4
python $S/multi_campaign_loco_eval.py haiku 2>&1 | tail -4
python $S/p171_heldout_eval.py anvil_901 haiku 2>&1 | tail -4
python $S/p171_heldout_eval.py anvil_900 haiku 2>&1 | tail -4
python $S/feature_ablation_eval.py sepolia_803 haiku 2>&1 | tail -4
for ds in anvil_850 anvil_830 sepolia_803 sepolia_802 sepolia_800; do python $S/p171_posthoc.py $ds haiku 2>&1 | tail -4; done
echo "===== DONE rep3 haiku $(date) ====="
