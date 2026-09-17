# Capítulo 5 — Diseño, dataset y lenguajes

Este capítulo reúne el diseño arquitectónico del sistema, los
lenguajes empleados en la implementación, y la especificación de
datasets y parámetros. Se divide en cuatro bloques principales:

- **§5.A Diseño y diagramas** — vista general del sistema, arquitecturas atacante y defensor, flujo end-to-end.
- **§5.B Arquitectura de software** — capas de contratos, blockchain, herramientas y coordinación multi-agente.
- **§5.C Lenguajes empleados** — Python, Solidity, Circom, JavaScript, Bash, Markdown con LOC por lenguaje.
- **§5.D Datasets y parámetros** — datasets propios y externos, particionado federado, configuración de detectores y LLMs.

---

## 5.0 Novedad central del TFM — visibilidad parcial federada por exchange

Antes de exponer el diseño técnico se articula, en una única sección
compacta, la novedad que este trabajo aporta al estado del arte
descrito en el Capítulo 2 y a las limitaciones documentadas en el
Capítulo 3.

### 5.0.1 El problema que nadie ha modelado

Toda la literatura académica AML sobre criptoactivos (Weber 2019,
Wu 2023, Elmougy 2023, Juvinski 2025) asume implícitamente **una
vista global del grafo** — un observador omnisciente que ve todas
las transacciones on-chain y todos sus emisores/receptores. En la
práctica regulatoria esto no ocurre: bajo FATF Rec. 16 y Reglamento
(UE) 2023/1113, **cada exchange sólo puede identificar KYC a sus
propios usuarios**. Las direcciones de otros exchanges y de wallets
no-KYC son observadas como *contrapartes anónimas*. En consecuencia,
ningún actor individual dispone de la visión global sobre la que
operan los detectores publicados.

**Esta brecha entre el modelo académico (grafo completo) y la
realidad regulatoria (vistas parciales federadas) es la novedad
central de este TFM.**

### 5.0.2 Propuesta: federación cross-exchange con fingerprints

La arquitectura propuesta modela explícitamente n exchanges
federados donde:

1. Cada exchange E_i observa **únicamente su subgrafo local**
   `G_i = (V_i, E_i)` obtenido por *hashing determinista* de las
   direcciones sobre {1..n} (§5.A.4).
2. Cada exchange entrena su propio clasificador binario Louvain
   sobre G_i, produciendo flags locales `S_i ⊂ V_i`.
3. Los exchanges comparten **NO datos crudos** sino **fingerprints
   agregados** de 19 dimensiones por dirección flageada. Estos
   fingerprints son propiedades topológicas (`in_degree`, `log_usdt_in`,
   etc.) que **no revelan** el patrón de contrapartes específico ni
   permiten re-identificar la actividad de un usuario particular fuera
   del subgrafo local. Cumple los requisitos de minimización de datos
   del art. 25 RGPD.
4. Un coordinador cross-exchange —basado en LLM— razona sobre los
   fingerprints agregados de todos los exchanges y propone
   agrupaciones actor-cluster: qué direcciones flageadas en distintos
   exchanges probablemente pertenecen al mismo actor adversarial
   subyacente.

### 5.0.3 Por qué esto es publishable

Ningún trabajo previo del estado del arte cumple simultáneamente los
tres siguientes criterios:

| Criterio | Weber 2019 | Wu 2023 | Elmougy 2023 | Juvinski 2025 | **Este TFM** |
|----------|:---------:|:-------:|:------------:|:-------------:|:------------:|
| Modelado explícito de visibilidad parcial multi-exchange | ❌ | ❌ | ❌ | ❌ | **✓** |
| Coordinador cross-exchange que razona sin acceso a datos crudos | ❌ | ❌ | ❌ | ❌ | **✓** |
| Validación empírica bajo particionado federado n=3 | ❌ | ❌ | ❌ | ❌ | **✓** |

Los resultados empíricos (§8.9.K multi-campaign LOCO F1 = 0.939 con
34 578 nodos y 7 campañas simultáneas + §8.9.49 role attribution
ARI = 0.43 sobre 18 clusters role×campaign) demuestran que la
arquitectura propuesta **funciona operativamente** bajo las
condiciones adversariales del despliegue real: multiples campañas
concurrentes, visibilidad parcial cero cross-exchange, sin re-training
por dataset.

### 5.0.4 Diagrama de la arquitectura

![Figura 1. Arquitectura del sistema dual multi-agente: atacante LLM (Opus 4.7) ejecuta transacciones on-chain; los 3 exchanges federados observan sólo sus vistas locales KYC-verificadas; el coordinador LLM defensor (Haiku 4.5) razona sobre fingerprints agregados sin acceso a datos crudos.](tfm/figures/architecture.png)

### 5.0.5 Implicaciones regulatorias

La arquitectura propuesta es directamente aplicable al despliegue
comercial post-MiCA (Reglamento (UE) 2023/1114, vigencia plena 2027):

- **Rec. 16 FATF** (travel rule): cada exchange comparte fingerprints
  agregados, no PII, cumpliendo la restricción de compartición.
- **Rec. 20 FATF** (transparencia SAR): el LLM coordinator produce
  razonamiento textual auditable por rol AML (§8.7).
- **MiCA art. 63** (transparencia algorítmica): los outputs LLM son
  interpretables por un compliance officer sin conocimiento de ML.

## 5.A Diseño del sistema — vista general

El sistema se organiza en **cinco capas** integradas verticalmente:
(1) contratos on-chain, (2) blockchain (Anvil/Sepolia), (3) módulos
Python (dispatcher, detectores, oracle), (4) coordinadores LLM, y
(5) exchanges federados observando vistas parciales. La Figura 1 de
§5.0.4 muestra la arquitectura completa; la Figura 2 de §6.0 muestra
el flujo temporal end-to-end de una campaña.

### 5.A.1 Atacante multi-agente

El atacante se compone de un **coordinador Opus 4.7** que planifica la
campaña siguiendo la taxonomía FATF (placement / layering /
integration) y delega ejecución en sub-agentes especializados:

- `placement_agent` — controla la fase inicial (source wallet →
  primeros burners).
- `mixer_agent` — coordina depósitos y retiradas del `MockTornado`
  con generación de pruebas Groth16 off-chain (snarkjs).
- `swap_agent` — ejecuta operaciones sobre `MockUniswapV2Pool`
  aplicando slippage realista basado en el oracle de precios.
- `bridge_agent` — simula transferencias cross-chain vía
  `MockBridge` (destino simbólico, log estructurado).
- `integration_agent` — coordina structuring sub-CTR y distribución
  final a `clean_exit_wallets`.

Todos los sub-agentes comparten el `ToolDispatcher` (§5.B.3) que
enforce las cinco invariants estructurales del catálogo on-chain.

### 5.A.2 Defensor multi-agente cross-exchange

El defensor tiene arquitectura simétrica al atacante. **Phase 1 local**
por exchange: un clasificador Louvain (baseline) o GCN (aprendido)
opera sobre la vista parcial `G_i` de cada exchange y produce
`predict_proba(addr) → [0,1]`. **Phase 2 cross-exchange**: un
coordinador Haiku 4.5 recibe los top-K flagged addresses por
exchange con sus fingerprints agregados 19-dim y propone actor
clusters cross-exchange con razonamiento textual.

El post-procesamiento P1-71 (§8.9.G) mergea clusters por centroid
distance hasta `max_clusters`; P1-73 (§8.9.H) elige `max_clusters`
por silhouette score sin ground truth.

### 5.A.3 Flujo end-to-end (una campaña completa)

Ver Figura 2 en §6.0. Resumidamente: (1) Alice roba fondos del pool
DEX vulnerable (placement); (2) mixer ZK + swaps + burners chain
(layering); (3) structuring sub-CTR a clean-exit distribuidas
(integration); (4) los 3 exchanges observan sus vistas parciales y
Louvain flagea localmente (detection); (5) el LLM cluster agrupa
cross-exchange con razonamiento textual (clustering). Los datos
generados quedan persistidos en `chain_trace.jsonl` +
`addresses.json` + `campaign.json` como artefactos verificables.

### 5.A.4 Modelo de datos — federación bajo visibilidad parcial

`partial_visibility_split(combined, num_exchanges=3, seed=42)` reparte
las direcciones observables entre los exchanges mediante hashing
determinista `hash(address) % num_exchanges`. Cada exchange observa
sólo aquellos edges donde AMBOS extremos son direcciones asignadas
a él, garantizando la restricción realista de que un exchange no
puede observar transacciones que no involucran a sus usuarios KYC.

### 5.A.5 Stack de la prueba ZK

Circuit Circom 2.0 (~60 LOC) implementa Merkle-tree verification
sobre el commitment del depósito, con hasher MiMCSponge. Compilación
via `circom` → R1CS → `snarkjs groth16 setup` → `verification_key.json`.
On-chain: contrato `Verifier` (Groth16) generado por `snarkjs
export solidityverifier`. La prueba se genera off-chain en Node.js
(~200 LOC de puente) y se envía al método `mixer_withdraw` del
`MockTornado`.

## 5.B Arquitectura del software

### 5.B.1 Capa de contratos on-chain (Solidity 0.8.20)

Seis contratos hand-written (no herencia de OpenZeppelin para evitar
dependencies innecesarias y minimizar surface adversarial):

- **MockUSDT** — ERC-20 minimal con `mint()` público y decimals=6
  para emular el USDT real.
- **MockUniswapV2Pool** — pool ETH/USDT constant-product (x·y=k) con
  swap, `addLiquidity`, `removeLiquidity`. Slippage real.
- **MockTornado** — mezclador estilo Tornado con soporte
  multi-denominación (0.1 / 1 / 10 ETH) y verificación Groth16.
- **MockBridge** — emisor de eventos `BridgeInitiated(dst_chain,
  amount)` para simular cross-chain sin destino real.
- **MiMCSponge** — hasher on-chain requerido por el circuit ZK.
- **Verifier** — contrato Groth16 generado por snarkjs.

Todos verificados en Sepolia Etherscan. Direcciones y hashes en el
repo (`deployments/sepolia.json`).

### 5.B.2 Capa blockchain (Foundry)

**Anvil** — sandbox EVM local instantáneo (~2 s startup). Uso
principal durante desarrollo. Permite reset de estado a placer, mint
ETH ilimitado, mine blocks manuales para test de temporalidad.

**Sepolia** — testnet pública para validación externa. Cada
contrato desplegado se verifica con `forge verify-contract` para
que sea inspectable por un auditor independiente en Etherscan.

### 5.B.3 Catálogo de herramientas on-chain (`ToolDispatcher`)

32 herramientas expuestas al LLM atacante, organizadas en 5 grupos
funcionales:

| Grupo | Herramientas (n) | Ejemplos |
|-------|:----:|----------|
| Transferencias directas | 4 | `transfer_eth`, `transfer_usdt` |
| Swaps DEX | 4 | `swap_eth_for_usdt`, `swap_usdt_for_eth` |
| Mixer ZK | 6 | `mixer_deposit`, `mixer_withdraw`, `mixer_recover` |
| Bridge cross-chain | 2 | `bridge_send`, `bridge_receive_mock` |
| Gestión de wallets | 8 | `generate_burner_wallet`, `register_clean_exit` |
| Coordinación / audit | 8 | `report_status`, `snapshot_state` |

**Cinco invariants estructurales** (validados en cada dispatch):

1. **Balance-preserving** — cada tx concilia balance total del sistema.
2. **Gas-sovereign** — cada burner recibe gas suficiente para su
   próxima operación sin depender de un funder externo (P1-42 A+B+D+G+).
3. **Deterministic seeding** — mismo seed reproduce misma campaña.
4. **Fail-safe** — errores capturados y reportados sin corromper estado.
5. **Auditable** — cada tx logueada con `from, to, value, gas, event`.

### 5.B.4 Orquestación multi-agente del atacante

`Coordinator` (Opus 4.7) recibe el escenario (`defi-exploit`,
`ransomware-cashout`, `stablecoin-scam`) + amount target + seed, y
planifica la campaña como secuencia de `Task`s. Cada Task delega en
un sub-agente con contexto local. Los sub-agentes usan tool-use
para llamar al `ToolDispatcher`; el coordinador supervisa el retorno
y decide si continuar, reintentar o abortar. Timeout total: 6 h en
Sepolia (por block time), 30 min en Anvil.

### 5.B.5 Arquitectura simétrica del defensor

`LLMDefenderCoordinator` (Haiku 4.5) tiene la misma arquitectura
que el atacante Coordinator pero opera en el otro lado:

- **Input**: `views: list[ExchangeView]` + `train_labels: dict[str, int]`.
- **Phase 1**: `PerExchangeDetector.fit_per_view(views, train_labels)`
  entrena un Louvain por exchange.
- **Phase 2**: `_build_llm_user_prompt()` construye el prompt con
  top-K flagged addresses × 3 exchanges; `LLMClient.complete()`
  ejecuta Haiku; `_parse_llm_clusters()` extrae el actor_clusters
  del output.
- **Post-hoc**: si `max_clusters` set, `_merge_clusters_by_centroid()`
  reduce a max_c; si `max_clusters="auto"`, `_auto_pick_max_clusters()`
  elige por silhouette.

### 5.B.6 Federación y visibilidad parcial

Ya cubierta en §5.0.2 y §5.A.4. El punto clave arquitectónico: cada
`ExchangeView` es un `nx.MultiDiGraph` subgrafo del combined + su
`visible_addresses`. El coordinador recibe **fingerprints** por
exchange (no subgrafos). Esto respeta la privacidad y mantiene el
tamaño del prompt LLM tratable (~20k tokens).

### 5.B.7 Repositorio y reproducibilidad

Todo el código bajo MIT en `github.com/0xAnonsal/aml-thesis`. Cada
tabla de §8 tiene su comando de reproducción exacto. Los prompts
completos están en `src/aml/attackers/prompts.py` y
`src/aml/detectors/multi_agent.py:_LLM_COORDINATOR_SYSTEM_PROMPT`.
Los contratos verificados en Sepolia Etherscan. 47 tests unitarios
en `tests/`.

## 5.C Lenguajes de programación empleados

El TFM combina seis lenguajes según sus fortalezas específicas.
Total ~10 100 líneas de código propio.

| Lenguaje | Versión | LOC | Uso |
|----------|---------|----:|-----|
| **Python** | 3.11 | ~9 000 | Simulador atacante, detectores, pipeline de evaluación, agentes LLM, análisis. Ecosistema ML/data-sci (PyTorch Geometric, NetworkX, scikit-learn, pandas). Justificación: madurez del stack para grafos + ML + LLM clients. |
| **Solidity** | 0.8.20 | ~800 | 6 contratos on-chain (MockUSDT, MockUniswapV2Pool, MockTornado, MockBridge, MiMCSponge, Verifier). Compilación con Foundry (`forge build`). Justificación: lenguaje estándar para smart contracts EVM. |
| **Circom** | 2.0 | ~60 | Circuit ZK del mezclador (Merkle-tree verification sobre commitment). Justificación: soporte maduro para Groth16 y ecosystem snarkjs. |
| **JavaScript** (Node.js) | 20 LTS | ~200 | Puente off-chain: generación de pruebas Groth16 vía snarkjs, empaquetado del `proof` para envío a `mixer_withdraw`. Justificación: snarkjs es la implementación de referencia. |
| **Bash** | GNU 5.x | ~100 | Scripts de setup, orquestación de campañas paralelas, deployment Sepolia. Justificación: infrastructure glue estándar. |
| **Markdown** | CommonMark | ~4 000 | Documentación del proyecto + draft del TFM (chapters_v3/*.md). Justificación: source-of-truth versionable en Git + conversión a docx via python-docx script. |

**Nota sobre la elección de stack**: la combinación
Python+Solidity+Circom refleja el estándar de-facto en la
investigación de aplicaciones ZK sobre Ethereum. Alternativas
evaluadas y descartadas: Rust (compilación más lenta, menor
ecosystem ML), Vyper (menos maduro que Solidity en ecosistema
DeFi), Halo2 (curva de aprendizaje mayor que Groth16 sin ganancia
para el TFM).


## 5.D Datasets y parámetros


Este capítulo describe el diseño experimental que produce los resultados
del Capítulo 8. Se detallan: (i) los cuatro *datasets* utilizados y su
provenance; (ii) el particionado federado en n=3 exchanges; (iii) la
configuración exacta de cada detector; (iv) la estrategia de
*cross-validation* diseñada para detectar y prevenir memorización;
(v) las métricas de evaluación; (vi) la parametrización de los
componentes LLM; (vii) las semillas de reproducibilidad;
(viii) las constantes finales *locked* del atacante refinado; y (ix)
el protocolo de validación externa sobre la testnet Sepolia.
El objetivo es que un lector independiente pueda regenerar cualquier
tabla o figura del Capítulo 8 con un solo comando desde el repositorio
del proyecto.

## 9.1 Datasets

Se emplean cuatro *datasets* complementarios: uno propio (simulación
adversarial multi-agente) y tres externos (uno real, dos benchmarks
académicos).

### 9.1.1 Simulación propia — `dataset.pkl`

El *dataset* principal se genera mediante el simulador ofensivo
descrito en el Capítulo 6, ejecutado sobre una copia local de la red
Ethereum (Foundry/Anvil). Cada corrida instancia el pipeline extremo-a-
extremo: despliegue de los seis contratos mock, arranque del
coordinador Opus 4.7, ejecución de una campaña completa por los
sub-agentes Sonnet 4.6, y captura del grafo de transacciones
resultante.

El *dataset* combinado (`~/aml-results/batch_2026-06-26/dataset.pkl`,
254 MB) integra 420 corridas: 20 campañas atacantes distribuidas
uniformemente entre tres escenarios (*defi-exploit*, *stablecoin-scam*
y *ransomware-cashout*, PRs 48-49 del repositorio) y 400 campañas
benignas (semillas 200-599), estas últimas ejecutadas por el módulo
`src/aml/detectors/run_benign.py` que simula tráfico de exchanges y
usuarios legítimos con las mismas herramientas del catálogo pero sin
el objetivo de blanqueo. La distribución de clases es realista: 649
direcciones adversariales (6,3 %) sobre un total de 10 311 nodos y
101 880 aristas, alineada con el rango de prevalencia AML reportado
por FATF para el ecosistema cripto (< 10 %).

El *ground truth* se estructura en dos niveles: (i) etiqueta binaria
y_v ∈ {0, 1} por dirección; (ii) partición latente de las 649
direcciones adversariales en 20 *actor clusters* (uno por campaña,
con tamaño heterogéneo entre 9 y 83 direcciones), más 9 584 *actor
singletons* correspondientes a wallets benignas. Esta doble
estructura permite evaluar separadamente la detección binaria local
(F1) y la atribución de actor cluster cross-exchange (ARI).

### 9.1.2 EthereumHeist (Wu et al. 2023)

**EthereumHeist** es el *dataset* real de referencia utilizado para
validación externa. Contiene 23 casos reales de hackeos y robos
sobre Ethereum mainnet, propagados forensicamente desde direcciones
semilla identificadas: 633 057 nodos, 2 452 786 aristas. El adaptador
`scripts/load_ethereum_heist.py` fusiona las 23 carpetas de hack en
un único `MultiDiGraph` y produce el *pickle* combinado
`data/ethereum_heist_combined.pkl` (285 MB).

Cuatro de los 23 hackeos son marcadamente asimétricos en escala
—UpbitHack (263 k nodos), PlusTokenPonzi (155 k), AscendEXHacker
(85 k), BitpointHacker (44 k)— y dominarían cualquier evaluación
combinada. Los experimentos del Capítulo 8 reportan tanto la
configuración completa como la configuración `--exclude-big-hacks`
(19 hackeos, 4 796 nodos, 48 408 aristas) que produce un problema
más equilibrado y tractable para CPU. Los dos regímenes se reportan
por separado.

### 9.1.3 Elliptic++ (Elmougy y Liu 2023)

**Elliptic++** es la extensión a nivel de wallet del *dataset* Elliptic
original (Weber et al. 2019) sobre Bitcoin. Contiene 822 942 wallets
etiquetadas como *lícito* / *ilícito* / *desconocido*, con features
propagadas del grafo de transacciones original. Este trabajo utiliza
un subconjunto de 50 000 wallets etiquetadas (excluyendo *desconocido*)
para la validación auxiliar del clasificador GCN local. Aunque el
*ledger* subyacente es Bitcoin (no Ethereum), la evaluación funciona
como sanity check del clasificador aislado del pipeline específico
Ethereum.

**Qué entra al modelo GCN a partir del *dataset* propio**. La
pipeline `combine_runs` → `partial_visibility_split` →
`extract_features` produce, para cada corrida ejecutada, tres
vistas {G_A, G_B, G_C} del *dataset* combinado. Para cada
vista, el clasificador GCN local recibe: (i) la matriz de features
X ∈ ℝ^|V_i| × 19 (los 19 features
descritos en §6.3 computados sobre el subgrafo G_i); (ii) las
aristas del subgrafo como *edge_index* de PyTorch Geometric;
(iii) el vector de etiquetas binarias y_v ∈ {0, 1} sólo para
las direcciones asignadas al exchange X_i (las contrapartes
compartidas aparecen en X pero con etiqueta *unknown*, enmascarada
en la loss). El GCN produce como salida un vector de probabilidades
p(v) ∈ [0, 1]^|V_i| por dirección visible; el top-K por
probabilidad descendente (K = 60 por defecto) se propaga al
coordinador cross-exchange de la capa 2 con las 19 features en
crudo como *fingerprint* per-address. **La capa 2 nunca recibe
grafos crudos**: el input al coordinador LLM es exclusivamente la
lista de direcciones flageadas + sus fingerprints en representación
textual.

### 9.1.4 OpenAML v1 (proyecto FINOS/DTCC 2025)

**OpenAML v1** es el marco open-source más reciente sobre Ethereum,
mantenido por FINOS (Fintech Open Source Foundation, Linux Foundation)
tras nacer como proyecto del *DTCC AI Hackathon* en la Duke University.
Se usa el archivo `training_data.csv` distribuido con la versión 1 del
repositorio, con 34 000 wallets etiquetadas y 16 features por wallet
(in-degree, out-degree, ETH recibido, USDT recibido, número de
contrapartes únicas, entre otras). Este *dataset* es el más cercano en
formato al output del pipeline propio y se usa para la triple-validación
cruzada.

**Nota sobre StableAML v2**. La configuración inicial contemplaba el
uso del dataset ampliado "StableAML v2" mencionado en la documentación
del proyecto OpenAML como *dataset* principal externo, pero el CSV
correspondiente no está distribuido en el repositorio público (*issues*
abiertas hace 11 meses sin respuesta al momento de la redacción). La
estrategia final locked el 2026-07-18 sustituye StableAML v2 por la
combinación Elliptic++ + OpenAML v1 + simulación propia, que provee
triple validación con provenance verificable.

## 9.2 Particionado federado en exchanges

Los cuatro *datasets* se someten al mismo particionado
`partial_visibility_split` (definido en `src/aml/detectors/dataset.py`,
línea 200-380) con parámetros fijos:

- **Número de exchanges**: n=3.
- **Nombres**: `exchange_A`, `exchange_B`, `exchange_C`.
- **Semilla**: 42 (determinista y reportada explícitamente en todas las
  ejecuciones).
- **Distribución**: hash-based, aproximadamente uniforme
  (± 3 % entre exchanges).

El algoritmo asigna cada dirección v ∈ V a exactamente un
exchange mediante `hash(str(v)) % 3` con la semilla determinada, y
construye para cada X_i el subgrafo inducido por las aristas
incidentes en direcciones de X_i. Las etiquetas KYC (y_v) sólo son
accesibles al entrenamiento del clasificador local f_i si
v ∈ X_i; para el resto de direcciones que aparecen en el subgrafo
de X_i como contrapartes, la etiqueta es *unknown*.

La elección de n=3 balancea dos consideraciones: (i) n=1 colapsa
al escenario centralizado no federado; (ii) n ≥ 10 produce
subgrafos demasiado ralos para entrenar el GCN local. Con n=3 cada
exchange observa aproximadamente el 33 % de las direcciones y ~55 %
de las aristas (las aristas con ambos endpoints en X_i más las que
cruzan hacia otras plataformas).

## 9.3 Configuración de detectores

Se comparan cuatro detectores.

### 9.3.1 Louvain (baseline no supervisado)

Implementado en `src/aml/detectors/baselines.py` mediante
`networkx.algorithms.community.louvain_communities`. Sin entrenamiento
supervisado: predice como *ilícita* toda dirección que caiga en una
comunidad cuyo tamaño exceda el percentil 90 de la distribución de
comunidades por exchange. Se incluye como *baseline* de referencia
histórica —Louvain era el algoritmo dominante en detección AML
pre-2020— y como estrawman para justificar el coste computacional
del GCN.

### 9.3.2 GCN (Kipf y Welling 2017)

Clasificador de convolución sobre grafos implementado en
`src/aml/detectors/gnn.py` (359 líneas) con PyTorch Geometric.
Configuración por defecto (dataclass `GCNConfig`, líneas 213-220):

- **Arquitectura**: dos capas `GCNConv` con 32 unidades ocultas y
  activación ReLU intermedia, capa lineal final a un logit.
- **Features de nodo** (d=19, definidas en
  `src/aml/detectors/gnn.py`, `FEATURE_NAMES`):
  (i) *degree features* (3): `in_degree`, `out_degree`, `total_degree`;
  (ii) *flujos de valor* log-transformados (4): `log_eth_in`,
  `log_eth_out`, `log_usdt_in`, `log_usdt_out`;
  (iii) *diversidad de contrapartes* log-transformadas (2):
  `log_unique_in`, `log_unique_out`;
  (iv) *conteo por tipo de arista* (10 = 5 tipos × 2 direcciones):
  `transfer_eth_{in,out}`, `transfer_usdt_{in,out}`, `swap_{in,out}`,
  `mixer_deposit_{in,out}`, `mixer_withdraw_{in,out}`. La transformación
  logarítmica en (ii) y (iii) comprime distribuciones que pueden
  abarcar varios órdenes de magnitud entre nodos benignos aislados y
  nodos hub de laundering. La justificación semántica del diseño se detalla en §6.3.
- **Dropout**: 0,1 tras cada capa oculta.
- **Optimizador**: Adam con *learning rate* 10⁻² (0,01), *weight
  decay* 5 · 10⁻⁴.
- **Épocas**: 100 por defecto en la *dataclass*; los *scripts* de
  evaluación pasan 50-100 según el *dataset* (validado empíricamente
  como suficiente para convergencia sin sobreajuste).
- **Loss**: `binary_cross_entropy_with_logits` con
  `pos_weight = n_negatives / n_positives` para compensar el
  desbalance de clase.
- **Semilla**: 42 (afecta inicialización de pesos y orden de mini-
  batches).

Se instancia una copia independiente del clasificador por exchange:
f_A, f_B, f_C no comparten pesos y se entrenan por separado
sobre sus vistas parciales.

### 9.3.3 MultiAgent baseline (similaridad coseno)

Implementado en `src/aml/detectors/multi_agent.py` como
`MultiAgentDetector` (línea 124). La clase encapsula la capa 2 del
defensor con una implementación no-LLM: recibe las direcciones
flageadas por cada *exchange*, extrae el vector de *features* de
19 dimensiones por dirección y agrupa dos direcciones v, w en
el mismo *cluster* si cos(x_v, x_w) ≥ 0,95
(constante `similarity_threshold`, línea 144). Es el *strawman*
comparativo obligatorio para el detector LLM.

### 9.3.4 MultiAgent LLM (coordinador Claude)

`LLMDefenderCoordinator` (`src/aml/detectors/multi_agent.py`,
línea 553; 780 líneas totales del módulo). Sustituye la capa 2
por una llamada estructurada a un LLM. Parámetros *locked*:

- **`top_k_flagged_per_exchange = 60`**: número máximo de direcciones
  flageadas que se envían al LLM por cada exchange. Selección por
  probabilidad descendente del clasificador local.
- **`llm_max_tokens = 8192`**: cap de output para permitir
  clusterings verbosos.
- **`fallback_similarity_threshold = 0.95`**: umbral de similaridad
  coseno aplicado si el parser JSON falla (idéntico al baseline).

Los modelos evaluados son:

- **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`): opción bulk
  económica, ~0,25/1,25 USD por millón de tokens
  input/output.
- **Claude Sonnet 4.6**: opción headline balanced,
  ~3/15 USD por millón.
- **Claude Opus 4.7** (`claude-opus-4-7`): opción qualitative demo,
  ~15/75 USD por millón.

Los tres modelos se ejecutan sobre exactamente el mismo prompt
(`_LLM_COORDINATOR_SYSTEM_PROMPT` en `multi_agent.py`) para permitir
comparativa directa modelo-a-modelo.

## 9.4 Métricas

### 9.4.1 Detección binaria local

- **F1** = harmonic mean de precisión y recall sobre la etiqueta
  binaria ŷ_v ∈ {0, 1} agregada a través de los tres
  exchanges. Métrica primaria de la capa 1 del defensor.

### 9.4.2 Atribución de actor cluster cross-exchange

- **Adjusted Rand Index (ARI)** (Hubert y Arabie 1985): mide el
  acuerdo entre el clustering predicho Ĉ y el clustering
  verdadero C*, corregido por acuerdo esperado bajo azar.
  Rango [-1, 1]; 0 corresponde a agrupación aleatoria, 1 a
  identidad. Métrica primaria de la capa 2 del defensor.
- **Homogeneidad** (Rosenberg y Hirschberg 2007): fracción de
  clusters ĉ ∈ Ĉ tales que todas las direcciones de
  ĉ pertenecen al mismo cluster real. Alta homogeneidad indica
  pureza de los clusters predichos.
- **Completitud**: fracción de clusters reales c ∈ C* tales
  que todas las direcciones de c caen en un único cluster predicho.
  Alta completitud indica ausencia de fragmentación de actores reales.

### 9.4.3 Métricas de coste

Para los detectores LLM se reporta adicionalmente:

- **Coste en USD**: suma de `input_tokens × precio_input +
  output_tokens × precio_output` sobre todas las llamadas de la
  evaluación. Devuelto directamente por
  `aml.attackers.llm_client.LLMClient.complete()`.
- **Tokens totales**: separados en `input_tokens` (contexto + prompt)
  y `output_tokens` (respuesta del LLM).
- **Tiempo wall-clock**: latencia total de la evaluación.

## 9.5 Configuración LLM

### 9.5.1 Prompts

Los prompts del coordinador defensor están calibrados para producir
entre 10 y 25 clusters (rango realista para las 20 campañas
atacantes esperadas más *outliers* benignos). El *system prompt*
enfatiza:

1. Nombrado de clusters con arquetipos AML reconocibles
   (*cross-exchange mixer hub*, *pass-through mixer relay*,
   *pure mixer depositor*, *fan-out distributor*,
   *consolidation sink*, *peeling chain*).
2. Formato de output JSON compacto
   `{"cluster_id": ["addr1", "addr2", ...]}`.
3. Restricción explícita de usar únicamente las direcciones
   proporcionadas en el input (evita hallucinations).
4. Sección de reasoning textual que explique cada cluster.

El *user prompt* concatena las tres vistas locales como bloques
independientes, cada uno con la lista completa de direcciones
flageadas del exchange y sus features (una línea por dirección con
formato `0x<full_42_char_address>: {feature_summary}`). Las
direcciones se envían **sin truncar** —una versión temprana que
enviaba direcciones abreviadas causó que el LLM devolviese versiones
truncadas incompatibles con el parser.

### 9.5.2 Parser tolerante

El parser `_parse_llm_clusters` (línea 490 de `multi_agent.py`) acepta
dos formatos alternativos:

1. **Compacto** (formato preferido): `{cluster_id: [addresses]}`.
2. **Verbose**: `[{cluster_id, addresses, reasoning}]` (formato
   histórico de las primeras iteraciones, soportado por
   backward-compat).

Si el LLM devuelve un JSON malformado o asigna direcciones no
presentes en el input, el parser aplica el *fallback* de similaridad
coseno con umbral 0,95 (idéntico al baseline). El campo
`used_fallback` del *result* se reporta explícitamente en cada
ejecución.

## 9.6 Configuración final del atacante (constantes locked)

Los resultados canónicos del Capítulo 8 (§8.9.5, seeds 400 y 403) se
producen con los siguientes valores fijos, congelados tras la
iteración de refinamiento documentada en §8.9.5:

| Constante                             | Valor                                     | Localización                                             |
|---------------------------------------|-------------------------------------------|----------------------------------------------------------|
| `_DEFAULT_GAS_RESERVE_ETH`            | 0,005 ETH                             | `attackers/tools.py`                                     |
| `_pick_funder threshold`              | 0,01 ETH                              | `attackers/tools.py`                                     |
| `_pick_funder refill target`          | 0,025 ETH                             | `attackers/tools.py`                                     |
| `MAX_PER_FUNDER_ETH`                  | 1,0 ETH                               | `attackers/funder_sizing.py`                             |
| `MIN_PER_FUNDER_ETH`                  | 0,02 ETH                              | `attackers/funder_sizing.py`                             |
| `POOL_PCT_OF_AMOUNT`                  | 0,05 (5 % del stolen)             | `attackers/funder_sizing.py`                             |
| `POOL_BOOTSTRAP_ETH_WEI`              | 5 000 ETH (mock pool AMM)              | `chains/eth_stack.py`                                    |
| `POOL_BOOTSTRAP_USDT_BASE`            | 10⁷ USDT (mock pool AMM)               | `chains/eth_stack.py`                                    |
| Cap dinámico *burners*                | max(30, min(250, 3·⌈USD/999⌉))               | `attackers/tools.py::_burner_cap`                    |
| Deployer address (guardarraíl)        | `0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4` | `attackers/tools.py::_deployer_addr`               |
| Umbral sub-Travel-Rule *structuring*  | < 999 USD por *clean exit*                   | *prompt* `INTEGRATION_SYSTEM`                        |
| Precio ETH/USDT fecha campaña         | Congelado por `resolve_campaign_ts`       | `env/market_context.py`                                  |

La derivación empírica de estos valores se detalla en §8.9.5. La
motivación de tres de las constantes merece resaltarse:

- **Cap dinámico de burners** (max(30, min(250, 3·⌈USD/999⌉))). La fórmula garantiza al menos 30
  burners (flexibilidad táctica en campañas pequeñas), no más de 250
  (evita bucles patológicos observados en seed 306 con 236 burners
  para 1 ETH), y crece proporcional al volumen esperado bajo el umbral *structuring* de 999 USD por *clean exit*
  (sub-Travel-Rule crypto: FATF R.16 establece el umbral en
  1 000 USD para transferencias de criptoactivos, más restrictivo
  que el CTR de 10 000 USD del BSA estadounidense).
- **Pool operativo del 5 %** del *amount* laundered. Alinea con el
  perfil *moderate professional* de Chainalysis 2023 (250-750 USD
  por wallet en promedio); una fracción mayor produce campañas
  computacionalmente costosas y forense-obvias, una menor deja la
  campaña sin capital para consumir gas en múltiples hops.
- **Deployer guardrail**. Excluir la dirección deployer del *pool*
  operativo previene la contaminación del denominador de las métricas
  de recuperación (versión pre-fix: 198 % nominal por incluir el
  balance del deployer al final).

---

## 5.E Planificación del proyecto y metodología de trabajo

### 5.E.1 Metodología aplicada

El desarrollo del TFM siguió una **metodología iterativa incremental**
adaptada al perfil experimental del proyecto. Cada iteración cierra
con validación empírica en Anvil (sandbox local, cost cero) antes de
promoción a Sepolia (testnet pública, cost real ETH testnet). Este
patrón Anvil-dev → Sepolia-prod permite iteración rápida en la fase
de exploración y garantiza validez externa en la fase final.

Las cinco fases del proyecto se ejecutaron secuencialmente con
solapamiento parcial en las fases 2-3 (implementación atacante) y
4-5 (implementación defensor). Los hitos entregables (contratos
verificados, datasets consolidados, ablations documentadas)
delimitan cada fase.

### 5.E.2 Fases del proyecto y recursos

| Fase | Descripción | Duración estimada | Duración real | Recursos empleados |
|------|-------------|------------------:|--------------:|--------------------|
| **F1 — Análisis y diseño** | Revisión bibliográfica, taxonomía FATF, diseño arquitectura dual multi-agente | 3 semanas | 3 semanas | 45 h autor + 5 h tutor |
| **F2 — Simulador ofensivo** | Contratos Solidity, herramientas on-chain, agente coordinador atacante | 5 semanas | 6 semanas | 90 h autor + $8 USD LLM Opus |
| **F3 — Validación Anvil-dev** | 15 campañas Anvil (seeds 500-606) con iteraciones P1-XX bug fixes y refinamientos | 3 semanas | 4 semanas | 60 h autor + $6 USD LLM |
| **F4 — Despliegue Sepolia** | 6 contratos verificados en Etherscan + 8 campañas oficiales (seeds 800-830) | 3 semanas | 3 semanas | 45 h autor + $9 USD LLM + 8 ETH testnet |
| **F5 — Defensor + ablations** | Pipeline defensivo Louvain + LLM + ablations (P1-55/56/70/71/72/73/74) + cross-domain + held-out + LOCO | 4 semanas | 4 semanas | 60 h autor + $2 USD LLM Haiku |
| **F6 — Redacción memoria + preparación defensa** | Draft TFM + docx + defensa | 3 semanas | 2 semanas | 45 h autor |
| **TOTAL** | | **21 semanas** | **22 semanas** | **~345 h autor + $25 USD LLM** |

### 5.E.3 Diagrama Gantt textual

```
Semana         1   2   3   4   5   6   7   8   9   10  11  12  13  14  15  16  17  18  19  20  21  22
F1 Análisis    ████████████                                                                        
F2 Atacante             ████████████████████████                                                   
F3 Anvil dev                            ████████████████████                                       
F4 Sepolia                                          ████████████                                   
F5 Defensor                                                      ████████████████                   
F6 Redacción                                                                     ████████████     
```

### 5.E.4 Recursos y hardware empleados

**Recursos humanos**: 1 estudiante autor (Saleh Sinawi), dedicación
estimada 180 h presenciales + no-presenciales conforme al plan de
estudios UC3M (6 ECTS × 30 h/ECTS = 180 h). Real: ~345 h por el
alcance experimental extendido — el excedente sobre lo previsto es
parte del aprendizaje personal del autor y no se contabiliza en el
presupuesto oficial.

**Recursos técnicos**:

- Portátil de desarrollo: Intel Core i7-11800H, 32 GB RAM DDR4,
  NVIDIA RTX 3060 6 GB, WSL2 Ubuntu 22.04.
- Foundry (Anvil + Forge) para sandbox on-chain local.
- Sepolia testnet (Ethereum). Wallet deployer:
  `0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4`.
- API Anthropic Claude (Opus 4.7 atacante, Sonnet 4.6 defensor
  headline, Haiku 4.5 defensor bulk).
- CoinGecko API (precios ETH/USDT diarios en cache).
- Alchemy + Infura RPC providers (Sepolia).
- GitHub (control de versiones + repo público MIT).

### 5.E.5 Plan de gestión de riesgos

| Riesgo | Probabilidad | Impacto | Mitigación aplicada |
|--------|:------------:|:-------:|---------------------|
| LLM API rate-limit o timeout durante campaña larga | Media | Alto | P1-67 timeout+retry en llm_client.py; máx 300s read + 5 retries |
| Sepolia faucet drain / no fondos ETH testnet | Media | Medio | Migración a Anvil para escenarios de scale; Sepolia sólo para validación externa |
| Bug crítico en contratos con ETH bloqueado | Baja | Alto | 47 tests unitarios de contratos + mixer_recover.py como red de rescate |
| LLM produce campañas no realistas | Media | Medio | P1-42/43/44 iteraciones de refinamiento con métricas F1 vs. baseline |
| Overfitting del defensor a seeds de dev | Media | Alto | LOCO-CV (§8.10) + held-out validation seeds 900/901 (§8.9.J) |
| Budget LLM excedido | Baja | Medio | Pivote a Haiku 4.5 en fase F5; cost tracking en cada eval |

## 5.F Presupuesto del proyecto

### 5.F.1 Coste de personal

Considerando la dedicación real del autor (~345 h) y usando la tarifa
horaria estándar para un ingeniero informático junior en España
(sueldo bruto anual medio ~28 000 EUR + 30 % costes sociales ~= 21 EUR/h
sobre 1 750 h anuales efectivas):

| Concepto | Horas | Tarifa | Coste |
|----------|------:|-------:|------:|
| Autor (estudiante ingeniero jr.) | 345 h | 21 EUR/h | **7 245 EUR** |
| Tutor UC3M (co-supervisión) | 5 h | 65 EUR/h | 325 EUR |
| **Subtotal personal** |  |  | **7 570 EUR** |

### 5.F.2 Coste de hardware (amortización)

Amortización lineal a 5 años sobre el periodo de 5 meses del TFM:

| Recurso | Precio | Uso | Amortización |
|---------|-------:|-----|-------------:|
| Portátil Intel i7-11800H 32 GB RTX 3060 | 1 500 EUR | 5 meses / 60 | **125 EUR** |
| Monitor externo 27" | 250 EUR | 5 meses / 60 | 21 EUR |
| **Subtotal hardware** |  |  | **146 EUR** |

### 5.F.3 Coste de servicios cloud y APIs

| Servicio | Uso | Coste real |
|----------|-----|-----------:|
| Anthropic API (Opus 4.7 atacante, ~13 campañas oficiales) | 26 campañas × ~$1 USD | 25 USD |
| Anthropic API (defensor Haiku + Sonnet ablations) | 15 evals × ~$0.15 USD | ~2 USD |
| CoinGecko API (free tier) | 5 meses | 0 USD |
| Alchemy / Infura RPC (free tier Sepolia) | 5 meses | 0 USD |
| GitHub (repo público) | 5 meses | 0 USD |
| **Subtotal servicios** (~$27 USD ≈ 25 EUR al cambio) |  | **25 EUR** |

### 5.F.4 Coste de electricidad y overhead

| Concepto | Cálculo | Coste |
|----------|---------|------:|
| Electricidad (300W × 345h × 0.20 EUR/kWh) | 20.7 kWh × 0.20 | **21 EUR** |
| Conectividad internet (5 meses × 40 EUR) | 200 EUR × 30% imputable | 60 EUR |
| **Subtotal overhead** |  | **81 EUR** |

### 5.F.5 Presupuesto total

| Concepto | Coste (EUR) |
|----------|------------:|
| Personal | 7 570 |
| Hardware (amortización) | 146 |
| Servicios cloud + APIs | 25 |
| Electricidad + overhead | 81 |
| **Subtotal directo** | **7 822** |
| IVA (21 %) | 1 643 |
| **TOTAL con IVA** | **9 465 EUR** |

**Nota sobre coste real vs. presupuesto**: el coste directo de los
recursos técnicos (hardware amortizado + APIs + electricidad) fue de
**252 EUR** — una fracción muy pequeña del total. El grueso del coste
(7 570 EUR, 97 %) corresponde a las horas de trabajo del autor. En
un contexto académico donde este coste no se factura, el **presupuesto
efectivo desembolsado** para completar el TFM fue de aproximadamente
**252 EUR**, lo cual constituye uno de los findings publishable del
proyecto: un pipeline AML multi-agente reproducible con budget
< 300 EUR es viable en 2026 gracias a la disponibilidad de LLMs
frontera a coste marginal.


