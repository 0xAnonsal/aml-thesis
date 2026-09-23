# -*- coding: utf-8 -*-
"""Figura 6 (v2): P1-71 max_clusters sweep — means over the repeated Haiku runs (results/reps/aggregate.json);
EthereumHeist single run (results/p171_posthoc_ethereum_heist.json)."""
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path
HERE = Path(__file__).parent
A = json.load(open('/home/anon/aml-thesis/results/reps/aggregate.json'))
H = A['posthoc']['haiku']; HE = A['posthoc'].get('ethereum_heist_single', {})
KS = [3, 5, 8, 12, 20]
fig, ax = plt.subplots(figsize=(10, 5.5), dpi=140)
style = {'sepolia_800': ('#3498db', 'o'), 'sepolia_802': ('#2980b9', 's'), 'sepolia_803': ('#1abc9c', '^'),
         'anvil_830': ('#e67e22', 'D'), 'anvil_850': ('#c0392b', 'v')}
for ds, (col, mk) in style.items():
    ys = [H[ds][f'max_{k}']['mean'] for k in KS]; es = [H[ds][f'max_{k}']['std'] for k in KS]
    ax.errorbar(KS, ys, yerr=es, marker=mk, label=ds, color=col, linewidth=1.8, markersize=7, capsize=3)
if HE:
    ax.plot(KS, [HE.get(f'max_{k}') for k in KS], marker='*', label='EthereumHeist (1 ejecución)', color='#9b59b6', linewidth=1.8, markersize=10, linestyle='--')
ax.axhline(y=0, color='black', linestyle='-', alpha=0.3, linewidth=0.8)
ax.set_xlabel('max_clusters (parámetro de la fusión post-hoc P1-71)')
ax.set_ylabel('ARI (atribución por rol; media ± σ sobre 3 ejecuciones)')
ax.set_title('Barrido de max_clusters en la fusión post-hoc P1-71', fontsize=11)
ax.set_xticks(KS)
ax.legend(loc='upper right', framealpha=0.95, ncol=2, fontsize=8.5)
ax.text(0.5, 1.02, 'k=3 es el mejor valor medio en los datasets sintéticos; en EthereumHeist el máximo (k=8) se eligió con la verdad de referencia.',
        ha='center', transform=ax.transAxes, fontsize=8.5, style='italic', color='#2c3e50')
plt.tight_layout()
fig.savefig(HERE / 'ari_sweep_v2.png', dpi=150, bbox_inches='tight', facecolor='white')
print('Wrote', HERE / 'ari_sweep_v2.png')
