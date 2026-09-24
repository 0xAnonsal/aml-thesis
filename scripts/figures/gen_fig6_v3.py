# -*- coding: utf-8 -*-
"""Figura 6 (v3): same data as v2 (results/reps/aggregate.json + EthereumHeist single run); title/subtitle no longer overlap."""
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from pathlib import Path
HERE = Path(__file__).parent
A = json.load(open('/home/anon/aml-thesis/results/reps/aggregate.json'))
H = A['posthoc']['haiku']; HE = A['posthoc'].get('ethereum_heist_single', {})
KS = [3, 5, 8, 12, 20]
fig, ax = plt.subplots(figsize=(10, 5.8), dpi=140)
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
ax.set_xticks(KS)
ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:.1f}'.replace('.', ',')))
ax.legend(loc='upper right', framealpha=0.95, ncol=2, fontsize=8.5)
fig.suptitle('Barrido de max_clusters en la fusión post-hoc P1-71', fontsize=11.5, fontweight='bold', y=0.985)
ax.set_title('k=3 es el mejor valor medio en los datasets sintéticos; en EthereumHeist el máximo (k=8) se eligió con la verdad de referencia.',
             fontsize=8.5, style='italic', color='#2c3e50', pad=8)
fig.tight_layout(rect=[0, 0, 1, 0.95])
fig.savefig(HERE / 'fig6_v3.png', dpi=150, bbox_inches='tight', facecolor='white')
print('Wrote', HERE / 'fig6_v3.png')
