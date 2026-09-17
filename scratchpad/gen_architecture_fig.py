"""Generate 2 architecture PNG figures for TFM:
  1. System architecture: attacker + 3 federated exchanges + LLM coordinator
  2. End-to-end flow: campaign lifecycle from placement → layering → integration
"""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
from pathlib import Path

OUT = Path('/home/anon/aml-thesis/tfm/figures')
OUT.mkdir(exist_ok=True)

# ================= FIGURE 1: System Architecture =================
fig, ax = plt.subplots(figsize=(12, 7.5), dpi=140)
ax.set_xlim(0, 14); ax.set_ylim(0, 10)
ax.axis('off')

# Colors
c_attacker = '#e74c3c'
c_chain = '#3498db'
c_exchange = '#2ecc71'
c_coord = '#9b59b6'
c_defender = '#f39c12'
c_bg = '#ecf0f1'

# Title
ax.text(7, 9.5, 'Arquitectura del sistema dual multi-agente',
        ha='center', fontsize=15, fontweight='bold')
ax.text(7, 9.1, 'Atacante LLM + 3 exchanges federados con visibilidad parcial + coordinador defensor LLM',
        ha='center', fontsize=10, style='italic', color='gray')

# --- Attacker box (top left) ---
att = FancyBboxPatch((0.3, 6.2), 3.5, 2.3, boxstyle="round,pad=0.05",
                    facecolor=c_attacker, edgecolor='black', linewidth=1.5, alpha=0.85)
ax.add_patch(att)
ax.text(2.05, 8.15, 'ATACANTE LLM', ha='center', fontsize=11, fontweight='bold', color='white')
ax.text(2.05, 7.75, 'Opus 4.7 Coordinator', ha='center', fontsize=9, color='white')
ax.text(2.05, 7.45, '+ 5 sub-agentes FATF:', ha='center', fontsize=8, color='white')
ax.text(2.05, 7.15, '• Placement  • Layering', ha='center', fontsize=8, color='white')
ax.text(2.05, 6.85, '• Mixer ZK   • DEX swap', ha='center', fontsize=8, color='white')
ax.text(2.05, 6.55, '• Structuring sub-CTR', ha='center', fontsize=8, color='white')

# --- Blockchain layer (middle) ---
chain = FancyBboxPatch((4.5, 5.3), 9.2, 1.6, boxstyle="round,pad=0.05",
                      facecolor=c_chain, edgecolor='black', linewidth=1.5, alpha=0.85)
ax.add_patch(chain)
ax.text(9.1, 6.55, 'CAPA BLOCKCHAIN — Ethereum (Anvil-dev / Sepolia-prod)', ha='center',
        fontsize=11, fontweight='bold', color='white')
ax.text(9.1, 6.1, '6 contratos verificados: MockUSDT · MockUniswapV2Pool · MockTornado(ZK) · MockBridge · MiMCSponge · Verifier',
        ha='center', fontsize=8, color='white')
ax.text(9.1, 5.7, 'Grafo de transacciones ERC-20 + ETH nativas + eventos on-chain (chain_trace.jsonl)',
        ha='center', fontsize=8, color='white')

# Arrow: attacker -> chain
arr1 = FancyArrowPatch((2.05, 6.2), (2.05, 5.6), arrowstyle='->', mutation_scale=20, color='black', linewidth=2)
ax.add_patch(arr1)
ax.text(2.55, 5.85, 'ejecuta tx\non-chain', fontsize=8, color='black')

# --- 3 Exchanges (middle-bottom) ---
exchange_names = ['Exchange 1\n(Binance-like)', 'Exchange 2\n(Coinbase-like)', 'Exchange 3\n(Kraken-like)']
exchange_positions = [1.5, 5.5, 9.5]
for i, (name, xpos) in enumerate(zip(exchange_names, exchange_positions)):
    ex = FancyBboxPatch((xpos, 2.8), 3, 1.8, boxstyle="round,pad=0.05",
                       facecolor=c_exchange, edgecolor='black', linewidth=1.5, alpha=0.85)
    ax.add_patch(ex)
    ax.text(xpos+1.5, 4.3, name, ha='center', fontsize=10, fontweight='bold', color='white')
    ax.text(xpos+1.5, 3.75, f'Vista LOCAL V_{i+1}', ha='center', fontsize=9, color='white')
    ax.text(xpos+1.5, 3.45, '(KYC-verified users only)', ha='center', fontsize=7.5, color='white')
    ax.text(xpos+1.5, 3.1, 'Louvain Phase 1 → F1', ha='center', fontsize=8, color='white', style='italic')
    # Arrow: chain -> exchange
    arr = FancyArrowPatch((xpos+1.5, 5.3), (xpos+1.5, 4.6), arrowstyle='->', mutation_scale=15, color='black', linewidth=1.5)
    ax.add_patch(arr)

# --- LLM Coordinator (bottom center) ---
coord = FancyBboxPatch((3.5, 0.5), 7, 1.8, boxstyle="round,pad=0.05",
                      facecolor=c_coord, edgecolor='black', linewidth=1.5, alpha=0.85)
ax.add_patch(coord)
ax.text(7, 1.95, 'DEFENSOR LLM COORDINATOR', ha='center', fontsize=11, fontweight='bold', color='white')
ax.text(7, 1.55, 'Haiku 4.5 + P1-71 post-hoc merge + P1-73 silhouette auto-tune', ha='center',
        fontsize=8.5, color='white')
ax.text(7, 1.2, 'Recibe fingerprints agregados (19-dim) → propone actor clusters cross-exchange', ha='center',
        fontsize=8.5, color='white')
ax.text(7, 0.85, 'OUTPUT: (a) binary F1  (b) actor clustering ARI  (c) razonamiento textual auditable', ha='center',
        fontsize=8.5, color='white', style='italic')

# Arrows: exchanges -> coordinator
for xpos in exchange_positions:
    arr = FancyArrowPatch((xpos+1.5, 2.8), (7, 2.3), arrowstyle='->', mutation_scale=15, color='black', linewidth=1.5,
                          connectionstyle="arc3,rad=0.1")
    ax.add_patch(arr)

# Text for arrows
ax.text(7, 2.55, 'fingerprints AGREGADOS 19-dim (NO datos crudos)', ha='center', fontsize=8,
        color='#c0392b', fontweight='bold')

# Legend
ax.text(0.3, 0.15, '● Novedad: cada exchange sólo ve su subgrafo local — el coordinador razona sobre fingerprints sin acceder a datos crudos',
        fontsize=8.5, color='#c0392b', fontweight='bold')

plt.tight_layout()
fig.savefig(OUT / 'architecture.png', dpi=150, bbox_inches='tight', facecolor='white')
print(f'Wrote {OUT / "architecture.png"}')
plt.close()


# ================= FIGURE 2: End-to-end flow =================
fig, ax = plt.subplots(figsize=(13, 5), dpi=140)
ax.set_xlim(0, 15); ax.set_ylim(0, 7)
ax.axis('off')

ax.text(7.5, 6.7, 'Flujo end-to-end de una campaña attacker + detección defensor',
        ha='center', fontsize=14, fontweight='bold')
ax.text(7.5, 6.35, 'Ejemplo: seed 803 (defi-exploit, 22.6 ETH, Sepolia real)',
        ha='center', fontsize=9, style='italic', color='gray')

phases = [
    ('PLACEMENT\n(exploit source)', '1. Alice roba 22.6 ETH\ndel pool DEX vulnerable', 1, '#e74c3c'),
    ('LAYERING\n(mixer + swaps)', '2. Mixer ZK (Groth16)\n+ Uniswap V2 swaps\n+ burners chain', 4, '#e67e22'),
    ('INTEGRATION\n(cash-out)', '3. Structuring sub-$999\n→ 20+ clean-exit\naddresses distribuidas', 7, '#f1c40f'),
    ('DETECTION\n(3 exchanges)', '4. Louvain Phase 1\nsobre vistas parciales\nflags → 149 addrs', 10, '#2ecc71'),
    ('CLUSTERING\n(LLM + P1-71)', '5. Haiku 4.5 + merge\n→ actor clusters\ncon razonamiento', 13, '#9b59b6'),
]

for name, desc, xcenter, color in phases:
    box = FancyBboxPatch((xcenter-1.1, 2.8), 2.2, 2.5, boxstyle="round,pad=0.05",
                        facecolor=color, edgecolor='black', linewidth=1.5, alpha=0.85)
    ax.add_patch(box)
    ax.text(xcenter, 4.9, name, ha='center', fontsize=10, fontweight='bold', color='white')
    ax.text(xcenter, 3.7, desc, ha='center', fontsize=8, color='white')

# Arrows connecting phases
for i in range(len(phases)-1):
    x_from = phases[i][2] + 1.1
    x_to = phases[i+1][2] - 1.1
    arr = FancyArrowPatch((x_from, 4.05), (x_to, 4.05), arrowstyle='->',
                         mutation_scale=25, color='black', linewidth=2)
    ax.add_patch(arr)

# Metrics at bottom
ax.text(7.5, 1.9, 'RESULTADOS EMPÍRICOS (seed 803 defi-exploit — Sepolia real):',
        ha='center', fontsize=10, fontweight='bold', color='#2c3e50')
metrics = [
    ('Fondos recuperados', '96%', 1),
    ('F1 Louvain', '0.981', 4),
    ('F1 con hard-neg', '0.973', 7),
    ('ARI P1-71 max_c=3', '0.122', 10),
    ('Cost LLM defensor', '$0.045', 13),
]
for label, value, xcenter in metrics:
    ax.text(xcenter, 1.4, label, ha='center', fontsize=8.5, color='#34495e')
    ax.text(xcenter, 0.9, value, ha='center', fontsize=11, fontweight='bold', color='#2c3e50')

# Cost bar at very bottom
ax.text(7.5, 0.2, 'Coste total pipeline validación defensor: ~$1.60 en llamadas LLM · Reproducible con budget < 300 EUR',
        ha='center', fontsize=8.5, color='#7f8c8d', style='italic')

plt.tight_layout()
fig.savefig(OUT / 'end_to_end_flow.png', dpi=150, bbox_inches='tight', facecolor='white')
print(f'Wrote {OUT / "end_to_end_flow.png"}')
plt.close()

print('Both figures generated successfully.')
