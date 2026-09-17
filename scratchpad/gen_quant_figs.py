"""Generate 3 quantitative PNG figures for TFM:
  3. F1 by dataset (bar chart, 5 datasets × Louvain vs GCN)
  4. ARI vs max_clusters sweep (line plot, 5 datasets)
  5. Memorization audit (GCN vs Louvain in 80/20 vs LOCO)
"""
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

OUT = Path('/home/anon/aml-thesis/tfm/figures')
OUT.mkdir(exist_ok=True)

plt.rcParams.update({
    'font.family': 'DejaVu Sans',
    'font.size': 10,
    'axes.titlesize': 12,
    'axes.labelsize': 11,
    'axes.grid': True,
    'grid.alpha': 0.3,
    'axes.axisbelow': True,
    'legend.fontsize': 9,
})

# ================= FIGURE 3: F1 by dataset =================
fig, ax = plt.subplots(figsize=(10, 5.5), dpi=140)
datasets = ['sepolia_800\n(naive)', 'sepolia_802\n(P1-42)', 'sepolia_803\n(P1-43+)',
            'anvil_830\n(defi)', 'anvil_850\n(ransomware)', 'EthereumHeist\n(real)']
louvain_f1 = [1.000, 1.000, 0.981, 0.987, 0.924, 0.938]
gcn_f1     = [0.921, 0.958, 0.944, 0.914, 0.947, 0.928]
hard_neg   = [1.000, 1.000, 0.973, 0.994, 0.973, None]

x = np.arange(len(datasets))
width = 0.28

bars1 = ax.bar(x - width, louvain_f1, width, label='Louvain (Phase 1)', color='#3498db', edgecolor='black', linewidth=0.8)
bars2 = ax.bar(x, gcn_f1, width, label='GCN (baseline supervisado)', color='#e67e22', edgecolor='black', linewidth=0.8)
bars3 = ax.bar(x + width, [v if v is not None else 0 for v in hard_neg], width, label='Louvain + hard-negative (§8.9.38)', color='#2ecc71', edgecolor='black', linewidth=0.8, hatch='//')

# Mark N/A for EthereumHeist hard-neg
for i, v in enumerate(hard_neg):
    if v is None:
        ax.text(i + width, 0.05, 'N/A', ha='center', fontsize=8, color='gray')

ax.set_ylabel('F1 score')
ax.set_title('F1 binary por dataset — Louvain vs GCN vs Louvain+hard-negative')
ax.set_xticks(x)
ax.set_xticklabels(datasets, fontsize=9)
ax.set_ylim(0.85, 1.02)
ax.axhline(y=0.928, color='gray', linestyle='--', alpha=0.6, linewidth=1)
ax.text(len(datasets)-0.5, 0.930, 'F1=0.928 (EthereumHeist real, headline conservador)',
        ha='right', fontsize=8, color='gray', style='italic')
ax.legend(loc='lower left', framealpha=0.95)

for bars in [bars1, bars2, bars3]:
    for bar in bars:
        h = bar.get_height()
        if h > 0.85:
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.003,
                    f'{h:.3f}', ha='center', fontsize=7.5)

plt.tight_layout()
fig.savefig(OUT / 'f1_by_dataset.png', dpi=150, bbox_inches='tight', facecolor='white')
print(f'Wrote {OUT / "f1_by_dataset.png"}')
plt.close()


# ================= FIGURE 4: ARI vs max_clusters sweep =================
fig, ax = plt.subplots(figsize=(10, 5.5), dpi=140)
max_c_values = [3, 5, 8, 12, 20, 30]
# From §8.9.G (P1-71) sweep results
sepolia_800 = [0.204, 0.094, -0.027, -0.015, None, None]
sepolia_802 = [0.194, 0.194, -0.039, -0.075, -0.022, None]
sepolia_803 = [0.122, -0.000, -0.003, -0.036, None, None]
anvil_830   = [0.137, 0.082, 0.047, -0.008, None, None]
anvil_850   = [0.137, 0.105, 0.095, -0.007, None, None]
eth_heist   = [0.150, 0.194, 0.406, 0.308, 0.176, None]  # from §8.9.43

def _plot_line(vals, label, color, marker):
    x = [c for c, v in zip(max_c_values, vals) if v is not None]
    y = [v for v in vals if v is not None]
    ax.plot(x, y, marker=marker, label=label, color=color, linewidth=1.8, markersize=7)

_plot_line(sepolia_800, 'sepolia_800', '#3498db', 'o')
_plot_line(sepolia_802, 'sepolia_802', '#2980b9', 's')
_plot_line(sepolia_803, 'sepolia_803', '#1abc9c', '^')
_plot_line(anvil_830, 'anvil_830 (defi)', '#e67e22', 'D')
_plot_line(anvil_850, 'anvil_850 (ransomware)', '#c0392b', 'v')
_plot_line(eth_heist, 'EthereumHeist (real)', '#9b59b6', '*')

ax.axhline(y=0, color='black', linestyle='-', alpha=0.3, linewidth=0.8)
ax.set_xlabel('max_clusters (parámetro P1-71 post-hoc merge)')
ax.set_ylabel('ARI (role-attribution)')
ax.set_title('Barrido P1-71 post-hoc cluster merge sobre los 6 datasets — óptimo max_c=3 para sintéticos, max_c=8 para EthereumHeist')
ax.legend(loc='upper right', framealpha=0.95, ncol=2)

# Highlight optimal points
ax.scatter([3, 3, 3, 3, 3], [0.204, 0.194, 0.122, 0.137, 0.137], s=200, facecolors='none', edgecolors='green', linewidth=2, zorder=5)
ax.scatter([8], [0.406], s=200, facecolors='none', edgecolors='green', linewidth=2, zorder=5)

plt.tight_layout()
fig.savefig(OUT / 'ari_sweep.png', dpi=150, bbox_inches='tight', facecolor='white')
print(f'Wrote {OUT / "ari_sweep.png"}')
plt.close()


# ================= FIGURE 5: Memorization audit =================
fig, ax = plt.subplots(figsize=(9, 5), dpi=140)
detectors = ['GCN\n(simulación propia)', 'GCN\n(EthereumHeist)', 'Louvain\n(simulación)', 'Louvain\n(EthereumHeist)']
split_8020 = [0.97, 0.99, 0.978, 0.938]
loco_cv    = [0.42, 0.68, 0.978, 0.93]  # Louvain robust — barely changes

x = np.arange(len(detectors))
width = 0.35
bars1 = ax.bar(x - width/2, split_8020, width, label='Split estándar 80/20', color='#3498db', edgecolor='black', linewidth=0.8)
bars2 = ax.bar(x + width/2, loco_cv, width, label='LOCO-CV (evidencia principal)', color='#e74c3c', edgecolor='black', linewidth=0.8)

for i, (s, l) in enumerate(zip(split_8020, loco_cv)):
    delta = l - s
    if abs(delta) > 0.05:
        # Show delta annotation
        color = 'red' if delta < 0 else 'green'
        ax.annotate(f'ΔF1={delta:+.2f}', xy=(i + width/2, l), xytext=(i + width/2, l - 0.15),
                    ha='center', fontsize=9, color=color, fontweight='bold',
                    arrowprops=dict(arrowstyle='->', color=color, lw=1.2))

ax.set_ylabel('F1 score')
ax.set_title('Auditoría metodológica de memorización (§8.10) — GCN colapsa bajo LOCO, Louvain se mantiene')
ax.set_xticks(x)
ax.set_xticklabels(detectors, fontsize=9)
ax.set_ylim(0, 1.1)
ax.legend(loc='upper right', framealpha=0.95)

for bars in [bars1, bars2]:
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2, h + 0.02, f'{h:.2f}', ha='center', fontsize=8.5, fontweight='bold')

# Finding label
ax.text(0.5, 1.02, 'Finding metodológico: los detectores GCN memorizan identidad de campaña; Louvain (no supervisado) es cota inferior confiable de generalización.',
        ha='center', transform=ax.transAxes, fontsize=9, style='italic', color='#2c3e50')

plt.tight_layout()
fig.savefig(OUT / 'memorization_audit.png', dpi=150, bbox_inches='tight', facecolor='white')
print(f'Wrote {OUT / "memorization_audit.png"}')
plt.close()

print('All 3 quant figures generated.')
