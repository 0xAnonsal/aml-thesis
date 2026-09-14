#!/bin/bash
set -a
source /home/anon/aml-thesis/.env
set +a
export PATH="/home/anon/.foundry/bin:$PATH"
cd /home/anon/aml-thesis
mkdir -p /tmp/heldout results/benign_corpus_v58 results/anvil_option_a
which anvil || { echo "anvil still not found"; exit 1; }

# Attackers: seeds 900 + 901
/home/anon/miniconda3/envs/aml-thesis/bin/python3 -m aml.attackers.run_campaign \
    --scenario defi-exploit --seed 900 --out results/anvil_option_a/ \
    > /tmp/heldout/attacker_900.log 2>&1 &
PID_A=$!

/home/anon/miniconda3/envs/aml-thesis/bin/python3 -m aml.attackers.run_campaign \
    --scenario ransomware-cashout --seed 901 --out results/anvil_option_a/ \
    > /tmp/heldout/attacker_901.log 2>&1 &
PID_B=$!

# Benign corpus v58: 5 seeds
for s in 400 401 402 403 404; do
    /home/anon/miniconda3/envs/aml-thesis/bin/python3 -m aml.detectors.run_benign \
        --seed $s --num-users 20 --num-txs 300 --out results/benign_corpus_v58/ \
        > /tmp/heldout/benign_$s.log 2>&1 &
done

echo "PIDs launched: attackers $PID_A $PID_B + 5 benigns"
wait
echo '=== ALL DONE ==='
ls -la /tmp/heldout/*.log
tail -5 /tmp/heldout/attacker_900.log
tail -5 /tmp/heldout/attacker_901.log
