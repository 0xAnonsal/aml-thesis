# -*- coding: utf-8 -*-
"""Render a readable subgraph of the seed 803 campaign: only the labelled campaign nodes (attacker, exits, contracts)."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx
from src.aml.detectors import graph as G

D = 'results/sepolia_campaign/2026-09-08T18-17-57_defi-exploit_seed803_sepolia'
run = G.load_run(D); g = G.to_networkx(run)

keep = [n for n, d in g.nodes(data=True) if d.get('kind', 'unknown') != 'unknown']
sub = g.subgraph(keep).copy()
# drop isolated nodes with no edge inside the subgraph, to reduce clutter
sub.remove_nodes_from([n for n in list(sub.nodes()) if sub.degree(n) == 0 and not g.nodes[n].get('label', '').startswith(('attacker', 'clean_exit_funded'))])
print('subgraph nodes', sub.number_of_nodes(), 'edges', sub.number_of_edges())

COL = {
 'attacker_source': ('#d62728', 420, 'Origen (Alice)'),
 'attacker_burner': ('#ff7f0e', 90, 'Burner del atacante'),
 'attacker_other':  ('#8c564b', 90, 'Otra dir. atacante'),
 'clean_exit_funded': ('#2ca02c', 110, 'Salida limpia (fondeada)'),
 'clean_exit_unused': ('#98df8a', 70, 'Salida limpia (sin usar)'),
 'contract': ('#1f77b4', 240, 'Contrato (mixer/pool)'),
 'infrastructure': ('#7f7f7f', 160, 'Infraestructura'),
}
def lab(n): return g.nodes[n].get('label', 'unknown')
node_color = [COL.get(lab(n), ('#cccccc', 40, ''))[0] for n in sub.nodes()]
node_size = [COL.get(lab(n), ('#cccccc', 40, ''))[1] for n in sub.nodes()]

plt.figure(figsize=(13, 10))
pos = nx.spring_layout(sub, k=0.45, iterations=90, seed=7)
nx.draw_networkx_edges(sub, pos, edge_color='#b0b0b0', width=0.5, alpha=0.5, arrows=False)
nx.draw_networkx_nodes(sub, pos, node_color=node_color, node_size=node_size, linewidths=0.3, edgecolors='#333333')
present = {lab(n) for n in sub.nodes()}
handles = [mpatches.Patch(color=COL[k][0], label=COL[k][2]) for k in COL if k in present]
plt.legend(handles=handles, loc='upper right', fontsize=10, framealpha=0.9)
plt.axis('off'); plt.tight_layout()
out = '/mnt/c/Users/Asus/AppData/Local/Temp/claude/C--Users-Asus-Downloads-files/2b4bd8c7-673e-4254-a0dc-583f5d6cecc2/scratchpad/fig4_subgraph_v2.png'
plt.savefig(out, dpi=200, bbox_inches='tight'); plt.close()
from PIL import Image
w, h = Image.open(out).size
print('saved', out, w, h)
