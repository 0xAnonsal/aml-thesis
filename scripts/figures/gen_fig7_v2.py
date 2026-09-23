# -*- coding: utf-8 -*-
"""Figura 7 (v2): split 80/20 vs LOCO per detector — F1 on attacker folds + FPR on unseen benign campaigns.
Source: results/loco_simulation_precision.json (dataset.pkl, 420 runs, seed 42)."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
OUT = Path(__file__).parent
detectors = ['Louvain\n(voto por comunidad)', 'GCN', 'MultiAgent\n(coseno)']
split_f1 = [0.215, 0.975, 0.871]
loco_f1  = [0.932, 0.932, 0.929]
loco_fpr = [0.777, 0.000, 0.038]
x = np.arange(len(detectors)); w = 0.26
fig, ax = plt.subplots(figsize=(9, 5), dpi=140)
b1 = ax.bar(x - w, split_f1, w, label='F1 — split 80/20 por corrida', color='#3498db', edgecolor='black', linewidth=0.8)
b2 = ax.bar(x,     loco_f1,  w, label='F1 — LOCO, 20 campañas atacantes', color='#e74c3c', edgecolor='black', linewidth=0.8)
b3 = ax.bar(x + w, loco_fpr, w, label='FPR — LOCO, 40 campañas benignas no vistas', color='#95a5a6', edgecolor='black', linewidth=0.8, hatch='//')
for bars in (b1, b2, b3):
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.02, f'{h:.2f}', ha='center', fontsize=8.5, fontweight='bold')
ax.set_ylabel('F1 / FPR'); ax.set_ylim(0, 1.12)
ax.set_xticks(x); ax.set_xticklabels(detectors, fontsize=9)
ax.set_title('Auditoría de memorización (§5.10): split 80/20 frente a LOCO por campaña', fontsize=11)
ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.12), ncol=3, fontsize=8.5, framealpha=0.95)
ax.text(0.5, 1.02, 'F1 casi igual en campañas atacantes no vistas; la diferencia está en las benignas no vistas: Louvain marca por defecto (prior 0,5) el 78 %.',
        ha='center', transform=ax.transAxes, fontsize=8.5, style='italic', color='#2c3e50')
ax.text(0.99, 0.98, 'Fuente: results/loco_simulation_precision.json (dataset.pkl, 420 corridas, semilla 42)', ha='right', va='top',
        transform=ax.transAxes, fontsize=7.5, color='#7f8c8d')
plt.tight_layout()
fig.savefig(OUT / 'memorization_audit_v2.png', dpi=150, bbox_inches='tight', facecolor='white')
print('Wrote', OUT / 'memorization_audit_v2.png')
