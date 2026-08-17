# TFM AML Multi-Agente

Sistemas adversariales multi-agente basados en LLM para investigación en detección de blanqueo de capitales en criptoactivos:

- **Red team** — un blanqueador multi-agente LLM (Coordinador + sub-agentes Placement / Layering / Integration alineados con la taxonomía FATF) que opera sobre un simulador EVM real. Utiliza ciclos reales por un mezclador ZK estilo Tornado, swaps Uniswap-V2, structuring en USDT, y fan-out sub-$999 hacia múltiples wallets *clean exit*.
- **Blue team** — un detector multi-agente colaborativo que realiza clustering a nivel de actor (identificación de wallets relacionadas) bajo visibilidad parcial del grafo — un agente por exchange simulado. Se compara contra detección de comunidades (Louvain) y una baseline GCN estilo Weber 2019.

Consulta [ROADMAP.md](ROADMAP.md) para la propuesta inicial (planteamiento del problema, contribuciones, metodología, datasets, métricas y cronograma). El estado actual del trabajo está reportado en el TFM completo en `tfm/chapters/`.

## Estado

**Draft del TFM completado (agosto 2026)** — pipeline completo atacante + defensor funcionando end-to-end sobre Anvil local y validado on-chain sobre Sepolia:

| Capa | Estado |
|---|---|
| Simulador Anvil + 6 contratos mock (USDT, Uniswap pool, Tornado mixer, bridge, MiMC, Verifier) | ✅ |
| Mezclador ZK Tornado real (Groth16, MiMC, Merkle) — pruebas reales verificadas on-chain | ✅ |
| Atacante multi-agente FATF — Coordinador + 3 sub-agentes, 19 herramientas on-chain, gestión de gas | ✅ |
| Validado end-to-end: campaña seed 403 de 10 ETH con 92 % de recuperación | ✅ |
| Runner de campañas + generador de baseline benigno (artefactos etiquetados) | ✅ |
| Cargador de runs + extractor del grafo de transacciones + renderer de figuras del TFM | ✅ |
| Combinador de datasets + partición de visibilidad parcial federada | ✅ |
| Quinteto de detectores: Louvain + GCN + coseno + LLM Coordinator (Haiku/Sonnet/Opus) | ✅ |
| Despliegue Sepolia (6 contratos verificables en Etherscan) + campaña real | ✅ |
| Más de 300 tests automáticos, todos en verde | ✅ |

## Instalación

```bash
# 1. Crear entorno conda
conda env create -f environment.yml
conda activate aml-thesis

# 2. Instalar el paquete aml en modo editable (arrastra dependencias core)
pip install -e ".[all]"   # o `pip install -e .` para instalación mínima

# 3. Instalar Foundry (anvil + forge + cast)
curl -L https://foundry.paradigm.xyz | bash
source ~/.bashrc
foundryup
forge --version && anvil --version

# 4. Instalar el stack ZK (Node + snarkjs + circom)
bash scripts/install_zk_tools.sh
source ~/.bashrc

# 5. Trusted setup ZK para el circuito de withdraw (~30 s tras descarga .ptau)
bash scripts/setup_zk.sh withdraw

# 6. Configurar secretos — mínimo ANTHROPIC_API_KEY para el CLI del atacante
cp .env.example .env
# Edita .env y añade: ANTHROPIC_API_KEY=sk-ant-...
```

## Ejecutar el atacante

Una campaña FATF completa de blanqueo ETH, orquestada autónomamente por agentes LLM:

```bash
# Una campaña, ~3 min con Haiku (~$0,30) o ~20 min con Sonnet (~$3)
python -m aml.attackers.run_campaign --scenario defi-exploit --seed 42 --out runs/
```

Salida: directorio con timestamp que contiene el transcript de la campaña, el chain trace completo (eventos ERC-20 / mixer / swap decodificados), direcciones etiquetadas (attacker vs benign + funded vs unused exits), y un resumen human-readable. Ver `--help` para todas las opciones.

## Generar el baseline benigno

Generador sintético de tráfico normal — la clase negativa para el entrenamiento del detector:

```bash
python -m aml.detectors.run_benign --num-users 20 --num-txs 100 --seed 42 --out runs/
```

Mismo esquema de salida que el atacante. Sin mezclador, sin smurfing, sin patrones de consolidación — explícitamente NO es el comportamiento de laundering que el detector debe flagear.

## Extraer grafos + renderizar figuras

```python
from pathlib import Path
from aml.detectors.graph import load_run, to_networkx, summarize_graph
from aml.detectors.viz import draw_run_graph

for run_dir in sorted(Path("runs").iterdir()):
    run = load_run(run_dir)
    g = to_networkx(run)
    print(run_dir.name, summarize_graph(g))
    draw_run_graph(run, f"figs/{run_dir.name}.png")
```

La figura del atacante muestra una wallet source roja, un anillo naranja de burners, **ciclos rojos discontinuos por el mezclador ZK** (la contribución headline de la tesis), y un anillo verde de wallets clean-exit. La figura benigna no tiene ninguna de esas features — sólo una malla densa alrededor del pool.

## Entrenar y evaluar detectores

```python
from pathlib import Path
from aml.detectors.dataset import combine_runs, partial_visibility_split, train_val_test_split
from aml.detectors.baselines import LouvainDetector, derive_binary_labels
from aml.detectors.gnn import GCNDetector
from aml.detectors.multi_agent import (
    MultiAgentDetector, LLMDefenderCoordinator,
    true_actor_clusters, actor_clustering_metrics,
)
from aml.detectors.eval import evaluate, pretty_print

# Combinar todos los runs
ds = combine_runs(sorted(Path("runs").iterdir()))
views = partial_visibility_split(ds, num_exchanges=3, seed=42)
train_runs, val_runs, test_runs = train_val_test_split(ds.all_run_names, seed=42)

# Etiquetas binarias derivadas de las etiquetas canónicas
all_labels = derive_binary_labels(ds.node_labels)
train_labels = {a: l for a, l in all_labels.items()
                if any(r in train_runs for r in ds.graph.nodes[a]["runs"])}

# Detector Louvain baseline + GCN + Multi-agente coseno + LLM coordinator
louvain = LouvainDetector(seed=42).fit(ds.graph, train_labels)
gcn = GCNDetector(epochs=100, seed=42).fit(ds.graph, train_labels)
cosine = MultiAgentDetector(
    detector_factory=lambda: GCNDetector(epochs=100, seed=42),
).fit_per_view(views, train_labels)
llm = LLMDefenderCoordinator(
    detector_factory=lambda: GCNDetector(epochs=100, seed=42),
    llm_model="sonnet",
).fit_per_view(views, train_labels)

# Métricas binarias de detección
test_nodes = [a for a in all_labels
              if any(r in test_runs for r in ds.graph.nodes[a]["runs"])]
test_labels = [all_labels[a] for a in test_nodes]
for name, det in [("Louvain", louvain), ("GCN", gcn),
                  ("Coseno", cosine), ("LLM", llm)]:
    print(name, pretty_print(evaluate(
        test_labels, det.predict(test_nodes), det.predict_proba(test_nodes),
    )))

# Actor clustering (métrica novel de la tesis)
true_actors = true_actor_clusters(ds.runs)
print("Cosine ARI:", actor_clustering_metrics(true_actors, cosine.actor_clusters))
print("LLM ARI:   ", actor_clustering_metrics(true_actors, llm.actor_clusters))
```

## Ejecutar la suite de tests

```bash
# Solo tests estructurales (sin API, sin cadena, sin torch) — ~10 s
pytest tests/test_dataset.py tests/test_detectors_baselines.py tests/test_detectors_multi_agent.py -q

# Añadir tests con cadena (requiere Foundry instalado) — ~30 s
pytest tests/test_tools.py tests/test_graph.py -q

# Añadir el test headline en vivo (requiere ANTHROPIC_API_KEY) — ~3 min, ~$0,30
pytest tests/test_coordinator.py::test_coordinator_runs_full_eth_laundering_campaign -v

# Sweep completa — ~5 min
pytest tests/ -q
```

## Arquitectura del proyecto

```
contracts/         Contratos Solidity mock (compilados con Foundry)
circuits/          Circuitos circom + artefactos snarkjs (build/ gitignored)
src/aml/
  chains/          Manager Anvil + helpers de deploy ETH + extractor chain-trace
  attackers/       Blanqueador multi-agente LLM:
                     coordinator.py    — Orquestador FATF (delegate-only)
                     sub_agent.py      — Especialista FATF tool-scoped
                     prompts.py        — Prompts Placement / Layering / Integration
                     tools.py          — 19 herramientas on-chain (USDT, ETH, swaps, mixer)
                     funder_sizing.py  — Distribución del pool de funders
                     scenarios.py      — Tipologías de campaña (defi-exploit, etc.)
                     run_campaign.py   — CLI para orquestar una campaña + volcar artefactos
  detectors/       Baselines Elliptic (semanas 1-2) + pipeline de evaluación
                   sobre datos simulados (semana 7+):
                     gcn.py, gat.py    — torch.nn.Module para Elliptic baselines
                     graph.py          — chain_trace.jsonl → MultiDiGraph etiquetado
                     dataset.py        — combinador de N runs + split visibilidad parcial
                     viz.py            — renderer de figuras para el TFM (matplotlib)
                     baselines.py      — Detector ABC + LouvainDetector + PerExchangeDetector
                     gnn.py            — GCNDetector (wrapper Detector ABC)
                     multi_agent.py    — MultiAgentDetector coseno + LLMDefenderCoordinator
                     eval.py           — métricas binarias + ARI para clustering
                     run_benign.py     — CLI para generar trazas baseline benigno
  env/             PriceOracle + market_context (fecha campaña, precios ETH/USDT/TRX)
  utils/           Helpers pequeños (env loader, etc.)
data/              Datasets (mayormente gitignored — ver ROADMAP §3.1)
results/           Outputs de experimentos (JSON persistidos en git)
scripts/           Puntos de entrada CLI + setup toolchain ZK
tests/             Más de 300 tests automáticos
tfm/               Draft del TFM (chapters + anexos + bibliografía)
foundry.toml       Config Foundry
```

## Ética y alcance

Todos los experimentos usan datos sintéticos y cadenas locales / testnet forkeadas. **Nunca fondos reales en mainnet.** Las interacciones con Tornado Cash usan contratos mock desplegados localmente en Anvil — nunca los contratos live de mainnet (sancionados por OFAC). El contrato MockUSDT es un artefacto de investigación escrito a mano, sin relación con el Tether real, desplegado sólo en Anvil local. Ver [ROADMAP §6](ROADMAP.md) para el alcance ético completo.
