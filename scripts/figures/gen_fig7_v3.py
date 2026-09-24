# -*- coding: utf-8 -*-
"""Figura 7 (v3): same data as v2 (results/loco_simulation_precision.json); title/subtitle separated, source line moved
below the legend, legend labels without em-dashes."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter
from pathlib import Path
OUT = Path(__file__).parent
detectors = ['Louvain\n(voto por comunidad)', 'GCN', 'MultiAgent\n(coseno)']
split_f1 = [0.215, 0.975, 0.871]
loco_f1  = [0.932, 0.932, 0.929]
loco_fpr = [0.777, 0.000, 0.038]
x = np.arange(len(detectors)); w = 0.26
fig, ax = plt.subplots(figsize=(9, 5.6), dpi=140)
b1 = ax.bar(x - w, split_f1, w, label='F1, split 80/20 por corrida', color='#3498db', edgecolor='black', linewidth=0.8)
b2 = ax.bar(x,     loco_f1,  w, label='F1, LOCO (20 campañas atacantes)', color='#e74c3c', edgecolor='black', linewidth=0.8)
b3 = ax.bar(x + w, loco_fpr, w, label='FPR, LOCO (40 campañas benignas no vistas)', color='#95a5a6', edgecolor='black', linewidth=0.8, hatch='//')
for bars in (b1, b2, b3):
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.02, f'{h:.2f}'.replace('.', ','), ha='center', fontsize=8.5, fontweight='bold')
ax.set_ylabel('F1 / FPR'); ax.set_ylim(0, 1.12)
ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:.1f}'.replace('.', ',')))
ax.set_xticks(x); ax.set_xticklabels(detectors, fontsize=9)
fig.suptitle('Auditoría de memorización (§5.10): split 80/20 frente a LOCO por campaña', fontsize=11.5, fontweight='bold', y=0.985)
ax.set_title('F1 casi igual en campañas atacantes no vistas; la diferencia está en las benignas no vistas: Louvain marca por defecto (prior 0,5) el 78 %.',
             fontsize=8.5, style='italic', color='#2c3e50', pad=8)
ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.13), ncol=3, fontsize=8.5, framealpha=0.95)
fig.text(0.5, 0.012, 'Fuente: results/loco_simulation_precision.json (dataset.pkl, 420 corridas, semilla 42)',
         ha='center', va='bottom', fontsize=7.5, color='#7f8c8d')
fig.tight_layout(rect=[0, 0.07, 1, 0.95])
fig.savefig(OUT / 'fig7_v3.png', dpi=150, bbox_inches='tight', facecolor='white')
print('Wrote', OUT / 'fig7_v3.png')
