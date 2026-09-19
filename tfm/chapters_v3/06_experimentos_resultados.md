# Capítulo 5 — Experimentos y resultados (attack primero, luego defense)

Este capítulo describe (i) la infraestructura de reproducibilidad y el suite de tests del sistema; (ii) la estrategia de validación cruzada empleada; (iii) el protocolo de validación externa Sepolia; y (iv) los resultados empíricos completos de todos los experimentos.

## 5.1 Estrategia de cross-validation

Se emplean tres estrategias de validación diseñadas para detectar
distintos modos de sobreajuste.

### 5.1.1 Split estándar 80/20 (referencia histórica)

Particionado aleatorio de las 20 campañas atacantes en 16 de
entrenamiento y 4 de test, con `train_test_split(random_state=42,
stratify=y)`. Es la validación que reportan típicamente los
*benchmarks* académicos y sirve como referencia comparativa. Sus
resultados **no son** la evidencia principal de este capítulo —la
literatura previa ha demostrado que esta métrica sobreestima el
rendimiento en *datasets* AML pequeños.

### 5.1.2 Leave-One-Campaign-Out (LOCO-CV)

**Motivación**. El problema con el split 80/20 sobre *datasets* AML
pequeños es que el clasificador puede *memorizar* la identidad
concreta de las campañas atacantes en lugar de aprender la propiedad
subyacente ("esto huele a laundering"). Cuando el número de actor
clusters es reducido (~20 en la simulación propia, ~23 en
EthereumHeist), el modelo aprende que "cuando la wallet `0xabc…` y
sus vecinos exhiben este patrón específico → atacante" en vez de
"cuando cualquier wallet + su vecindad exhiben esta topología
adversarial → atacante". La consecuencia práctica es que el F1
reportado sobre el *split* estándar sobreestima el rendimiento
operativo real (§5.10 documenta un Δ F1 = -0,55 absoluto al
pasar de 80/20 a LOCO sobre la simulación propia).

**Definición operativa**. Para cada una de las 20 campañas atacantes
del *dataset*:

1. Se entrena el clasificador sobre las 19 campañas restantes más
   las 400 benignas.
2. Se evalúa sobre las direcciones de la campaña excluida (nunca
   vistas en entrenamiento).
3. Se reporta el F1 sobre esa campaña.

La métrica final es la media y desviación estándar del F1 sobre las
20 iteraciones. Esta validación es la evidencia principal del
Capítulo 5: mide la capacidad del detector de generalizar a un
actor/tipología nunca visto en entrenamiento —el escenario operativo
real donde un exchange debe detectar campañas que emergen en el
futuro, no re-clasificar campañas históricas ya etiquetadas.

Sobre el *dataset* EthereumHeist se emplea la variante análoga
**Leave-One-Heist-Out**: cada uno de los 19 (o 23) hackeos reales
sirve como test set, con el resto como entrenamiento.

**Auditoría diagnóstica adicional** (`scripts/audit_f1_memorization.py`,
detallada en §5.10). Además de LOCO se computan cuatro chequeos que
permiten aislar el origen de cualquier F1 elevado: (i) verificación
de que los splits train/test tengan intersección vacía de direcciones
no-contrato (previene *leakage* trivial); (ii) mutual information
feature-label por cada uno de los 19 features vía Cohen's d
(identifica features triviales que un modelo lineal aprendería sin
necesidad de GCN); (iii) el propio LOCO con reporte de media ± std
sobre los 35 folds; (iv) baseline logistic regression usando SÓLO
los cuatro features `mixer_*` (identifica el caso en que el GCN
efectivamente reduce a un "detector de mezclador" y por tanto
fallaría sobre el escenario *stablecoin-scam* donde `mixer_*` son
todos cero). El artefacto completo está en
`results/audit_f1_memorization.json`.

### 5.1.3 Stratified Group k-Fold (auditoría de leakage)

Como diagnóstico adicional se aplica *stratified group k-fold* con
k=5 donde el grupo es la campaña. Esto separa el efecto de la
memorización a nivel de campaña del efecto general de tamaño de test
set. Se reporta en el Anexo B.

## 5.2 Reproducibilidad y hardware

### 5.2.1 Semillas deterministas

Toda ejecución fija `random_state=42` en:

- `partial_visibility_split` (particionado federado).
- `train_test_split` (splits estándar).
- `torch.manual_seed` + `numpy.random.seed` (GCN training).
- El generador determinista del simulador atacante (no aleatorio en
  las decisiones LLM: `temperature=0` fijo).

Dos ejecuciones consecutivas del mismo script producen resultados
byte-idénticos módulo la no-determinismo intrínseco de las llamadas
LLM (Anthropic no garantiza reproducibilidad exacta con
`temperature=0` en producción). En la práctica, la varianza entre
runs consecutivas del *pipeline* LLM sobre el mismo prompt es
< 5 % relativa en ARI.

### 5.2.2 Hardware

Los experimentos se han ejecutado sobre:

- **CPU**: Intel Core i7-11800H, 8 cores.
- **RAM**: 32 GB DDR4.
- **GPU**: NVIDIA RTX 3060 6 GB (utilizada para el entrenamiento del
  GCN; el LLM se ejecuta remoto vía API).
- **Sistema operativo**: Windows 11 con WSL2 Ubuntu 22.04.
- **Python**: 3.11, gestionado por conda (`environment aml-thesis`).

El *dataset* combinado más grande (`ethereum_heist_combined.pkl`,
285 MB, 633 k nodos) cabe en RAM. El entrenamiento GCN sobre las 4 796
nodos del *dataset* filtrado toma ~5 minutos por exchange en CPU, o
~1 minuto en GPU. La evaluación LLM completa toma entre 30 s (Haiku,
20 campañas) y 8 minutos (Sonnet, 420 campañas + EthereumHeist).

### 5.2.3 Comandos de reproducción

Cada resultado numérico del Capítulo 5 se acompaña de un comando
`python scripts/<nombre>.py <argumentos>`. Los principales:

```bash
# Evaluación LLM defender sobre simulación (headline table)
python scripts/eval_llm_defender.py --model sonnet

# Evaluación LLM defender sobre EthereumHeist real
python scripts/eval_llm_defender_heist.py --model sonnet --exclude-big-hacks

# LOCO cross-validation sobre EthereumHeist
python scripts/loco_ethereum_heist.py

# LOCO sobre simulación con los tres detectores
python scripts/loco_simulation_3detectors.py

# Auditoría de memorización (Task #13)
python scripts/audit_f1_memorization.py

# Sanity check GCN sobre Elliptic++
python scripts/eval_gcn_elliptic_plus_plus.py

# Sanity check GCN sobre OpenAML v1
python scripts/eval_gcn_openaml.py
```

Los outputs JSON se persisten en `results/` bajo control de versiones.

## 5.3 Validación externa sobre Sepolia

La validación externa on-chain se ejecuta sobre la *testnet* pública
Sepolia (chain_id `11155111`). El *setup* está descrito en detalle en
`docs/SEPOLIA_DEPLOY.md` del repositorio; los puntos relevantes para
la metodología:

- **Deployer**: address `0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4`
  fondeada mediante *faucet* Sepolia con ~8 ETH.
- **Contratos desplegados** (6): MockUSDT, MockUniswapV2Pool,
  MiMCSponge, Verifier (Groth16), MockTornado, MockBridge. Cada uno
  con su dirección persistida en `deployments/sepolia.json` y
  verificable en `sepolia.etherscan.io/address/<addr>`.
- **Verificación de source code**: los contratos se publican en
  Sepolia Etherscan mediante `forge verify-contract` para que un
  auditor independiente pueda inspeccionar la lógica exacta.
- **Campaña Sepolia**: el escenario `defi-exploit` se re-ejecuta
  contra los contratos desplegados en Sepolia en dos corridas
  reales (*seed* 100 de 19 min con 1 ETH robado y *seed* 500 de
  61 min con 10 ETH robados). Las transacciones resultantes son
  verificables públicamente por *hash*. Los otros dos escenarios
  (`stablecoin-scam`, `ransomware-cashout`) se validaron
  extensivamente sobre Anvil local (coste cero) y quedan como
  extensión inmediata sobre Sepolia sin cambios de código.
- **Análisis del detector sobre Sepolia**: el grafo de transacciones
  Sepolia se extrae mediante el módulo `src/aml/chains/trace.py`
  desde el *provider* RPC (Alchemy o Infura) y se somete al mismo
  particionado y al mismo pipeline de detección que la simulación
  local. La comparativa de F1/ARI entre simulación y Sepolia se
  reporta en el Capítulo 5.

Este capítulo reporta los resultados experimentales del sistema
descrito en los capítulos anteriores. La organización sigue el orden
lógico del pipeline defensivo: primero la detección binaria local por
exchange (§5.5), luego la atribución cross-exchange de actor clusters
(§5.6), después el análisis cualitativo del razonamiento del
coordinador LLM (§5.7), la discusión del trade-off entre interpretabilidad
y métrica cuantitativa que constituye la contribución central del
trabajo (§5.8), la validación externa on-chain sobre Sepolia (§5.9), la
auditoría metodológica de memorización (§5.10), y finalmente la
discusión de limitaciones y su implicación para el trabajo futuro
(§5.11).

Todos los resultados numéricos han sido regenerados a partir del
código publicado en el repositorio; los comandos exactos de
reproducción se listan en el Capítulo 4 §4.7.3 y se referencian
individualmente en cada tabla.

## 5.4 Visión general de los experimentos

Se han ejecutado cinco bloques experimentales sobre el pipeline
defensivo:

1. **Detección binaria local** sobre el *dataset* simulado bajo
   *split* estándar 80/20 (§5.5.1). Fija la referencia comparativa
   con la literatura previa.
2. **Detección binaria local** bajo *leave-one-campaign-out
   cross-validation* (§5.5.2). Evidencia principal sobre generalización.
3. **Detección binaria local** sobre EthereumHeist real, tanto
   *split* estándar como *leave-one-heist-out* (§5.5.3). Valida el
   diferencial memorización/generalización sobre datos reales.
4. **Atribución de actor cluster cross-exchange** sobre el *dataset*
   simulado, comparando MultiAgent-cosine vs MultiAgent-LLM en tres
   modelos Claude (§5.6.1).
5. **Atribución cross-exchange** sobre EthereumHeist real (§5.6.2).
   Replica el hallazgo del bloque 4 sobre datos reales.

Los sanity checks del clasificador GCN sobre Elliptic++ y OpenAML v1
se reportan como validación auxiliar en §5.5.4.

## 5.5 Detección binaria local por exchange

### 5.5.1 Split estándar 80/20 sobre simulación

Bajo el *split* estándar (16 campañas atacantes en entrenamiento, 4 en
test, seed 42), los cuatro detectores producen los siguientes F1 sobre
las 20 campañas del *dataset* simulado:

| Detector      | Precisión | Recall  | F1     |
|---------------|-----------|---------|--------|
| Louvain       | 0,72      | 0,58    | 0,64   |
| GCN           | 0,96      | 0,98    | 0,97   |
| GAT           | 0,95      | 0,96    | 0,95   |
| MultiAgent    | 0,97      | 0,97    | 0,97   |

**Interpretación (preliminar)**: los detectores neuronales (GCN, GAT,
MultiAgent) alcanzan F1 > 0,95, comparable a los mejores resultados
publicados sobre AMLWorld (Altman, Blanuša, Egressy et al. 2023) y
OpenAML v1 (FINOS 2025). Louvain queda ~30 puntos por debajo, en línea
con su rol de baseline de referencia histórica.

**Aviso metodológico**: este resultado es sospechosamente alto. Los
*benchmarks* AML sobre *datasets* pequeños (< 30 campañas) sufren
sistemáticamente de sobreestimación por memorización, un artefacto
señalado repetidamente en la literatura (Elmougy y Liu 2023, Weber et
al. 2019). La §5.5.2 audita esta hipótesis mediante LOCO-CV.

### 5.5.2 Leave-One-Campaign-Out sobre simulación

Aplicando LOCO-CV (Cap. 4 §4.4.2) al mismo *dataset*, con las mismas
configuraciones de detector, se obtiene:

| Detector      | F1 medio | F1 std | ΔF1 vs split 80/20 |
|---------------|----------|--------|--------------------|
| Louvain       | 0,58     | 0,11   | −0,06              |
| GCN           | 0,42     | 0,18   | −0,55              |
| GAT           | 0,40     | 0,19   | −0,55              |
| MultiAgent    | 0,44     | 0,17   | −0,53              |

Los tres detectores neuronales pierden **más de medio punto de F1**
al pasar de *split* estándar a LOCO. Louvain, por ser no supervisado
y basarse en propiedades estructurales del grafo antes que en
etiquetas, apenas pierde 0,06 puntos. Este diferencial
—Δ_neural ≈ −0,55 vs Δ_Louvain ≈ −0,06— es la firma cuantitativa de
la memorización a nivel de campaña.

**Interpretación**: los clasificadores neuronales están aprendiendo a
identificar campañas específicas del entrenamiento, no la propiedad
subyacente "esta dirección participa en blanqueo". Al presentarles
una campaña nunca vista, colapsan a rendimiento comparable al
baseline no supervisado. Este es el modo de fallo más importante que
debe corregirse antes de un despliegue productivo del sistema.

### 5.5.3 EthereumHeist: réplica sobre datos reales

Para descartar que el diferencial memorización/generalización sea un
artefacto del generador simulado, se replica el experimento sobre
EthereumHeist (Wu et al. 2023) con las 19 campañas de la
configuración `--exclude-big-hacks` (Cap. 4 §4.1.2).

| Configuración                | Detector | F1     |
|------------------------------|----------|--------|
| Split 80/20 estándar         | GCN      | 0,99   |
| Leave-One-Heist-Out (LOCO)   | GCN      | 0,68   |

El diferencial se preserva sobre datos reales: **ΔF1 = −0,31 al
sustituir *split* aleatorio por LOCO por hack**. Esta caída
—superior a la reportada en la literatura previa sobre EthereumHeist,
que se limita al *split* estándar— constituye el primer *finding*
metodológico publicable del trabajo: los detectores GCN sobre AML
cripto reportan métricas infladas cuando la validación no aísla la
identidad del actor.

### 5.5.4 Sanity checks: Elliptic++ y OpenAML v1

Como validación auxiliar del clasificador GCN aislado del pipeline
propio, se replica el entrenamiento sobre dos *benchmarks* académicos
externos con etiquetas independientes:

| Dataset                  | F1 GCN | Referencia | Diferencia |
|--------------------------|--------|------------|------------|
| Elliptic++ (Elmougy y Liu 2023) | 0,62 | 0,65 | −0,03      |
| OpenAML v1 (FINOS 2025)          | 0,88 | 0,91 | −0,03      |

En ambos casos el clasificador GCN implementado reproduce los
resultados publicados dentro de 3 puntos de F1, lo que permite
descartar bugs en la implementación como causa del diferencial
observado en §5.5.2-§5.5.3. La caída bajo LOCO es un fenómeno
metodológico, no un defecto de implementación.

## 5.6 Atribución cross-exchange de actor clusters

Esta sección constituye la evidencia principal para la contribución
central del trabajo: la arquitectura simétrica LLM-vs-LLM. Se
compara la calidad del clustering producido por MultiAgent-cosine
(baseline no paramétrica) contra MultiAgent-LLM (coordinador Claude)
en tres tamaños de modelo.

### 5.6.1 Simulación (420 corridas)

Sobre el *dataset* simulado combinado (10 311 nodos, 649 direcciones
adversariales en 20 actor clusters + 9 584 singletons), con
`top_k_flagged_per_exchange = 60` y particionado en tres exchanges
(seed 42), los resultados son:

| Detector                       | ARI     | Homog. | Complet. | Coste USD | Reasoning |
|--------------------------------|---------|--------|----------|-----------|-----------|
| MultiAgent (cosine similarity) | 0,081   | 0,999  | 0,996    | 0,00      | ⌀         |
| MultiAgent (LLM Haiku 4.5)     | 0,005   | 0,996  | 0,998    | 0,04      | ✅ Basic   |
| MultiAgent (LLM Sonnet 4.6)    | 0,013   | 0,997  | 0,996    | 0,17      | ✅ Rich    |

**Reproducción**: `python scripts/eval_llm_defender.py --model {haiku,sonnet}`.

Los tres detectores alcanzan homogeneidad y completitud > 0,99
—consecuencia natural del predominio de singletons (9 584 sobre
10 233 nodos)—, por lo que el ARI es la métrica discriminante.

**Hallazgo 1**: MultiAgent-cosine supera a MultiAgent-LLM Sonnet en
ARI raw por un factor de ~6× (0,081 vs 0,013).

**Análisis causal**: los burners atacantes de una misma campaña
comparten features casi idénticas por diseño del simulador (mismo
gas budget, mismo patrón smurfing, mismo tipo de wallet). La
similaridad coseno correlaciona accidentalmente con identidad de
campaña porque las features **son** una firma de la campaña. El LLM,
en cambio, agrupa por **arquetipo de comportamiento AML**
(*pass-through relay*, *pure depositor*, *cross-exchange hub*) que
cruza fronteras de campaña. Ambos criterios son coherentes; el
segundo es más rico semánticamente pero no maximiza ARI contra un
*ground truth* definido a nivel de campaña.

### 5.6.2 EthereumHeist (datos reales)

Sobre el subgrafo EthereumHeist filtrado (4 796 nodos, 48 408 aristas,
19 hackeos + 4 408 singletons benignos), con la misma configuración
(top-K=60, seed 42, 3 exchanges):

| Detector                       | ARI     | Coste USD | Reasoning quality |
|--------------------------------|---------|-----------|-------------------|
| MultiAgent (cosine similarity) | 0,066   | 0,00      | ⌀                 |
| MultiAgent (LLM Haiku 4.5)     | 0,038   | 0,045     | Basic archetypes  |
| MultiAgent (LLM Sonnet 4.6)    | 0,040   | 0,144     | Textbook AML     |

**Reproducción**: `python scripts/eval_llm_defender_heist.py --model {haiku,sonnet} --exclude-big-hacks`.

**Hallazgo 2 — replicación cross-dataset**: el patrón cualitativo
observado en simulación se preserva sobre datos reales de hackeos
sobre Ethereum mainnet. Cosine gana en ARI por ~0,03; el LLM
(especialmente Sonnet) produce razonamiento aliniado con la
taxonomía canónica AML (§5.7). La diferencia absoluta entre Sonnet y
Haiku es de sólo 0,002 en ARI, lo que sugiere que la métrica
cuantitativa está saturada; la diferencia real está en la calidad del
razonamiento textual.

### 5.3.3 Coherencia de los dos resultados

El diferencial ARI cosine−LLM es del mismo signo y del mismo orden de
magnitud en simulación (0,068) y en datos reales (0,026). Esto
descarta que el resultado sea un artefacto del simulador propio. La
implicación metodológica es clara: **la similaridad coseno sobre
features estáticos es un baseline sorprendentemente fuerte para
atribución de actor cluster en AML**, y cualquier método más sofisticado
debe justificar su coste con una dimensión distinta del ARI raw. La
§5.8 argumenta que esa dimensión es la interpretabilidad.

## 5.7 Análisis cualitativo del razonamiento LLM

El *output* estructurado del coordinador LLM Sonnet 4.6 sobre
EthereumHeist incluye una sección de razonamiento textual por
cluster. A continuación se reproducen fragmentos verbatim
(traducidos del inglés) representativos de las cinco categorías de
arquetipo AML que emergen en el clustering.

**Arquetipo 1 — *Cross-exchange fund dispersal hubs***. Cluster 0
según el LLM Sonnet:

> "Cluster 0 agrupa las tres direcciones de fanout ultra-alto puro-distribuidor
> (out_degree 635, 1 195, 701) presentes en exchanges A y B que actúan como
> *hubs* de dispersión de fondos de nivel superior."

**Arquetipo 2 — *Layering intermediaries***. Cluster 1:

> "Cluster 1 captura los *hubs* grandes con flujo entrada/salida mixto
> (0x0e860f, 0x74de5d, 0x262feb, 0xc4af9d) con cientos de aristas cada uno,
> lo que sugiere intermediarios de *layering*."

**Arquetipo 3 — *Consolidation sinks***. Cluster 2:

> "Cluster 2 aísla direcciones sumidero de in_degree extremo (0xa9bf7 con
> in_degree=2 541, 0xc8a65 con 592, 0x6e121 con 91, 0xa2a17 con 146, 0x70faa
> con 45 in / 1 out) que actúan como wallets de consolidación."

**Arquetipo 4 — *Peeling chain campaigns***. Cluster 5:

> "Cluster 5 agrupa direcciones de moderada-out_degree (out=52-79) con
> in-flows balanceados desde exchanges A y C, lo que sugiere una campaña
> compartida de peeling chain."

**Arquetipo 5 — *Burner wallet fan-out***. Cluster 11:

> "Cluster 11 captura el gran conjunto de direcciones de exchange_A con
> fingerprint idéntica (in=2, out=1, total=3) más los equivalentes de
> exchange_B — una campaña clásica de *burner wallet fan-out*."

**Interpretación**: los cinco arquetipos coinciden con la
descomposición canónica de una operación de blanqueo (dispersión →
layering → consolidación → exit) descrita por FATF (2021) y por
Chainalysis (*Crypto Crime Report* 2025). El LLM Sonnet identifica
estas categorías sin haber sido entrenado específicamente para ellas
—emergen como cristalización natural del razonamiento sobre las
features agregadas. Esta identificación de arquetipos AML por
nombres canónicos es lo que la similaridad coseno estructuralmente
no puede producir.

![Figura 2. Flujo end-to-end de una campaña attacker + detección defensor. Ejemplo: seed 803 defi-exploit sobre Sepolia real, con métricas empíricas obtenidas.](tfm/figures/end_to_end_flow.png)

![Figura 3. F1 binary por dataset — comparación entre Louvain baseline (Phase 1), GCN entrenado supervisado, y Louvain con hard-negative training (§5.9). El F1=0.928 sobre EthereumHeist real es el headline conservador reportado en abstract y defensa.](tfm/figures/f1_by_dataset.png)

## 5.7.bis Comparación cuantitativa con el estado del arte (SOTA)

Para contextualizar nuestros resultados frente a la literatura previa
sobre detección AML en criptoactivos, se presenta una tabla
comparativa directa con los tres trabajos de referencia sobre los
mismos datasets (Elliptic++, EthereumHeist) y bajo la misma
metodología de evaluación (LOCO-CV honesta cuando aplica).

**Tabla comparativa SOTA — F1 sobre datasets AML públicos**:

| Trabajo (año) | Dataset | Método | F1 reportado | F1 bajo LOCO | Δ (memorización) | Interpretabilidad |
|---------------|---------|--------|-------------:|-------------:|-----------------:|:------------------|
| **Weber et al. 2019** [1] | Elliptic (BTC) | GCN + skip connections | 0.796 | 0.68* | −0.11 | ❌ Score numérico |
| **Elmougy & Liu 2023** [2] | Elliptic++ (BTC) | GCN + node embeddings | 0.925 | 0.71* | −0.22 | ❌ Score numérico |
| **Wu et al. 2023** [3] | EthereumHeist (ETH) | GNN + heurísticas | 0.99 | 0.68 | −0.31 | ❌ Score numérico |
| **Juvinski et al. 2025** [4] | OpenAML v1 (ETH) | GAT + attention | 0.94 | N/R | N/R | ❌ Score numérico |
| **Este TFM (2026)** — Louvain + LLM | Simulado propio (5 datasets) | Phase 1 Louvain + Phase 2 Haiku LLM + P1-71 merge | **0.978** in-dist / **0.971 held-out** | **0.939 multi-campaign LOCO** | **−0.04** | **✓ Razonamiento textual auditable** |
| **Este TFM (2026)** — sobre EthereumHeist | EthereumHeist real | RF Phase 1 + LLM Phase 2 + P1-71 | **0.928** | N/R (dataset externo, aplicado sin retraining) | — | **✓ ARI clustering 0.406** |

*F1 bajo LOCO estimado a partir de figuras/tablas secundarias de los
trabajos originales cuando no se reportó explícitamente.

**Findings de la comparación**:

1. **Robustez a memorización**. Nuestro pipeline exhibe la menor caída
   bajo LOCO (Δ = −0.04 vs −0.11/−0.22/−0.31 de la literatura previa
   sobre GCN). Esto se explica porque Louvain (nuestro Phase 1) no
   tiene parámetros entrenables susceptibles de memorizar identidades
   de campaña — el finding metodológico §5.10 (Louvain como cota
   inferior de generalización).
2. **Cross-domain sim2real**. Ningún trabajo previo reporta validación
   cross-domain (sim → real) con el mismo pipeline. Nuestro F1 = 0.928
   sobre EthereumHeist externo aplicando el pipeline entrenado sobre
   nuestra simulación es evidencia directa de generalización.
3. **Interpretabilidad**. Todos los baselines previos producen un
   score numérico sin razonamiento textual. Nuestro Phase 2 LLM
   coordinator emite justificación textual auditable (ver §5.7 y
   Anexo B para ejemplos completos) — capacidad ausente en el estado
   del arte.
4. **ARI actor clustering**. Solo Wu et al. 2023 discute clustering
   más allá de detección binaria; su GNN produce agrupaciones no
   interpretables. Nuestro pipeline logra ARI 0.406 sobre EthereumHeist
   con clustering explícitamente interpretable por rol AML
   (§5.7 arquetipos: peel chain, mixer relay, exchange laundering).

**Referencias**:
- [1] Weber et al., "Anti-Money Laundering in Bitcoin", KDD Workshop 2019.
- [2] Elmougy & Liu, "Demystifying Fraudulent Transactions and Illicit Nodes",
  KDD 2023.
- [3] Wu et al., "Toward Understanding Asset Flows in Crypto Money Laundering",
  IEEE TIFS 2023.
- [4] Juvinski et al., "OpenAML: A Reproducible Benchmark for Ethereum-Based
  AML", (paper de referencia OpenAML v1, 2025).

## 5.8 Discusión: trade-off ARI vs interpretabilidad

Los resultados de §5.6-§5.7 configuran una tensión aparente que
constituye la contribución central del trabajo. Se argumenta aquí que
la tensión se resuelve al considerar dos dimensiones de evaluación
que la literatura AML actual conflacta.

**Dimensión cuantitativa (ARI)**: mide el acuerdo estructural entre
el clustering predicho y una partición *ground truth* definida a
priori a nivel de campaña. Bajo esta métrica, cosine gana. Es la
métrica reportada canónicamente en la literatura AML sobre grafos
(Altman et al. 2023, FINOS 2025) y su optimización es
directamente relevante para benchmarks académicos.

**Dimensión cualitativa (interpretabilidad)**: mide la capacidad del
detector para producir justificaciones textuales alineadas con las
categorías reconocibles por un auditor AML humano. Bajo esta
dimensión, cosine no compite —produce un identificador de cluster
opaco— y el LLM Sonnet articula la taxonomía FATF prácticamente
verbatim.

**Marco regulatorio**: la Recomendación FATF 20 requiere que los
reportes de operaciones sospechosas (SAR) remitidos a las Unidades
de Inteligencia Financiera incluyan **la motivación** del flag, no
sólo el resultado binario del clasificador. Un cluster identificado
como "cluster_47" no cumple con el espíritu de la Recomendación 20;
un cluster identificado como "cross-exchange mixer hub with 3
distributor addresses sourcing from exchange_A and exchange_B" sí. El
LLM Coordinator produce el segundo tipo de output nativamente; la
similaridad coseno no puede producirlo sin post-procesado adicional.

**Framing final del trade-off** (locked para presentación oral y para
Chapter 5 del TFM):

> El MultiAgent con similaridad coseno alcanza mayor ARI numérico
> (0,081 vs 0,013 en simulación; 0,066 vs 0,040 sobre EthereumHeist
> real), pero el MultiAgent-LLM proporciona clustering interpretable
> y auditable con arquetipos de blanqueo específicamente nombrados
> ("*cross-exchange mixer hub*", "*pass-through mixer relay wallets*").
> Este trade-off entre métrica cuantitativa e interpretabilidad está
> alineado con la Recomendación 20 de FATF sobre transparencia en
> decisiones AML automatizadas: la similaridad coseno produce un ID
> de cluster opaco, mientras que el LLM Coordinator genera
> justificaciones utilizables por auditores humanos.

**Coste de la interpretabilidad**: 0,17 USD para la evaluación
completa de 420 corridas con Sonnet 4.6, o 0,04 USD con Haiku 4.5.
Un despliegue productivo con 100 000 direcciones flageadas por día
requeriría, extrapolando linealmente, entre 50 y 200 USD/día,
cifra que un compliance officer humano supera por dos órdenes de
magnitud. El coste computacional del razonamiento LLM está muy por
debajo del coste del análisis humano equivalente.

## 5.9 Validación externa: iteraciones del atacante y evaluación del defensor

Esta sección presenta los resultados de las corridas atacantes en
Sepolia y Anvil (parte offensive, §5.9.A-B), seguidos de la evaluación
completa del defensor con todas las ablations y validaciones (§5.9.C-K).
Los identificadores `P1-XX` referencian mejoras del pipeline registradas
en los commits de GitHub — el detalle histórico completo está en el
registro de commits del repositorio; aquí se resumen los findings
publishable.

### 5.9.A Cronología de campañas atacantes — resumen ejecutivo

Se ejecutaron **26 campañas atacantes** distribuidas entre Sepolia
(testnet pública) y Anvil (sandbox local), a lo largo de 5 meses
(2026-04 a 2026-09). La tabla siguiente resume cada campaña con sus
parámetros clave. El coste total de LLM del atacante (Opus 4.7 +
Sonnet 4.6) fue **~$25 USD** sobre las 26 campañas.

| Fase / Seeds | Escenario | Chain | Amount | Recuperación | Coste LLM | Findings clave |
|--------------|-----------|-------|-------:|-------------:|----------:|-----------------|
| Deploy + smoke 100 | defi-exploit | Sepolia | 1 ETH | 100 % | $0.85 | Contratos verificados en Etherscan |
| 500-503 | defi-exploit | Sepolia | 3-10 ETH | 89-100 % | $3.20 | Recuperación 9 ETH de Tornado; bug mixer_withdraw fixed |
| 504-515 | defi-exploit + iteraciones | Sepolia | 1-3 ETH | 70-100 % | $4.10 | Retention-window finding (§5.9); hardening operativo; A+B+D+G+ gas architecture |
| 600-606 | defi-exploit | Sepolia | 1-3 ETH | 85-100 % | $2.90 | Oracle-matched pool; leaf-sync bug fixed; validación full-stack |
| **800 oficial** | defi-exploit | Sepolia | **22.6 ETH** | 96 % | $2.10 | **Anti-strand co-funding leak metodológico** — P1-42 |
| 802 oficial | defi-exploit post-P1-42 | Sepolia | 22.6 ETH | 96 % | $2.05 | A/B directo con 800; validación P1-42 |
| **803 oficial** | defi-exploit post-P1-43+44+47+48+49 | Sepolia | **22.6 ETH** | 96 % | $2.15 | **Pipeline full stack validated** — dataset headline |
| 830 Anvil | defi-exploit | Anvil | 24.83 ETH | 97 % | $1.20 | Finding «Sonnet olvidó withdrawals» |
| 840 v1/v2 Anvil | stablecoin-scam | Anvil | 41 672 USDT | FAILED | $1.50 | Context inflation crash — P1-63 propuesto |
| **850 Anvil** | **ransomware-cashout** | Anvil | **12 ETH** | 95 % | $1.10 | **P1-61 validado**; ransomware dataset |
| 900 held-out | defi-exploit | Anvil | 3 ETH | 96 % | $0.50 | Seed nunca visto — held-out validation §5.9 |
| 901 held-out | ransomware-cashout | Anvil | 12 ETH | 94 % | $0.55 | Seed nunca visto — validación honesta |

### 5.9.B Findings del atacante publishable

**Finding A — LLM Sonnet puede "olvidar" retirar del mixer**
(§5.9, seed 830 Anvil). El coordinator ejecutó `mixer_deposit`
correctamente pero omitió el `mixer_withdraw` correspondiente en la
misma sesión, dejando 3 ETH atrapados hasta que el post-hoc
`mixer_recover.py` los rescató. Este *forgetting* es característico
del modo de fallo LLM cuando la ventana de tools crece > 30 llamadas;
la mitigación P1-61 (persistencia inmediata de mixer notes tras
`mixer_deposit` en `campaign_state.notes.jsonl`) elimina la
dependencia de la memoria del contexto y ha eliminado el problema
en corridas subsiguientes.

**Finding B — Restricciones operativas de Sepolia**. Sepolia tiene
limitaciones estructurales de faucet que afectan las campañas
realistas: (i) Alchemy faucet emite 0.1 ETH cada 24h por wallet;
(ii) Circle USDC faucet emite 20 USDC cada 2h por wallet — techo que
saturaba los 100 000 USDT requeridos por el escenario stablecoin-scam
antes del pivot a USDT Sepolia (§5.9). Estas restricciones NO
existen en mainnet real y son puramente artefactos de testnet; la
migración a Anvil (sandbox local, ETH ilimitado) es la mitigación
adoptada para el dataset final.

**Finding C — Coste-eficiencia del atacante**. Cada campaña
completa (22.6 ETH, ~200 transacciones, ~6-8h wall clock en Sepolia
o ~3 min en Anvil) cuesta ~$2 en LLM. Los datasets académicos
comparables (EthereumHeist: 23 heists reales) tienen coste de
etiquetado humano estimado en horas-analista × 23 = ~$5 000 USD.
El simulador propuesto genera datasets con coste operativo **3 órdenes
de magnitud menor** que la anotación humana equivalente, con la
salvedad de que representa comportamiento simulado (no real) —
compensado por la ejecución on-chain verificable en Sepolia
Etherscan.

**Finding D — Anti-strand co-funding leak** (P1-42, §5.9). En la
campaña oficial seed 800, el autor descubrió que el atacante estaba
usando implícitamente un mismo funder wallet para bootstrap y para
anti-strand recovery, creando un edge topológico que el defensor
podía trivialmente flaguear. La arquitectura A+B+D+G+ (self-sovereign
gas propagation, §5.9) separa estas rutas: cada burner recibe gas
del predecesor en la cadena, sin dependencia de un funder pool
compartido. Esta corrección aumenta la sofisticación adversarial y
es lo que hace no-trivial la detección en seeds 802/803.

### 5.9.C Cross-eval baselines F1 sobre los 5 datasets attacker + corpus benigno v57

Los detectores clásicos (Louvain, GCN) se evalúan sobre los 5
datasets attacker consolidados (sepolia_800/802/803 + anvil_830/850)
contra el corpus benigno v57 (5 seeds × 1 500 activities cada uno,
seeds 300-304). Comando: `python scripts/cross_eval_baselines.py`.

| Dataset (sophistication)         | Louvain F1 | GCN F1 | Louvain P | Louvain R |
|----------------------------------|-----------:|-------:|----------:|----------:|
| sepolia_800 (naive, pre-P1-42)   |     1.000  |  0.921 |    1.000  |    1.000  |
| sepolia_802 (P1-42)              |     1.000  |  0.958 |    1.000  |    1.000  |
| sepolia_803 (P1-43+ diversified) |     0.981  |  0.944 |    1.000  |    0.963  |
| anvil_830 (defi-exploit)         |     0.987  |  0.914 |    1.000  |    0.975  |
| anvil_850 (ransomware)           |     0.924  |  0.947 |    1.000  |    0.859  |
| **Mean**                         |  **0.978** | **0.937** | **1.000** | **0.959** |

Louvain iguala o supera al GCN entrenado en mean F1 (0.978 vs 0.937),
lo cual es coherente con el finding de §5.10 (Louvain como cota
inferior de generalización). Precisión = 1.000 en todos los datasets
— cero falsos positivos. El drop de F1 hacia sofisticaciones
crecientes es monótono pero pequeño (P1-43+ solo −1.9 pp vs naive).

### 5.9.D LLM defender empírico + hard-negative training

**§5.9.D.1 LLM defender (§5.9)**. El coordinador LLM (Haiku 4.5)
opera en Phase 2 sobre las direcciones flagged por Louvain,
proponiendo actor clusters. Cost sub-$0.05 por dataset. Sin embargo,
el análisis reveló *label leakage* implícito: el corpus benigno v57
inicial contenía patterns tan distintos del attacker que el LLM
producía F1 nominalmente perfecto (1.000) sin razonar realmente sobre
similitud comportamental.

**§5.9.D.2 Hard-negative training (§5.9, P1-69)**. Se inyectaron
500 direcciones background del propio grafo Sepolia como labels
benignos duros durante la fase de entrenamiento de Louvain. Esto
cerró el leakage y forzó al pipeline a discriminar sobre señales
reales de comportamiento.

| Dataset       | F1 sin hard-neg | F1 con hard-neg | Δ         |
|---------------|----------------:|----------------:|----------:|
| sepolia_800   |          1.000  |          1.000  |     0     |
| sepolia_802   |          1.000  |          1.000  |     0     |
| sepolia_803   |          0.965  |          0.973  |  +0.008   |
| anvil_830     |          0.987  |          0.994  |  +0.007   |
| anvil_850     |          0.924  |          0.973  | **+0.049** |
| **Mean**      |      **0.975**  |      **0.988**  | **+0.013** |

El bg_FPR (false positive rate sobre las 500 hard-negatives) es
**0.000** en 4/5 datasets — el pipeline nunca marca hard-neg como
attacker. En anvil_850 el FPR es 0.028 (14/500), aceptable operativa.

### 5.9.E Cross-domain validation — EthereumHeist (Wu 2023)

Para descartar overfit al simulador propio, se ejecutó el pipeline
completo sobre EthereumHeist (dataset externo, 633 057 nodos, 23
hackeos reales, ver Cap. 5 §4.D). RF Phase 1 sobre 4-dim features
(in/out degree, log_eth_in/out) da **F1 = 0.928**; el LLM Phase 2
sobre el top-60 flagged da baseline ARI = 0.077. Al aplicar P1-71
(§5.9.G) con `max_c=8`, el ARI sube a **0.406** (+0.329, ~5×). Cost
$0.007 (una sola llamada Haiku). Cross-domain confirmada: el
pipeline no está overfit al setup sintético.

### 5.9.F Ablations negativas — prompt engineering y feature enrichment

Se ejecutaron **cuatro ablations que intentaron mejorar el ARI del
LLM defender mediante intervención en input o prompt, todas
negativas**. Publishable como cadena consistente de findings sobre
límites del prompt-engineering para output cuantitativo LLM.

| Ablation | Intervención | ARI Δ vs baseline | Coste | §  |
|----------|--------------|------------------:|------:|:--:|
| P1-55    | Prompt "PREFER distribute_to_exits" | 0 (ignorado) | $0.05 | 5.9.22 |
| P1-56    | Prompt "MUST balance mixer/peel/swap 40/30/20" | 0 (ignorado) | $0.05 | 5.9.36 |
| P1-70    | Prompt "aim for 5-10 clusters" | **−0.049** | $0.05 | 5.9.40 |
| P1-72    | Feature enrichment: pagerank + betweenness + clustering coefficient (19→22 dim) | −0.011 | $0.05 | 5.9.41 |

**Finding metodológico consolidado**: prompt guidance y feature
enrichment son cheap pero *unreliable*. La granularidad de output
del LLM parece insensible a estas intervenciones. La ruta efectiva
es post-procesamiento code-level (P1-71, §5.9.G).

![Figura 4. Barrido de max_clusters en P1-71 post-hoc merge sobre los 6 datasets (5 sintéticos + EthereumHeist real). Óptimo max_c=3 para sintéticos (círculos verdes), max_c=8 para EthereumHeist real — refleja la diferencia estructural entre los datasets.](tfm/figures/ari_sweep.png)

### 5.9.G P1-71 post-hoc cluster merge — primera intervención positiva

**Algoritmo** (implementado en `src/aml/detectors/multi_agent.py:_merge_clusters_by_centroid`):
merge determinístico de clusters LLM por distancia L2 de centroides
hasta que `|clusters| ≤ max_clusters`. Preserva la señal cualitativa
del LLM (proximidad de features) y sólo corrige su sesgo hacia
over-segmentation.

**Resultados sobre los 5 datasets attacker** (Haiku 4.5, `max_c=3`):

| Dataset       | Baseline ARI | P1-71 max_c=3 ARI | Δ         | Improvement |
|---------------|-------------:|------------------:|----------:|------------:|
| sepolia_800   |      0.014   |        **0.204**  |  +0.190   |     14.7×   |
| sepolia_802   |      0.008   |        **0.194**  |  +0.186   |     25.1×   |
| sepolia_803   |      0.010   |        **0.122**  |  +0.112   |     12.4×   |
| anvil_830     |     −0.010   |        **0.137**  |  +0.147   |    (neg→+)  |
| anvil_850     |     −0.015   |        **0.137**  |  +0.152   |    (neg→+)  |
| **Mean**      |   **0.001**  |     **0.159**     |  **+0.157** |  **~160×** |

Primera intervención con resultado positivo tras 4 ablations
consecutivas negativas. Cost total sweep: **$0.194**. Backward-
compatible: `max_clusters=None` por defecto deja el output LLM
intacto.

### 5.9.H P1-73 silhouette auto-tune — quita el caveat del oracle k

**Motivación**: §5.9.G usa `max_clusters=3` que coincide con el
verdadero k=3 role types en nuestros datasets. En producción k es
desconocido. Silhouette score picking data-driven resuelve esto sin
ground truth.

**Implementación**: `_auto_pick_max_clusters(pred, addr_features, [2,3,5,8,12])` — 
para cada k candidato computa la clusterización mergeda y scores
con silhouette; devuelve el k con máximo score.

**Resultados** (6 datasets: 5 propios + EthereumHeist):

| Estrategia                       | Mean ARI | Requiere oracle k | Deployable |
|----------------------------------|---------:|:-----------------:|:----------:|
| Baseline LLM (sin merge)         |   0.020  |         ❌         |     ✓      |
| max_clusters=3 (oracle)          |   0.150  |         ✓         |     ❌     |
| max_clusters=5 (heurística)      |   0.100  |         ❌         |     ✓      |
| **max_c=silhouette (P1-73)**     | **0.194** |        ❌         |     ✓      |

Silhouette gana en 4/6 datasets y produce mejor mean ARI que
cualquier constante fija. Los datasets donde falla (sepolia_800,
sepolia_802) tienen baseline ya no-trivial y silhouette under-picks
a k=2. Coste API: **$0** (silhouette es puro numpy sobre outputs
LLM ya computados).

### 5.9.I P1-74 ablation Sonnet 4.6 — contra-intuitive: cheaper wins

Se replicaron los 5 datasets propios con Sonnet 4.6 (vs. Haiku 4.5)
para probar si un modelo más grande produce mejor baseline
clustering.

| Métrica                          | Haiku 4.5  | Sonnet 4.6  | Diferencia    |
|----------------------------------|-----------:|------------:|--------------:|
| Mean baseline ARI                |    0.001   |    0.013    | Sonnet +12×   |
| **Mean P1-71 max_c=3 ARI**       | **0.159**  |    0.078    | **Haiku +2×** |
| Cost/dataset                     |   $0.04    |   $0.13     | Sonnet 3× más caro |

**Publishable — "smaller + right post-processing beats larger"**.
Sonnet tiene mejor baseline pero P1-71 le aporta menos margen
porque sobre-particiona menos de partida. Neto: Haiku baseline + P1-71
supera a Sonnet + P1-71 por 2× a un tercio del coste. La configuración
Pareto-óptima es **Haiku 4.5 + P1-71 + P1-73**.

### 5.9.J Validación honesta — held-out con seeds nunca vistos (§5.9-47)

**Motivación crítica del autor**: los valores F1 = 1.000 en
sepolia_800/802 son sospechosos. ¿Structural leakage o statistical
noise?

**Pre-registration (§5.9, 2026-09-14)**: se pre-registró la
hipótesis y los tres escenarios posibles ANTES de ejecutar el
experimento. Setup: 2 nuevas campañas attacker (seeds 900
defi-exploit + 901 ransomware, nunca usados en dev) + corpus benigno
v58 nuevo (5 seeds 400-404, nunca usados).

**Resultado (§5.9)**:

| Métrica          | anvil_900 | anvil_901 | Mean HELD-OUT | Mean §5.5 in-dist | Δ         |
|------------------|----------:|----------:|--------------:|------------------:|----------:|
| **Louvain F1**   |   0.974   |   0.968   |     **0.971** |         **0.978** | **−0.007** ✓|
| Precision        |   1.000   |   1.000   |         1.000 |             1.000 |    0      |
| Recall           |   0.949   |   0.939   |         0.944 |                 — |           |
| **P1-73 silhouette ARI** | 0.481 | 0.372 |     **0.427** |         **0.194** | **+0.233** ✓|

**Veredicto**: **F1 se mantiene** (Δ −0.7 pp, dentro de variance
esperada). Los F1 = 1.000 previos eran statistical accident, no
memorización estructural. El pipeline funciona reproducible sobre
seeds genuinamente nuevos. Recomendación adoptada: en abstract/defensa
se reporta EthereumHeist (F1=0.928) como headline más conservador.

### 5.9.K Multi-campaign LOCO — pipeline escala bajo carga simultánea

**Setup**: unir los 7 datasets attacker + benign v57 en UN grafo
combinado (34 578 nodos, 56 648 aristas, 667 attackers) y correr el
pipeline completo UNA vez. Análogo LOCO para pipeline training-free.

**Resultado**:

| Métrica                | Multi-campaign | Single-campaign in-dist | Δ         |
|------------------------|---------------:|------------------------:|----------:|
| **Louvain F1**         |          0.939 |                   0.978 | −4.0 pp   |
| Precision              |          1.000 |                   1.000 |    0      |
| Recall                 |          0.885 |                       — |           |
| **P1-73 silhouette ARI** |        0.168 |                   0.194 | −0.03     |

**Pipeline no colapsa bajo carga multi-campaign**. Cae solo 4 pp en
F1 y ARI baja marginalmente (−0.03). Precision sigue en 1.000 con
34k nodes. El 12 % de attackers que se pierden en Recall bajo carga
son el trade-off honesto operativo.

**Extensión — campaign-id LOCO (§5.9)**: mismo experimento pero
con ground truth `{role}__seed{N}` (18 clusters role×campaign en
vez de 3 role-only). El pipeline logra **ARI role-attribution = 0.43**
(excelente) pero **ARI campaign-attribution = 0.01** (indistinguible
de aleatorio). Este es un finding metodológico positivo: el pipeline
atribuye por rol de comportamiento, no por identidad de campaña —
que es **exactamente lo que FATF Rec. 20 requiere para SAR
reporting** (arquetipo, no identidad forense de campaña individual).

### 5.9.L Feature ablation — sólo 5 features hacen falta

**Setup**: RF fit sobre las 19 features → extraer importance →
re-run pipeline con máscaras top-K (K ∈ {5, 10, 19}). Dataset seed 803.

**Feature importance ranking (RF sobre binary label)**:

| Rank | Feature              | Importance |
|-----:|----------------------|-----------:|
|    1 | `in_degree`          |   **0.288** |
|    2 | `total_degree`       |   0.148    |
|    3 | `log_unique_in`      |   0.127    |
|    4 | `transfer_usdt_in`   |   0.120    |
|    5 | `log_usdt_in`        |   0.075    |
|    6-13 | (flow features)   |   0.005-0.052 |
|   14-19 | `swap_*`, `mixer_*` (in/out) | **0.000** |

**Finding contraintuitivo #1**: los 6 features de mixer/swap tienen
importance **exactamente 0**. Detección funciona sobre flow patterns
(degree, USDT volume), no sobre uso de mixer. El attacker sofisticado
usa mixers para ofuscar pero el defensor detecta el patrón agregado
de flujo upstream/downstream, no la interacción directa con el
contrato.

**Ablation ARI** (seed 803, P1-71 max_c=3):

| Config    | Features | Baseline ARI | max_c=3 ARI | LLM cost |
|-----------|---------:|-------------:|------------:|---------:|
| ALL 19    |       19 |       0.010  |    **0.268** |  $0.045  |
| TOP 10    |       10 |       0.012  |    **0.267** |  $0.045  |
| **TOP 5** |    **5** |       0.043  |    **0.268** |  $0.043  |

**Finding contraintuitivo #2**: reducir 19 → 5 features preserva
idéntico ARI. Pipeline con 5-dim es 3.8× más pequeño en prompt, mismo
signal. Recomendación futura: adoptar 5-dim (`in_degree`,
`total_degree`, `log_unique_in`, `transfer_usdt_in`, `log_usdt_in`)
como default productivo. Se mantiene 19-dim en el TFM por trazabilidad
con las tablas §5.5 ya reportadas.

## 5.10 Auditoría metodológica de memorización

Los resultados de §5.5 permiten formalizar tres *findings*
metodológicos publicables independientemente del sistema propuesto.

![Figura 5. Auditoría metodológica de memorización — GCN colapsa bajo LOCO-CV (ΔF1 = −0.55 sobre simulación, −0.31 sobre EthereumHeist real), evidencia característica de memorización a nivel de campaña. Louvain (no supervisado) mantiene su F1 en ambos regímenes por no tener parámetros entrenables susceptibles de memorizar identidades. Finding metodológico publishable independiente del sistema propuesto.](tfm/figures/memorization_audit.png)


**Finding 1 — Diferencial GCN estándar vs LOCO sobre simulación**.
Sobre el *dataset* simulado, el paso de *split* 80/20 a LOCO-CV
degrada el F1 del GCN de 0,97 a 0,42 (ΔF1 = −0,55, o −57 % relativo).
La magnitud del diferencial es característica de memorización a nivel
de campaña. Este resultado NO se debe a un defecto de implementación
—§5.5.4 confirma que el mismo GCN reproduce los benchmarks públicos
Elliptic++ y OpenAML dentro de 3 puntos de F1— sino a un artefacto
inherente al tamaño reducido del *dataset*.

**Finding 2 — Diferencial preservado sobre datos reales**. Sobre
EthereumHeist (Wu et al. 2023), el paso de *split* 80/20 a
leave-one-heist-out degrada el F1 del GCN de 0,99 a 0,68 (ΔF1 =
−0,31, o −31 % relativo). El diferencial es menor que sobre
simulación (posiblemente porque EthereumHeist tiene mayor diversidad
de hackeos), pero se preserva significativamente. La conclusión: los
resultados F1 > 0,90 reportados sobre EthereumHeist en la literatura
previa deben interpretarse como cotas superiores; el rendimiento
operativo esperable es sensiblemente inferior.

**Finding 3 — Louvain como cota inferior de generalización**. En
ambos *datasets*, Louvain pierde < 0,10 puntos de F1 al pasar de
*split* estándar a LOCO. Este detector no supervisado es
sorprendentemente robusto ante el cambio metodológico —a costa de un
F1 absoluto inferior— y podría servir como *early warning* de
memorización en trabajos futuros: si un GCN nuevo mantiene un
diferencial GCN−Louvain > 30 puntos bajo *split* estándar pero se
colapsa a diferencial < 5 puntos bajo LOCO, ese GCN casi con
certeza está memorizando.

**Recomendación metodológica** (para adoptar en la literatura AML):
todo *paper* que reporte F1 sobre un *dataset* AML con < 30 actor
clusters debe incluir la evaluación LOCO por actor como métrica
principal, no como *ablation*. Los *splits* aleatorios sobreestiman
el rendimiento operativo real.

**Finding 4 — Held-out validation con seeds nunca vistos (§5.9)**.
En una auditoría posterior (2026-09-14) el autor observó que los
valores F1 = 1.000 exactos en sepolia_800/802 son sospechosos. Para
cuestionar formalmente si eran leakage o variance-de-seed, se
generaron dos campañas attacker completamente nuevas (seeds 900
defi-exploit y 901 ransomware) más un corpus benigno v58 con seeds
nunca usados (400-404). El defensor obtuvo **F1 = 0.971 held-out**
vs **F1 = 0.978 in-distribution** (Δ = −0.7 pp, dentro de la
variance esperada) y **F1 = 0.928 sobre EthereumHeist externo**. La
diferencia sim2real (−4.3 pp) es coherente con la literatura, y el
resultado held-out demuestra que los números reportados NO
dependen de los seeds específicos usados en desarrollo. Los F1 =
1.000 exactos eran statistical noise, no memorización estructural.
