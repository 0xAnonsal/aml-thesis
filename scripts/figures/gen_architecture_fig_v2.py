# -*- coding: utf-8 -*-
"""Figuras 1 y 2 (v2): same layout as the repo's scratchpad/gen_architecture_fig.py, content corrected:
Fig 1: three sub-agents (not five), structuring sub-umbral (not sub-CTR), Fase 1, Spanish labels, no em-dashes.
Fig 2: seed 803 numbers from summary.txt / aggregate.json (honest recovery 56,8 %; ARI k=3 0,124 ± 0,001 over 3 runs;
LLM cost 0,044 USD from hard_neg_eval_sepolia_803.json), comma decimals, unsupported figures removed."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from pathlib import Path
OUT = Path(__file__).parent

# ================= FIGURA 1: arquitectura =================
fig, ax = plt.subplots(figsize=(12, 7.5), dpi=140)
ax.set_xlim(0, 14); ax.set_ylim(0, 10); ax.axis('off')
c_attacker, c_chain, c_exchange, c_coord = '#e74c3c', '#3498db', '#2ecc71', '#9b59b6'
ax.text(7, 9.5, 'Arquitectura del sistema dual multi-agente', ha='center', fontsize=15, fontweight='bold')
ax.text(7, 9.1, 'Atacante LLM + 3 exchanges federados con visibilidad parcial + coordinador defensor LLM',
        ha='center', fontsize=10, style='italic', color='gray')
att = FancyBboxPatch((0.3, 6.2), 3.5, 2.3, boxstyle="round,pad=0.05", facecolor=c_attacker, edgecolor='black', linewidth=1.5, alpha=0.85)
ax.add_patch(att)
ax.text(2.05, 8.15, 'ATACANTE LLM', ha='center', fontsize=11, fontweight='bold', color='white')
ax.text(2.05, 7.78, 'Coordinador (Sonnet 4.6)', ha='center', fontsize=9, color='white')
ax.text(2.05, 7.48, '+ 3 sub-agentes FATF (Sonnet 4.6):', ha='center', fontsize=8, color='white')
ax.text(2.05, 7.18, '• Placement  • Layering  • Integration', ha='center', fontsize=8, color='white')
ax.text(2.05, 6.85, 'herramientas: mixer ZK, swaps DEX,', ha='center', fontsize=8, color='white')
ax.text(2.05, 6.55, 'structuring sub-umbral, burners', ha='center', fontsize=8, color='white')
chain = FancyBboxPatch((4.5, 5.3), 9.2, 1.6, boxstyle="round,pad=0.05", facecolor=c_chain, edgecolor='black', linewidth=1.5, alpha=0.85)
ax.add_patch(chain)
ax.text(9.1, 6.55, 'CAPA BLOCKCHAIN: Ethereum (Anvil-dev / Sepolia-prod)', ha='center', fontsize=11, fontweight='bold', color='white')
ax.text(9.1, 6.15, '4 contratos propios verificados en Etherscan (MockUSDT · MockUniswapV2Pool · MockTornado ZK · MockBridge)', ha='center', fontsize=8, color='white')
ax.text(9.1, 5.85, '+ MiMCSponge (bytecode) y Verifier (generado por snarkjs)', ha='center', fontsize=8, color='white')
ax.text(9.1, 5.5, 'Grafo de transacciones ERC-20 + ETH nativas + eventos on-chain (chain_trace.jsonl)', ha='center', fontsize=8, color='white')
ax.add_patch(FancyArrowPatch((3.85, 6.9), (4.5, 6.45), arrowstyle='->', mutation_scale=20, color='black', linewidth=2))
ax.text(3.6, 7.15, 'ejecuta tx on-chain', fontsize=8, color='black', ha='left')
names = ['Exchange 1\n(tipo Binance)', 'Exchange 2\n(tipo Coinbase)', 'Exchange 3\n(tipo Kraken)']
xs = [1.5, 5.5, 9.5]
for i, (name, xpos) in enumerate(zip(names, xs)):
    ax.add_patch(FancyBboxPatch((xpos, 2.8), 3, 1.8, boxstyle="round,pad=0.05", facecolor=c_exchange, edgecolor='black', linewidth=1.5, alpha=0.85))
    ax.text(xpos + 1.5, 4.52, name, ha='center', va='top', fontsize=10, fontweight='bold', color='white')
    ax.text(xpos + 1.5, 3.75, f'Vista LOCAL V_{i + 1}', ha='center', fontsize=9, color='white')
    ax.text(xpos + 1.5, 3.45, '(solo sus usuarios con KYC)', ha='center', fontsize=7.5, color='white')
    ax.text(xpos + 1.5, 3.1, 'Louvain Fase 1 → F1', ha='center', fontsize=8, color='white', style='italic')
    x0 = max(xpos + 1.5, 4.9)   # the chain box starts at x=4.5: exchange 1 gets a diagonal arrow from its left edge
    ax.add_patch(FancyArrowPatch((x0, 5.3), (xpos + 1.5, 4.62), arrowstyle='->', mutation_scale=15, color='black', linewidth=1.5))
coord = FancyBboxPatch((3.5, 0.5), 7, 1.8, boxstyle="round,pad=0.05", facecolor=c_coord, edgecolor='black', linewidth=1.5, alpha=0.85)
ax.add_patch(coord)
ax.text(7, 1.95, 'COORDINADOR DEFENSOR LLM', ha='center', fontsize=11, fontweight='bold', color='white')
ax.text(7, 1.55, 'Haiku 4.5 + fusión post-hoc P1-71 + elección automática de k por silhouette (P1-73)', ha='center', fontsize=8.5, color='white')
ax.text(7, 1.2, 'Recibe fingerprints agregados (19 dimensiones) → propone actor clusters cross-exchange', ha='center', fontsize=8.5, color='white')
ax.text(7, 0.85, 'SALIDA: (a) ARI de actor clusters  (b) razonamiento textual auditable  (el F1 binario lo da Louvain, Fase 1)', ha='center', fontsize=7.6, color='white', style='italic')
for xpos in xs:
    ax.add_patch(FancyArrowPatch((xpos + 1.5, 2.8), (7, 2.3), arrowstyle='->', mutation_scale=15, color='black', linewidth=1.5, connectionstyle="arc3,rad=0.1", zorder=2))
ax.text(7, 2.55, 'fingerprints AGREGADOS de 19 dimensiones (NO datos crudos)', ha='center', fontsize=8, color='#c0392b', fontweight='bold', zorder=5,
        bbox=dict(facecolor='white', edgecolor='none', pad=1.5))
ax.text(0.3, 0.15, '● Novedad: cada exchange solo ve su subgrafo local; el coordinador razona sobre fingerprints sin acceder a datos crudos',
        fontsize=8.5, color='#c0392b', fontweight='bold')
plt.tight_layout()
fig.savefig(OUT / 'fig1_architecture_v2.png', dpi=150, bbox_inches='tight', facecolor='white'); plt.close()
print('Wrote fig1_architecture_v2.png')

# ================= FIGURA 2: flujo end-to-end (seed 803) =================
fig, ax = plt.subplots(figsize=(13, 5), dpi=140)
ax.set_xlim(-0.35, 15.35); ax.set_ylim(0, 7); ax.axis('off')
ax.text(7.5, 6.7, 'Flujo end-to-end de una campaña atacante y su detección', ha='center', fontsize=14, fontweight='bold')
ax.text(7.5, 6.35, 'Ejemplo: seed 803 (defi-exploit, 22,6 ETH, Sepolia real)', ha='center', fontsize=9, style='italic', color='gray')
phases = [
    ('PLACEMENT\n(origen del exploit)', '1. Alice roba 22,6 ETH\ndel pool DEX vulnerable', 1, '#e74c3c'),
    ('LAYERING\n(mixer + swaps)', '2. Mixer ZK (Groth16)\n+ swaps Uniswap V2\n+ cadena de burners', 4, '#e67e22'),
    ('INTEGRATION\n(cash-out)', '3. Structuring sub-umbral\n(999 USD) → 60 clean exits\nregistradas, 54 fondeadas', 7, '#f1c40f'),
    ('DETECCIÓN\n(3 exchanges)', '4. Louvain Fase 1\nsobre vistas parciales\n→ direcciones marcadas', 10, '#2ecc71'),
    ('CLUSTERING\n(LLM + P1-71)', '5. Haiku 4.5 + fusión\n→ actor clusters\ncon razonamiento', 13, '#9b59b6'),
]
for name, desc, xc, color in phases:
    ax.add_patch(FancyBboxPatch((xc - 1.1, 2.8), 2.2, 2.5, boxstyle="round,pad=0.05", facecolor=color, edgecolor='black', linewidth=1.5, alpha=0.85))
    ax.text(xc, 4.9, name, ha='center', fontsize=10, fontweight='bold', color='white')
    ax.text(xc, 3.7, desc, ha='center', fontsize=8, color='white')
for i in range(len(phases) - 1):
    ax.add_patch(FancyArrowPatch((phases[i][2] + 1.1, 4.05), (phases[i + 1][2] - 1.1, 4.05), arrowstyle='->', mutation_scale=25, color='black', linewidth=2))
ax.text(7.5, 1.9, 'RESULTADOS EMPÍRICOS (seed 803, defi-exploit, Sepolia real):', ha='center', fontsize=10, fontweight='bold', color='#2c3e50')
metrics = [
    ('Recuperación honesta', '56,8 %', 1),
    ('F1 Louvain', '0,981', 4),
    ('F1 con negativos duros', '0,973', 7),
    ('ARI P1-71 (k=3, 3 ejec.)', '0,124 ± 0,001', 10),
    ('Coste LLM defensor', '0,044 USD', 13),
]
for label, value, xc in metrics:
    ax.text(xc, 1.4, label, ha='center', fontsize=8.5, color='#34495e')
    ax.text(xc, 0.9, value, ha='center', fontsize=11, fontweight='bold', color='#2c3e50')
ax.text(7.5, 0.2, 'Campaña atacante: 77 min, 42 812 tx trazadas, 141 wallets, coste LLM 6,53 USD (Sonnet). Fuentes: summary.txt de la seed 803, results/reps/aggregate.json, results/hard_neg_eval_sepolia_803.json',
        ha='center', fontsize=7.8, color='#7f8c8d', style='italic')
plt.tight_layout()
fig.savefig(OUT / 'fig2_flow_v2.png', dpi=150, bbox_inches='tight', facecolor='white'); plt.close()
print('Wrote fig2_flow_v2.png')
