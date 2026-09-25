# -*- coding: utf-8 -*-
"""Figura 3 (v2): Gantt built from the final Tabla 6 (duración estimada vs real). 22 semanas, abril-septiembre 2026.
Filled bar = duración real; black bracket above = duración estimada (same start). Overlaps: F3/F4 (ETH accumulation)
and F4/F5, as §4.6 says (solapamiento parcial en las fases 2-4 y 4-5)."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from pathlib import Path
OUT = Path(__file__).parent
# (label, start week, real weeks, estimated weeks, colour)
phases = [
    ('F1 Análisis y diseño',                 1, 3, 3, '#4c72b0'),
    ('F2 Simulador ofensivo',                4, 6, 5, '#55a868'),
    ('F3 Iteraciones Anvil-dev → Sepolia',   9, 8, 3, '#c44e52'),
    ('F4 Despliegue Sepolia',               17, 5, 3, '#8172b2'),
    ('F5 Defensor + ablaciones',            18, 4, 4, '#ccb974'),
    ('F6 Redacción memoria + defensa',      19, 4, 3, '#64b5cd'),
]
fig, ax = plt.subplots(figsize=(12, 4.6), dpi=150)
for i, (lab, s, real, est, col) in enumerate(phases):
    y = len(phases) - i
    ax.barh(y, real, left=s - 0.5, height=0.55, color=col, edgecolor='black', linewidth=0.6)
    ax.plot([s - 0.5, s - 0.5 + est], [y + 0.36, y + 0.36], color='black', linewidth=2.2, solid_capstyle='butt')
    ax.plot([s - 0.5, s - 0.5], [y + 0.30, y + 0.42], color='black', linewidth=1.2)
    ax.plot([s - 0.5 + est, s - 0.5 + est], [y + 0.30, y + 0.42], color='black', linewidth=1.2)
    txt = f'S{s}-S{s + real - 1}: {real} sem.' + ('' if real == est else f' (est. {est})')
    if s + real - 1 >= 20:   # last phase: label to the left of the bar so it stays inside the axes
        ax.text(s - 0.7, y, txt, va='center', ha='right', fontsize=8.6, color='#222222')
    else:
        ax.text(s - 0.5 + real + 0.2, y, txt, va='center', ha='left', fontsize=8.6, color='#222222')
ax.set_yticks(range(1, len(phases) + 1)); ax.set_yticklabels([p[0] for p in phases][::-1], fontsize=9.5)
ax.set_xlim(0.5, 23.2); ax.set_xticks(range(1, 23)); ax.set_xticklabels([str(w) for w in range(1, 23)], fontsize=8.5)
ax.set_xlabel('Semana (S1 = semana del 27 de abril de 2026; S22 termina el 27 de septiembre)', fontsize=10)
ax.grid(axis='x', alpha=0.3); ax.set_axisbelow(True)
for sp in ('top', 'right'): ax.spines[sp].set_visible(False)
ax.legend(handles=[Patch(facecolor='#999999', edgecolor='black', label='Duración real'),
                   Line2D([0], [0], color='black', linewidth=2.2, label='Duración estimada (Tabla 6)')],
          loc='lower left', fontsize=8.5, framealpha=0.95)
ax.set_title('Total: 21 semanas estimadas, 22 de calendario (30 sumando fases; F4-F6 en paralelo desde finales de agosto)', loc='right', fontsize=8.5, color='#555555', style='italic')
fig.tight_layout()
fig.savefig(OUT / 'fig3_gantt_v2.png', dpi=150, bbox_inches='tight', facecolor='white')
print('Wrote', OUT / 'fig3_gantt_v2.png')
