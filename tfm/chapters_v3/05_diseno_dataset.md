# Capítulo 4 — Diseño, dataset y lenguajes

Este capítulo reúne el diseño arquitectónico del sistema, los
lenguajes empleados en la implementación, y la especificación de
datasets y parámetros. Se divide en cuatro bloques principales:

- **§4.A Diseño y diagramas** — vista general del sistema, arquitecturas atacante y defensor, flujo end-to-end.
- **§4.B Arquitectura de software** — capas de contratos, blockchain, herramientas y coordinación multi-agente.
- **§4.C Lenguajes empleados** — Python, Solidity, Circom, JavaScript, Bash, Markdown con LOC por lenguaje.
- **§4.D Datasets y parámetros** — datasets propios y externos, particionado federado, configuración de detectores y LLMs.

---

## 4.0 Novedad central del TFM — visibilidad parcial federada por exchange

Antes de exponer el diseño técnico se articula, en una única sección
compacta, la novedad que este trabajo aporta al estado del arte
descrito en el Capítulo 2 y a las limitaciones documentadas en el
Capítulo 3.

### 4.0.1 El problema que nadie ha modelado

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

### 4.0.2 Propuesta: federación cross-exchange con fingerprints

La arquitectura propuesta modela explícitamente n exchanges
federados donde:

1. Cada exchange E_i observa **únicamente su subgrafo local**
   `G_i = (V_i, E_i)` obtenido por *hashing determinista* de las
   direcciones sobre {1..n} (§4.A.4).
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

### 4.0.3 Por qué esto es publishable

Ningún trabajo previo del estado del arte cumple simultáneamente los
tres siguientes criterios:

| Criterio | Weber 2019 | Wu 2023 | Elmougy 2023 | Juvinski 2025 | **Este TFM** |
|----------|:---------:|:-------:|:------------:|:-------------:|:------------:|
| Modelado explícito de visibilidad parcial multi-exchange | ❌ | ❌ | ❌ | ❌ | **✓** |
| Coordinador cross-exchange que razona sin acceso a datos crudos | ❌ | ❌ | ❌ | ❌ | **✓** |
| Validación empírica bajo particionado federado n=3 | ❌ | ❌ | ❌ | ❌ | **✓** |

Los resultados empíricos (§5.9.K multi-campaign LOCO F1 = 0.939 con
34 578 nodos y 7 campañas simultáneas + §5.9 role attribution
ARI = 0.43 sobre 18 clusters role×campaign) demuestran que la
arquitectura propuesta **funciona operativamente** bajo las
condiciones adversariales del despliegue real: multiples campañas
concurrentes, visibilidad parcial cero cross-exchange, sin re-training
por dataset.

### 4.0.4 Diagrama de la arquitectura

![Figura 1. Arquitectura del sistema dual multi-agente: atacante LLM (Opus 4.7) ejecuta transacciones on-chain; los 3 exchanges federados observan sólo sus vistas locales KYC-verificadas; el coordinador LLM defensor (Haiku 4.5) razona sobre fingerprints agregados sin acceso a datos crudos.](tfm/figures/architecture.png)

## 4.A Vista general de la arquitectura

El sistema se organiza en cinco capas integradas verticalmente. En la
capa más profunda se despliegan los seis contratos Solidity que
materializan el ecosistema on-chain sobre el que operan atacante y
defensor. Sobre
ellos actúa la capa blockchain, formada por Anvil durante el desarrollo y
Sepolia durante la validación externa. La capa Python del proyecto se
sitúa por encima e integra el `ToolDispatcher` (que valida cada
transacción), los detectores (Louvain, GCN, LLM coordinator) y los
módulos de análisis. La capa de coordinación LLM contiene los agentes
Anthropic Claude (Opus 4.7 para el atacante; Sonnet 4.6 y Haiku 4.5 para
el defensor). Finalmente, la capa de exchanges federados agrupa las tres
vistas parciales sobre las que operan los detectores locales. La Figura 1
de §4.0.4 muestra estas cinco capas y su interacción.

El atacante multi-agente se compone de un coordinador Opus 4.7 que
planifica cada campaña siguiendo la taxonomía FATF [6] y cinco
sub-agentes especializados: `placement_agent` controla la fase inicial
(source wallet a burners), `mixer_agent` coordina depósitos y retiradas
del `MockTornado` con generación de pruebas Groth16 [23] off-chain via
snarkjs [30], `swap_agent` ejecuta operaciones sobre `MockUniswapV2Pool`
aplicando el slippage real de la curva x·y=k, `bridge_agent` simula
transferencias cross-chain vía `MockBridge` con log estructurado, e
`integration_agent` distribuye los fondos finales a las direcciones
`clean_exit` mediante structuring por debajo del umbral CTR. Todos
comparten el `ToolDispatcher` que enforce las cinco invariants
estructurales (balance-preserving, gas-sovereign, deterministic-seeding,
fail-safe, auditable) documentadas en §6.A.

El defensor tiene arquitectura simétrica. En Phase 1, cada exchange
observa únicamente su subgrafo local y entrena un clasificador Louvain
[10] (o GCN [9], según configuración) que produce `predict_proba(addr) →
[0,1]` para cada dirección observable. En Phase 2, un coordinador Haiku
4.5 recibe los `top-K` flagged addresses de cada exchange con sus
fingerprints agregados de 19 dimensiones y propone actor clusters
cross-exchange con razonamiento textual auditable, alineado con FATF
Recomendación 20. El post-procesamiento P1-71 (§5.9.G) mergea los
clusters LLM por distancia L2 de centroides hasta `max_clusters`, y P1-73
(§5.9.H) elige `max_clusters` automáticamente por silhouette score sin
requerir ground truth.

El flujo end-to-end de una campaña sigue las cinco fases mostradas en la
Figura 2 de §6.0. Alice roba fondos del pool DEX vulnerable (placement);
el atacante los procesa a través del mixer ZK, swaps y burners chain
(layering); distribuye los fondos limpios mediante structuring sub-CTR a
direcciones `clean_exit` (integration); los tres exchanges observan
sus vistas parciales y Louvain flagea localmente (detection); y el LLM
cluster cross-exchange agrupa las direcciones flageadas con razonamiento
textual (clustering). Los datos generados quedan persistidos en
`chain_trace.jsonl`, `addresses.json` y `campaign.json` como artefactos
verificables e independientemente reproducibles.

La federación se implementa a través de `partial_visibility_split(combined,
num_exchanges=3, seed=42)`. Esta función reparte las direcciones
observables entre los exchanges mediante hashing determinista
`hash(address) % num_exchanges`. Cada exchange observa sólo aquellas
aristas donde AMBOS extremos son direcciones asignadas a él,
garantizando la restricción realista de que un exchange no puede observar
transacciones que no involucran a sus usuarios KYC. La determinismo del
hash asegura que el mismo seed reproduce exactamente el mismo
particionado entre runs, propiedad esencial para la reproducibilidad de
las tablas del Capítulo 6.

El stack ZK se apoya en un circuit Circom 2.0 (≈60 líneas) que implementa
la verificación Merkle-tree sobre el commitment `(secret, nullifier)` del
depósito, con hasher MiMCSponge de circomlib. La compilación produce el
R1CS que snarkjs procesa vía `groth16 setup` con la ceremony pública
Powers of Tau Hermez [ptau12] hasta generar `verification_key.json` y el
contrato Solidity `Verifier`. La prueba se genera off-chain en Node.js
(≈200 líneas de puente) y se envía al método `mixer_withdraw` del
`MockTornado`, que verifica la validez del proof y libera los fondos si
el nullifier no ha sido usado antes.

## 4.B Arquitectura del software

La capa de contratos on-chain está compuesta por seis contratos Solidity
0.8.20 escritos a mano. Se opta por no importar OpenZeppelin ni Uniswap
para minimizar la superficie adversarial y mantener control explícito
sobre las invariants del ecosistema. `MockUSDT` implementa un ERC-20
minimal con `mint()` público y `decimals=6` para emular las
características de USDT real. `MockUniswapV2Pool` es un pool ETH/USDT
constant-product (x·y=k) con `swap`, `addLiquidity` y `removeLiquidity`
que aplica slippage real basado en el balance del pool. `MockTornado` es
el mezclador estilo Tornado Cash [20] con soporte multi-denominación
(0.1, 1 y 10 ETH) y verificación Groth16. `MockBridge` emite eventos
`BridgeInitiated(dst_chain, amount)` para simular cross-chain sin destino
real. `MiMCSponge` es el hasher on-chain requerido por el circuit ZK, y
`Verifier` es el contrato Groth16 generado automáticamente por snarkjs.
Los seis contratos están verificados en Sepolia Etherscan; sus
direcciones y hashes de despliegue quedan registrados en
`deployments/sepolia.json`.

La capa blockchain utiliza Foundry como toolchain unificado. Anvil
proporciona un sandbox EVM local con startup de aproximadamente dos
segundos, ETH ilimitado y capacidad de mine on demand, lo que resulta
esencial durante el desarrollo iterativo. Sepolia se emplea como testnet
pública para la validación externa; cada contrato desplegado se verifica
con `forge verify-contract` para que un auditor independiente pueda
inspeccionar el source code directamente en Etherscan.

El `ToolDispatcher` expone 32 herramientas al LLM atacante, organizadas
en cinco grupos funcionales: transferencias directas (4), swaps DEX (4),
mixer ZK (6), bridge cross-chain (2), gestión de wallets (8) y
coordinación/audit (8). Cada dispatch valida las cinco invariants
estructurales enumeradas en §6.A antes de emitir la transacción
correspondiente, lo que garantiza que ningún error del LLM puede corromper
el estado del sistema ni evadir la trazabilidad.

La orquestación multi-agente del atacante se estructura en torno al
objeto `Coordinator`. Recibe como entrada el escenario
(`defi-exploit`, `ransomware-cashout` o `stablecoin-scam`), el amount
target y el seed, y planifica la campaña como una secuencia de `Task`s
delegables. Cada `Task` se asigna a un sub-agente con contexto local; el
sub-agente usa la API de tool-use de Anthropic para invocar el
`ToolDispatcher`, y el coordinador supervisa el retorno y decide si
continuar, reintentar o abortar. El timeout total se fijó en seis horas
para Sepolia (limitado por el block time de la red pública) y treinta
minutos para Anvil.

El defensor `LLMDefenderCoordinator` replica esta estructura. Recibe la
lista de vistas por exchange (`views: list[ExchangeView]`) y las
etiquetas de entrenamiento (`train_labels: dict[str, int]`). En Phase 1,
`PerExchangeDetector.fit_per_view` entrena un Louvain independiente por
cada `ExchangeView`. En Phase 2, `_build_llm_user_prompt` construye el
prompt con los top-K flagged addresses de los tres exchanges,
`LLMClient.complete` ejecuta la llamada a Haiku 4.5, y
`_parse_llm_clusters` extrae el diccionario `actor_clusters` del output
del LLM. Si el parámetro `max_clusters` está definido,
`_merge_clusters_by_centroid` reduce el número de clusters
determinísticamente; si vale `"auto"`, `_auto_pick_max_clusters` selecciona
el mejor `max_clusters` por silhouette score.

Cada `ExchangeView` es un `nx.MultiDiGraph` que representa el subgrafo
observable por el exchange, junto con el conjunto de direcciones visibles
`visible_addresses`. El coordinador cross-exchange recibe únicamente los
fingerprints agregados de las direcciones flageadas —nunca los subgrafos
completos— lo que respeta la restricción de privacidad y mantiene el
tamaño del prompt LLM en aproximadamente veinte mil tokens, dentro de los
límites de contexto de Haiku 4.5.

Todo el código está publicado bajo licencia MIT en el repositorio
[`github.com/0xAnonsal/aml-thesis`](https://github.com/0xAnonsal/aml-thesis).
Para cada tabla y figura del Capítulo 6 existe un comando de reproducción
exacto que un tercero puede ejecutar sin acceso a nuestros artefactos
privados. Los prompts completos residen en `src/aml/attackers/prompts.py`
para el atacante y en la constante
`_LLM_COORDINATOR_SYSTEM_PROMPT` de `src/aml/detectors/multi_agent.py`
para el defensor. Los contratos verificados en Sepolia son inspectables
por hash en Etherscan, y los cuarenta y siete tests unitarios de `tests/`
cubren tanto la capa Solidity como el pipeline Python.

## 4.C Lenguajes de programación

El TFM combina seis lenguajes según sus fortalezas específicas, con un
total aproximado de diez mil líneas de código propio. Python 3.11
concentra el noventa por ciento del volumen, con unas nueve mil líneas
distribuidas entre el simulador atacante, los detectores, el pipeline de
evaluación, los agentes LLM y el análisis. Elegimos Python por la
madurez de su stack para grafos, machine learning y clientes LLM;
PyTorch Geometric [29], NetworkX [28] y scikit-learn cubren de forma
directa todas las necesidades del proyecto.

Solidity 0.8.20 aporta las aproximadamente ochocientas líneas de los seis
contratos on-chain (`MockUSDT`, `MockUniswapV2Pool`, `MockTornado`,
`MockBridge`, `MiMCSponge`, `Verifier`), compilados con Foundry [26]. El
lenguaje estándar para smart contracts EVM y el soporte nativo del
compilador `solc` para overflow checks lo hacen la elección obligada
sobre alternativas más experimentales como Vyper. Circom 2.0 contribuye
las sesenta líneas del circuit ZK del mezclador, sobre la implementación
de referencia de Groth16 provista por snarkjs [30].

Los tres lenguajes restantes juegan roles auxiliares. JavaScript (Node.js
LTS) proporciona doscientas líneas de puente off-chain que generan las
pruebas Groth16 con snarkjs y las empaquetan para el `mixer_withdraw`.
Bash aporta cien líneas de scripts de setup, orquestación de campañas
paralelas y despliegue Sepolia. Markdown constituye las cuatro mil líneas
de documentación del proyecto más el propio draft del TFM en
`chapters_v3/*.md`, versionado en Git y convertible a docx mediante el
script `scratchpad/md_to_docx.py`.

Se descartaron Rust por la lentitud del ciclo de compilación y la menor
madurez de su ecosistema para grafos y aprendizaje automático; Vyper por
su soporte menos completo que Solidity para el ecosistema DeFi; y Halo2
como sistema ZK alternativo a Groth16 por presentar una curva de
aprendizaje mayor sin aportar beneficio funcional al alcance del TFM.


## 4.D Datasets y parámetros


Este capítulo describe el diseño experimental que produce los resultados
del Capítulo 5. Se detallan: (i) los cuatro *datasets* utilizados y su
provenance; (ii) el particionado federado en n=3 exchanges; (iii) la
configuración exacta de cada detector; (iv) la estrategia de
*cross-validation* diseñada para detectar y prevenir memorización;
(v) las métricas de evaluación; (vi) la parametrización de los
componentes LLM; (vii) las semillas de reproducibilidad;
(viii) las constantes finales *locked* del atacante refinado; y (ix)
el protocolo de validación externa sobre la testnet Sepolia.
El objetivo es que un lector independiente pueda regenerar cualquier
tabla o figura del Capítulo 5 con un solo comando desde el repositorio
del proyecto.

### 4.D.1 Datasets

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
combinada. Los experimentos del Capítulo 5 reportan tanto la
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

### 4.D.2 Particionado federado en exchanges

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

### 4.D.3 Configuración de detectores

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

### 4.D.4 Métricas

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

### 4.D.5 Configuración LLM

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

### 4.D.6 Configuración final del atacante (constantes locked)

Los resultados canónicos del Capítulo 5 (§5.9, seeds 400 y 403) se
producen con los siguientes valores fijos, congelados tras la
iteración de refinamiento documentada en §5.9:

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

La derivación empírica de estos valores se detalla en §5.9. La
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

## 4.E Planificación del proyecto y metodología de trabajo

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
| Overfitting del defensor a seeds de dev | Media | Alto | LOCO-CV (§5.10) + held-out validation seeds 900/901 (§5.9.J) |
| Budget LLM excedido | Baja | Medio | Pivote a Haiku 4.5 en fase F5; cost tracking en cada eval |

## 4.F Presupuesto del proyecto

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

El coste real de la API de Anthropic durante los cinco meses del TFM se
detalla en la tabla siguiente, con las cifras extraídas directamente del
historial de facturación de la cuenta.

| Fecha | Concepto | Coste |
|-------|----------|------:|
| May 10, 2026 | Credit grant Anthropic | 6.05 USD |
| Jun 11, 2026 | Credit grant Anthropic | 24.20 USD |
| Jul 5, 2026 | Credit grant Anthropic | 12.10 USD |
| Aug 12, 2026 | Credit grant Anthropic | 24.20 USD |
| Aug 15, 2026 | Credit grant Anthropic | 24.20 USD |
| Aug 26, 2026 | Credit grant Anthropic | 24.20 USD |
| Sep 3, 2026 | Credit grant Anthropic | 36.30 USD |
| Sep 9, 2026 | Credit grant Anthropic | 36.30 USD |
| **Subtotal Anthropic pagado** | | **187.55 USD** |
| Bonos y créditos gratuitos consumidos | Anthropic promo | ~50-100 USD |
| **Total API consumido** | | **~240-290 USD** |

El desglose por uso aproximado es: Opus 4.7 en las veintiséis campañas
del atacante consume aproximadamente el 70 % del gasto; Sonnet 4.6 en la
ablation §5.9.I y en las evaluaciones headline representa
aproximadamente el 20 %; Haiku 4.5 en las evaluaciones bulk del defensor
representa el 10 % restante.

| Servicio adicional | Uso | Coste |
|--------------------|-----|------:|
| CoinGecko API (tier gratuito) | 5 meses | 0 EUR |
| Alchemy / Infura RPC (tier gratuito Sepolia) | 5 meses | 0 EUR |
| GitHub (repositorio público) | 5 meses | 0 EUR |
| Etherscan API (verificación source) | 6 contratos | 0 EUR |
| **Subtotal servicios adicionales** | | **0 EUR** |

**Subtotal cloud + APIs**: 187.55 USD desembolsados directamente ≈
**172 EUR** al tipo de cambio ~0.92 EUR/USD del periodo. Incluyendo el
consumo de créditos promocionales de Anthropic, el consumo total en
llamadas LLM se estima en **~220-265 EUR** equivalentes.

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
| Servicios cloud + APIs (LLM Anthropic) | 172 |
| Electricidad + overhead | 81 |
| **Subtotal directo** | **7 969** |
| IVA (21 %) | 1 674 |
| **TOTAL con IVA** | **9 643 EUR** |

**Nota sobre coste real desembolsado**: en un contexto académico las
horas de trabajo del autor no se facturan. El **presupuesto efectivo
realmente desembolsado** para completar el TFM asciende a los siguientes
conceptos: 187.55 USD en pagos directos a Anthropic (~172 EUR),
aproximadamente 146 EUR en amortización de hardware imputada al
proyecto, y unos 81 EUR de electricidad y overhead. En total,
aproximadamente **400 EUR desembolsados**. Si se contabilizan además
los créditos promocionales de Anthropic consumidos durante el periodo
(50-100 USD adicionales), el coste técnico total del pipeline sube
hasta unos **450-500 EUR**.

Este dato constituye uno de los findings publishable del proyecto: un
pipeline AML multi-agente completo, reproducible y verificable
on-chain, se puede desarrollar en 2026 con un budget técnico por debajo
de 500 EUR gracias a la disponibilidad de LLMs frontera a coste
marginal reducido y a la infraestructura open-source (Foundry, snarkjs,
PyTorch Geometric, NetworkX). El coste equivalente en 2023, con la
generación anterior de modelos frontera (GPT-4 a 30 USD por millón de
tokens de entrada), habría sido de al menos 10 000-15 000 EUR sólo en
llamadas LLM para el mismo volumen de campañas.


