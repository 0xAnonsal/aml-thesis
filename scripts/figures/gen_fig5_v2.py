# -*- coding: utf-8 -*-
"""Figura 5 (v2): F1 por dataset — Louvain vs GCN (cross_eval_baselines_our_datasets.json) y Louvain con negativos duros
(hard_neg_eval_*.json). Sin EthereumHeist (allí no se ejecutó Louvain ni GCN: el 0,928 es un Random Forest)."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
OUT = Path(__file__).parent
datasets = ['sepolia_800\n(naive)', 'sepolia_802\n(P1-42)', 'sepolia_803\n(P1-43+)', 'anvil_830\n(defi)', 'anvil_850\n(ransomware)']
louvain_f1 = [1.000, 1.000, 0.981, 0.987, 0.924]
gcn_f1     = [0.921, 0.958, 0.944, 0.914, 0.947]
hard_neg   = [1.000, 1.000, 0.973, 0.994, 0.973]
x = np.arange(len(datasets)); w = 0.27
fig, ax = plt.subplots(figsize=(9.5, 5.2), dpi=140)
b1 = ax.bar(x - w, louvain_f1, w, label='Louvain (Fase 1)', color='#3498db', edgecolor='black', linewidth=0.8)
b2 = ax.bar(x,     gcn_f1,     w, label='GCN (baseline supervisado)', color='#e67e22', edgecolor='black', linewidth=0.8)
b3 = ax.bar(x + w, hard_neg,   w, label='Louvain + negativos duros (P1-69)', color='#2ecc71', edgecolor='black', linewidth=0.8, hatch='//')
for bars in (b1, b2, b3):
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.003, f'{h:.3f}', ha='center', fontsize=7.5)
ax.set_ylabel('F1 (precisión medida contra los benignos etiquetados)')
ax.set_title('F1 binario por dataset: Louvain, GCN y Louvain con negativos duros', fontsize=11)
ax.set_xticks(x); ax.set_xticklabels(datasets, fontsize=9)
ax.set_ylim(0.85, 1.03)
ax.legend(loc='lower left', framealpha=0.95, fontsize=8.5)
ax.text(0.5, 1.02, 'Ambos detectores marcan además el 97-99,9 % del fondo sin etiqueta de Sepolia (bg_fpr), que no entra en el F1.',
        ha='center', transform=ax.transAxes, fontsize=8.5, style='italic', color='#2c3e50')
ax.text(0.99, 0.02, 'Fuentes: results/cross_eval_baselines_our_datasets.json, results/hard_neg_eval_*.json', ha='right', va='bottom',
        transform=ax.transAxes, fontsize=7.5, color='#7f8c8d')
plt.tight_layout()
fig.savefig(OUT / 'f1_by_dataset_v2.png', dpi=150, bbox_inches='tight', facecolor='white')
print('Wrote', OUT / 'f1_by_dataset_v2.png')
