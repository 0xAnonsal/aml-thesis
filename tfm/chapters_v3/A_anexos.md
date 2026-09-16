# Anexos

Los anexos documentan los artefactos técnicos referenciados en los
capítulos anteriores. Su función es hacer el trabajo verificable e
independientemente reproducible por un tercero. El código íntegro
está publicado bajo licencia MIT en
[`github.com/0xAnonsal/aml-thesis`](https://github.com/0xAnonsal/aml-thesis).

Los prompts del atacante y del defensor se reproducen aquí en su
forma estructural (bloques y guardarraíles) más que como texto
íntegro; el texto completo reside en los archivos referenciados y
está sujeto a versionado en Git. Cualquier cambio material a los
prompts se acompaña de una entrada en el `CHANGELOG.md` del
repositorio.

> **Nota**: los Anexos B (prompt defensor), C (catálogo íntegro de
> herramientas), D (contratos desplegados) y E (comandos de reproducción)
> del draft previo se han retirado por brevedad. Todo su contenido está
> disponible en el repositorio público
> [`github.com/0xAnonsal/aml-thesis`](https://github.com/0xAnonsal/aml-thesis):
> el prompt del defensor está en `src/aml/detectors/multi_agent.py:_LLM_COORDINATOR_SYSTEM_PROMPT`,
> el catálogo de herramientas en `src/aml/attackers/tools.py`, los
> contratos verificados en Sepolia Etherscan (ver Anexo F), y los
> comandos exactos en `scripts/` y `scratchpad/`.

## Anexo A — Prompts del atacante multi-agente

Archivo: `src/aml/attackers/prompts.py` (422 líneas). Contiene cuatro
*system prompts* (Coordinador y tres especialistas FATF) más tres
bloques composicionales que se interpolan en los cuatro roles para
mantener consistencia semántica.

### A.1 Bloques composicionales

**`_RESEARCH_FRAMING`**. Encabezado ético común a los cuatro roles:
declara el contexto de simulación controlada, la ausencia de fondos
reales, y el objetivo AML defensivo. Este bloque es lo que legítima
la generación de comportamiento adversarial —sin él, los modelos
Claude aplicarían sus políticas de rechazo estándar a peticiones de
laundering.

**`_GAS_DISCIPLINE`**. Restricciones operativas sobre gestión de gas:
piso mínimo de 0,005 ETH reservado por burner (refinado de los
0,05 ETH iniciales tras las corridas seed 304-307), uso de
`get_gas_budget` para verificar capacidad transaccional restante,
semántica *drain-only-at-end* que sólo permite vaciar el gas dust al
final de la campaña.

**`_MARKET_CONTEXT`**. Bloque inyectado dinámicamente por el runner
con el precio ETH/USDT/TRX del oráculo determinista
(`env/market_context.py`) fijado a la fecha de campaña. Permite al
agente razonar en unidades USD (natural para el umbral CTR de 999
USD) sin re-calibrar prompt entre corridas con precios diferentes.

**`_BURNER_MODERATION`** (post-fix A+B-lite, §8.9.5). Instrucción
explícita al Layering specialist para que solicite *"MODERATE aids"*
en vez de la formulación anterior *"OVER-PROVISIONING WELL BEYOND
REQUIRED"*, que en seed 306 produjo el bucle patológico de 236
burners para 1 ETH. Complementa el *hard cap* del *dispatcher*
descrito en §3.3.

**`_DETECTOR_EVASION`**. Instrucciones tácticas de evasión adversarial.
Nombra explícitamente los tres detectores contra los que corre la
campaña (Louvain, GCN estilo Weber, MultiAgent colaborativo) y
enumera las señales que cada detector explota. Instrucción central:
"*be deliberately heterogeneous*" —varía topologías, monta amounts,
mezcla tipos de transacción— porque un pipeline laundering
internamente uniforme es trivialmente aprendible.

### A.2 Coordinator (`COORDINATOR_SYSTEM`)

Instanciado sobre Claude Opus 4.7. Rol de estratega puro: no ejecuta
transacciones directamente. Dispone de cuatro herramientas:

- `delegate_to_placement`: FATF stage 1.
- `delegate_to_layering`: FATF stage 2.
- `delegate_to_integration`: FATF stage 3.
- `inspect_chain`: audit read-only.

Cada delegación pasa a un sub-agente un `objective` (qué lograr) y un
`context` (todo el estado necesario: direcciones, montos, notas del
mezclador, hechos cargados desde fases previas). El coordinador
mantiene el hilo entre fases y decide cuándo re-delegar si un
sub-agente reporta *partial* o *failed*.

Responsabilidad crítica del coordinador: **planificación de clean
exits**. Debe calcular el número de wallets finales de destino
(*clean exits*) que la fase Integration debe crear, respetando la
restricción CTR (< 999 USD por exit) y una dispersión realista de
2-5 plataformas.

### A.3 Placement (`PLACEMENT_SYSTEM`)

Instanciado sobre Claude Sonnet 4.6. Función: introducir los fondos
en el sistema. Operaciones típicas: `mint_usdt`, `transfer_usdt` a
las primeras direcciones intermedias, generación de burners iniciales.
El sub-agente Placement no ve la conversación del Coordinador ni la
de sus pares —opera aislado sobre su `objective` y `context`.

### A.4 Layering (`LAYERING_SYSTEM`)

Instanciado sobre Claude Sonnet 4.6. Es el sub-agente que consume el
80 % de las herramientas del catálogo. Ejecuta las técnicas de
ofuscación: smurfing (`smurf_split`, `smurf_eth_split`), peel chains
(`peel_chain`), swaps (`swap_eth_for_usdt`, `swap_usdt_for_eth`),
depósitos y retiradas en el mezclador (`mixer_deposit`,
`mixer_withdraw`). El contrato `MockBridge` está desplegado
(Sepolia D.1) pero fuera del alcance del catálogo de herramientas
del atacante: no se expone como *tool* al LLM porque el *bridge*
real requeriría integración *cross-chain* que queda como trabajo
futuro (§10.4.4).

El prompt Layering incluye un catálogo priorizado de tácticas por
tamaño de flow: para movimientos ≥ 1 ETH prefiere ciclos
mezclador; para movimientos < 1 ETH prefiere structuring
multi-hop.

### A.5 Integration (`INTEGRATION_SYSTEM`)

Instanciado sobre Claude Sonnet 4.6. Función: consolidar los fondos
en las direcciones limpias de destino que el propio sub-agente crea
mediante `register_clean_exit`. Restricción principal: cada *exit*
debe recibir < 999 USD para permanecer por debajo del umbral
sub-Travel-Rule crypto (Recomendación FATF 16 fija 1 000 USD para
transferencias de criptoactivos; el CTR estadounidense clásico de
10 000 USD del BSA es el orden de magnitud superior y no aplica
aquí). El nombrado de exits sigue el patrón
`<platform>_<n>` (por ejemplo `binance_3`, `coinbase_1`) para
facilitar la trazabilidad forense posterior.

## Anexo F — Estructura del repositorio

```
aml-thesis/
├── contracts/                Contratos Solidity (~800 LOC)
│   ├── MockUSDT.sol          ERC-20 mock (6 decimales, permissionless mint)
│   ├── MockUniswapV2Pool.sol AMM ETH/USDT constant-product
│   ├── MockTornado.sol       Mezclador ZK Groth16
│   ├── MerkleTreeWithHistory.sol
│   ├── MockBridge.sol        Puente cross-chain lock-and-release
│   ├── IHasher.sol / IVerifier.sol
│   └── Verifier.sol          Auto-generado por snarkjs
├── circuits/                 Circuitos Circom para el mezclador
│   └── withdraw.circom       Prueba de pertenencia al árbol Merkle
├── src/aml/                  Paquete Python principal (~9 000 LOC)
│   ├── attackers/            Multi-agente ofensivo
│   │   ├── coordinator.py    Opus 4.7 estratega
│   │   ├── sub_agent.py      Sonnet 4.6 x 3 (Placement/Layering/Integration)
│   │   ├── prompts.py        4 system prompts + 3 bloques composicionales
│   │   ├── tools.py          19 herramientas on-chain
│   │   ├── llm_client.py     Wrapper Anthropic SDK
│   │   ├── run_campaign.py   Runner end-to-end
│   │   └── scenarios.py      Escenarios pre-definidos (defi/scam/ransomware)
│   ├── detectors/            Pipeline defensivo
│   │   ├── gnn.py            GCN 2-layer 32-hidden
│   │   ├── gat.py            GAT alternativo
│   │   ├── baselines.py      Louvain + PerExchangeDetector
│   │   ├── multi_agent.py    MultiAgentDetector + LLMDefenderCoordinator
│   │   ├── dataset.py        partial_visibility_split federación
│   │   ├── graph.py          NetworkX ↔ PyTorch Geometric
│   │   ├── eval.py           F1, ARI, homogeneidad, completitud
│   │   ├── run_benign.py     Generador de campañas benignas
│   │   └── viz.py            Utilidades de visualización
│   ├── chains/               Abstracción blockchain
│   │   ├── anvil.py          Context manager Anvil local
│   │   ├── mimc.py           Deploy MiMCSponge auto-generado
│   │   ├── eth_stack.py      Stack completo mock ETH
│   │   └── trace.py          Extracción grafo desde RPC
│   └── utils/                Helpers cross-cutting
├── scripts/                  Scripts CLI de reproducción
│   ├── deploy_eth_mocks.py         Deploy sobre Anvil ephemeral
│   ├── deploy_eth_mocks_sepolia.py Deploy sobre Sepolia persistent
│   ├── verify_sepolia_deployment.py
│   ├── eval_llm_defender.py        Eval sobre simulación
│   ├── eval_llm_defender_heist.py  Eval sobre EthereumHeist
│   ├── loco_simulation_3detectors.py LOCO-CV simulación
│   ├── loco_ethereum_heist.py      LOCO-CV EthereumHeist
│   ├── audit_f1_memorization.py    Auditoría §8.10
│   ├── load_ethereum_heist.py      Adaptador dataset Wu 2023
│   └── ...                          (más scripts de utilidad)
├── tests/                    Suite pytest (~300 tests, cobertura > 85%)
├── docs/                     Documentación
│   └── SEPOLIA_DEPLOY.md     Runbook Sepolia
├── deployments/              JSONs de deploys (gitignored)
├── data/                     Datasets (gitignored excepto price cache)
├── results/                  JSON outputs por experimento
├── tfm/                      Este documento
│   ├── chapters_v2/          Capítulos definitivos (estructura UC3M 10 caps)
│   │   ├── 01_introduccion.md
│   │   ├── 02_analisis_comparaciones.md
│   │   ├── 03_tecnologias.md
│   │   ├── 04_diseno_diagrama.md
│   │   ├── 05_lenguajes_usados.md
│   │   ├── 06_arquitectura_software.md
│   │   ├── 07_decisiones.md
│   │   ├── 08_implementacion_pruebas.md
│   │   ├── 09_datasets_parametros.md
│   │   ├── 10_conclusiones.md
│   │   └── A_anexos.md          (este archivo)
│   └── README.md             Instrucciones pandoc → PDF/DOCX
├── foundry.toml              Config compilador Solidity
├── pyproject.toml            Config paquete Python
├── environment.yml           Conda environment
├── .env.example              Template variables entorno
├── .env.sepolia.example      Template Sepolia
└── README.md                 Overview del repo
```

## Anexo G — Atribuciones de código, artefactos criptográficos y datasets de terceros

Este anexo enumera exhaustivamente todo el código, los artefactos
criptográficos y los datasets de terceros integrados en el proyecto,
con la licencia bajo la que se utilizan y una descripción de la
adaptación o el rol dentro del sistema. El repositorio del proyecto
también incluye ficheros `LICENSE` (código propio bajo licencia MIT)
y `ATTRIBUTIONS.md` (versión operacional de esta lista para
consumidores del código).

### G.1 Contratos Solidity adaptados de proyectos externos

**Tornado Cash** — [`github.com/tornadocash/tornado-core`](https://github.com/tornadocash/tornado-core) (MIT).
Base arquitectónica del mezclador ZK utilizado en el simulador:

| Fichero propio                                | Origen Tornado                       | Naturaleza de la adaptación                                                                                                                                             |
|-----------------------------------------------|--------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `contracts/MockTornado.sol`                   | `contracts/ETHTornado.sol`           | Denominación única fija (sin *routing* multi-denominación); sin *relayer* (fee y refund a cero en las pruebas); comentarios ampliados para revisores externos al ZK.    |
| `contracts/MerkleTreeWithHistory.sol`         | `contracts/MerkleTreeWithHistory.sol`| Preservación literal de la estructura *append-only* con historial acotado de raíces, hashLeftRight MiMC-Feistel y dígitos de subárbol precomputados; cambios sólo en comentarios e idioms Solidity 0.8+; sin cambios semánticos al árbol.  |
| `circuits/withdraw.circom`                    | `circuits/withdraw.circom`           | Preservación literal del witness (nullifier + secret, prueba de inclusión Merkle, derivación del nullifier hash, binding de recipient/fee/refund); constantes `LEVELS=10` y `fee=refund=0` reflejan la variante *no-relayer single-denomination*.  |

La preservación literal del *cryptographic core* es intencional: es
lo que asegura que la simulación tenga las mismas propiedades de
soundness y zero-knowledge que el sistema real que las campañas de
blanqueo explotan en mainnet, requisito para que los *findings* del
Capítulo 8 sean transferibles.

**Uniswap V2** — [`github.com/Uniswap/v2-core`](https://github.com/Uniswap/v2-core) (GPL-2.0).
Referencia algorítmica (no reutilización de código):

| Fichero propio                        | Naturaleza de la referencia                                                                                                                                              |
|---------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `contracts/MockUniswapV2Pool.sol`     | Reimplementación *hand-written* del AMM constant-product con *fee* del 0,3 %. No hay LP tokens; un único bootstrapper siembra la liquidez y a partir de entonces cualquier dirección puede hacer swap. Reservas trackeadas en storage sin sync desde balances. |

### G.2 Primitivas criptográficas y artefactos generados

**snarkjs** — [`github.com/iden3/snarkjs`](https://github.com/iden3/snarkjs), *Copyright 2021 0KIMS association* (GPL-3.0).
Genera automáticamente el verifier on-chain y las pruebas Groth16 off-chain:

- `contracts/Verifier.sol`: contrato `Groth16Verifier` auto-generado
  por `snarkjs zkey export solidityverifier` aplicado al *zkey* del
  circuito `withdraw`. Se distribuye sin modificar bajo la licencia
  GPL-3.0 heredada.
- Uso off-chain: `scripts/zk_helpers.js` y `src/aml/chains/mimc.py`
  invocan `snarkjs.groth16.prove` para producir cada prueba de retiro.

**circomlib / circomlibjs** — [`github.com/iden3/circomlib`](https://github.com/iden3/circomlib) y [`github.com/iden3/circomlibjs`](https://github.com/iden3/circomlibjs) (MIT).
Primitivas hash consumidas por circuito y contrato:

- `circuits/withdraw.circom` incluye `circomlib/circuits/mimcsponge.circom`
  para la permutación Feistel MiMC.
- El bytecode del contrato `MiMCSponge` se genera en tiempo de deploy
  vía `circomlibjs.mimcSpongecontract.createCode("mimcsponge", 220)`
  y se despliega por `src/aml/chains/mimc.py`.

**Hermez Powers of Tau** — ceremonia de trusted setup público
[`github.com/iden3/snarkjs#7-prepare-phase-2`](https://github.com/iden3/snarkjs#7-prepare-phase-2).
El fichero `powersOfTau28_hez_final_*.ptau` (fase 1 universal de
Hermez) se reutiliza para el trusted setup específico del circuito
`withdraw`; no lo generamos nosotros, sino que descargamos y
verificamos el hash oficial. El script `scripts/setup_zk.sh` documenta
el flujo.

**Foundry** — [`github.com/foundry-rs/foundry`](https://github.com/foundry-rs/foundry) (Apache-2.0 o MIT dual).
Toolchain de compilación, testing y despliegue Solidity utilizado para
todos los contratos. Los binarios `forge`, `anvil` y `cast` se
invocan desde los scripts pero no se redistribuyen con el proyecto.

**circom** — [`github.com/iden3/circom`](https://github.com/iden3/circom) (GPL-3.0).
Compilador del lenguaje circom utilizado para compilar
`circuits/withdraw.circom` a R1CS, WebAssembly witness generator y
símbolos.

### G.3 Datasets de terceros

Los cuatro *datasets* utilizados en el Capítulo 8 son externos y se
citan académicamente:

| Dataset                       | Cita académica                                                                                                                                          | Provisión                                                              | Rol en el TFM                              |
|-------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------------------------------|
| **Elliptic** (2019)           | Weber, M., Domeniconi, G., Chen, J. et al. *Anti-Money Laundering in Bitcoin: Experimenting with Graph Convolutional Networks for Financial Forensics*. arXiv 1908.02591. | Kaggle público                                                          | Baseline histórico (referenciado en §8.10)  |
| **Elliptic++** (2023)         | Elmougy, Y., Liu, L. *Demystifying Fraudulent Transactions and Illicit Nodes in the Bitcoin Network*. arXiv 2305.15214.                                | GitHub `git-disl/EllipticPlusPlus`                                     | Sanity check GCN (§8.5.4)                  |
| **EthereumHeist** (2023)      | Wu, J. et al. *Toward Understanding Asset Flows in Crypto Money Laundering through the Lenses of Ethereum Heists*. IEEE TIFS 18: 1994-2009.            | Dropbox + GitHub `HxQlaive/EthereumHeist`                              | Validación externa real (§8.5.3, §8.6.2)   |
| **OpenAML v1** (2025)         | *FINOS OpenAML v1 — DTCC AI Hackathon dataset*. FINOS (Linux Foundation).                                                                              | GitHub `finos/OpenAML` (`training_data.csv`)                           | Sanity check GCN (§8.5.4)                  |

Adicionalmente se referencian sin utilizar directamente:

- **AMLWorld / AMLSim**: Altman, E., Bhattacharyya, A., Chen, C. et al.
  *Realistic Synthetic Financial Transactions for Anti-Money Laundering
  Models*. NeurIPS 2023 Datasets and Benchmarks.
  [`github.com/IBM/AMLSim`](https://github.com/IBM/AMLSim). Citado en el
  Capítulo 2 como *state of the art* de generación sintética AML pero no
  ejecutado en el pipeline propio.

**Datos de precios**. Los ficheros CSV en `data/prices/{eth,trx,usdt}.csv`
son *snapshots* diarios descargados desde la API pública gratuita de
CoinGecko ([`coingecko.com/api`](https://www.coingecko.com/api)) y
cacheados localmente por reproducibilidad. No hay licencia formal
sobre estos datos; el TFM los usa exclusivamente como *market context*
del oráculo determinista descrito en §3.4.

### G.4 Referencias algorítmicas de detectores

Los cuatro detectores de referencia del Capítulo 8 son
implementaciones de algoritmos ampliamente conocidos:

- **GCN** (Kipf, T., Welling, M. *Semi-Supervised Classification with
  Graph Convolutional Networks*. ICLR 2017). Implementado mediante
  `torch_geometric.nn.GCNConv` en `src/aml/detectors/gnn.py`.
- **GAT** (Veličković, P. et al. *Graph Attention Networks*. ICLR 2018).
  Implementado mediante `torch_geometric.nn.GATConv` en
  `src/aml/detectors/gat.py`.
- **EvolveGCN** (Pareja, A. et al. *EvolveGCN: Evolving Graph
  Convolutional Networks for Dynamic Graphs*. AAAI 2020). Referenciado
  en el Capítulo 2 y el ROADMAP; no implementado dentro del scope v1
  del TFM.
- **Louvain** (Blondel, V. D. et al. *Fast unfolding of communities in
  large networks*. J. Stat. Mech. 2008). Consumido vía
  `networkx.algorithms.community.louvain_communities`.

Métricas de evaluación importadas de la literatura:

- **Adjusted Rand Index** — Hubert, L., Arabie, P. *Comparing
  partitions*. Journal of Classification 1985. Consumido vía
  `sklearn.metrics.adjusted_rand_score`.
- **Homogeneidad y Completitud** — Rosenberg, A., Hirschberg, J.
  *V-Measure: A Conditional Entropy-Based External Cluster Evaluation
  Measure*. EMNLP-CoNLL 2007. Consumido vía
  `sklearn.metrics.homogeneity_completeness_v_measure`.

### G.5 Bibliotecas software (dependencies)

Sin ánimo exhaustivo, las bibliotecas software principales sobre las
que se apoya el proyecto:

| Biblioteca                | Licencia         | Rol                                                                     |
|---------------------------|------------------|-------------------------------------------------------------------------|
| **PyTorch**               | BSD-3            | Backend numérico + autograd para los GCN/GAT                            |
| **PyTorch Geometric**     | MIT              | Capas de convolución sobre grafos (`GCNConv`, `GATConv`)                 |
| **NetworkX**              | BSD-3            | Manipulación del grafo etiquetado + Louvain baseline                    |
| **scikit-learn**          | BSD-3            | Métricas ARI / homogeneidad / completitud                                |
| **web3.py**               | MIT              | RPC hacia Anvil / Sepolia + firma de transacciones                       |
| **eth-account**           | MIT              | Generación determinista de wallets burner                                |
| **snarkjs** (npm)         | GPL-3.0          | Generación de pruebas Groth16 off-chain                                  |
| **circomlibjs** (npm)     | MIT              | Bytecode del contrato MiMCSponge on-chain                                |
| **Anthropic SDK**         | MIT              | Cliente HTTP hacia la API de Claude                                      |
| **pytest**                | MIT              | Framework de tests                                                       |

### G.6 Contratos escritos de cero (sin adaptación externa)

Para claridad, los siguientes contratos son *hand-written* sin código
externo importado:

- `contracts/MockUSDT.sol` (63 líneas). ERC-20 mínimo de 6 decimales,
  sin blacklist ni fee-on-transfer, mint permissionless. Sin relación
  con el Tether real.
- `contracts/MockBridge.sol` (94 líneas). Bridge cross-chain
  lock-and-release simplificado sin multisig ni verificación de
  firmas on-chain. Modela únicamente la señal on-chain que un detector
  observaría.
- `contracts/IHasher.sol` y `contracts/IVerifier.sol` (interfaces
  mínimas, ~15 líneas cada una).

### G.7 Licencia del código propio del proyecto

Todo el código propio del proyecto —código Python en `src/aml/`,
scripts CLI en `scripts/`, tests en `tests/`, contratos *hand-written*
listados en §G.6 y el propio TFM en `tfm/`— se libera bajo
**licencia MIT** (fichero `LICENSE` en la raíz del repositorio).
Los ficheros de terceros incluidos en el repositorio conservan su
licencia original (marcada en el *header* SPDX de cada fichero); en
particular, `contracts/Verifier.sol` está bajo GPL-3.0 por herencia
de snarkjs.

Consumidores del código deben respetar el conjunto de licencias
aplicable a los ficheros que utilicen. Un adoptante que redistribuya
sólo los contratos *hand-written* + el código Python está bajo MIT
únicamente; un adoptante que redistribuya el stack ZK completo debe
respetar además la GPL-3.0 del verifier.

## Anexo H — Declaración de uso de Inteligencia Artificial Generativa

> **Nota**: Este anexo cumple con el requisito establecido por la
> Universidad Carlos III de Madrid desde el curso 2024-2025 sobre
> declaración obligatoria del uso de IA Generativa en Trabajos Fin de
> Máster. La plantilla oficial descargable desde
> [`uc3m.libguides.com/TFM/IAGenerativa`](https://uc3m.libguides.com/TFM/IAGenerativa)
> se rellena separadamente; este anexo documenta las prácticas
> subyacentes con mayor detalle.

### H.1 Modelos y herramientas utilizadas

Durante el desarrollo del proyecto se han utilizado los siguientes
modelos de lenguaje grandes (LLM) accedidos vía la API de Anthropic:

- **Claude Opus 4.7** (identificador `claude-opus-4-7`) — uso puntual
  para tareas de razonamiento complejo (revisión metodológica,
  discusión de trade-offs de diseño).
- **Claude Sonnet 4.6** (identificador `claude-sonnet-4-6`) — uso
  principal como asistente de programación bajo la CLI *Claude Code*.
- **Claude Haiku 4.5** (identificador `claude-haiku-4-5-20251001`) —
  uso económico para iteración rápida durante desarrollo.

**Importante**: estos mismos modelos se utilizan también como *objeto
de estudio* dentro del propio sistema descrito en los Capítulos 6
y 7
(Opus como coordinador atacante, Sonnet como sub-agentes tácticos,
todos ellos como coordinadores defensores). Esta doble función
—herramienta de desarrollo y objeto de investigación— se declara
explícitamente para evitar ambigüedad.

### H.2 Tareas para las que se ha utilizado IA generativa

Alineadas con los usos que la política UC3M enumera como
**permitidos**:

**Procesamiento de datos con supervisión y postprocesamiento**:
- Ejecución de scripts de evaluación sobre los *datasets* del proyecto
  (`scripts/eval_llm_defender.py`, `scripts/loco_ethereum_heist.py`,
  etc.) bajo la revisión posterior de todos los resultados numéricos.
- Análisis exploratorio de artefactos generados por las campañas del
  atacante para identificar patrones de refinamiento (§8.9.5).

**Herramienta de desarrollo de código (co-piloto)**:
- Asistencia en la implementación de contratos Solidity adaptados de
  Tornado Cash y en la integración de la toolchain ZK (snarkjs +
  circomlib), siempre bajo revisión y validación manual.
- Asistencia en la implementación del *pipeline* Python (agentes
  atacantes, detectores, particionado federado), siguiendo las
  decisiones arquitectónicas descritas en el Capítulo 6.
- Depuración de errores puntuales durante desarrollo iterativo
  (fixes de reconciliación de árbol Merkle, snarkjs, gas budgeting).

**Refinamiento posterior de la redacción**:
- Revisión gramatical, sintáctica y de coherencia terminológica
  sobre borradores redactados originalmente por el autor. La
  estructura argumental, las decisiones metodológicas y los
  hallazgos empíricos reportados son enteramente atribuibles al
  autor.

**Tareas administrativas**:
- Conversión de los ficheros Markdown de los capítulos a formato
  `.docx` (script `md_to_docx.py`).
- Traducción parcial de documentación auxiliar del proyecto (README,
  ROADMAP, runbook Sepolia) del inglés al español.

### H.3 Tareas para las que NO se ha utilizado IA generativa

Alineadas con los usos que la política UC3M enumera como **no
aceptados**:

**Diseño de la arquitectura del sistema** (Capítulos 6 y 7). El
diseño multi-agente FATF del atacante, el particionado de visibilidad
parcial federada y la asimetría LLM-vs-LLM del defensor son
decisiones del autor.

**Decisiones metodológicas** (Capítulo 9). La selección de *datasets*
(Elliptic++, EthereumHeist, OpenAML v1, simulación propia), la
estrategia de *cross-validation* (LOCO-CV como métrica primaria), la
elección de *baselines* (Louvain, GCN, coseno) y la parametrización
de todos los detectores son decisiones documentadas y justificadas
por el autor.

**Generación de datos experimentales**. Todos los datos reportados en
el Capítulo 8 provienen de: (i) ejecución real del pipeline sobre la
blockchain Anvil local o la testnet Sepolia; (ii) *datasets*
académicos externos citados explícitamente en el Anexo G. **En ningún
caso se han fabricado resultados experimentales mediante IA
generativa**.

**Interpretación de los resultados y hallazgos del Capítulo 8**. Las
conclusiones sobre el trade-off ARI vs interpretabilidad (§8.8), la
auditoría de memorización (§8.10) y las lecciones metodológicas del
Capítulo 10 §10.3 reflejan el análisis crítico del autor sobre los
datos empíricos observados.

### H.4 Trazabilidad y verificabilidad

- El historial completo de commits del repositorio
  [`github.com/0xAnonsal/aml-thesis`](https://github.com/0xAnonsal/aml-thesis)
  preserva la traza de contribuciones asistidas por LLM cuando fueron
  aplicables (metadato `Co-Authored-By` en los mensajes de commit
  durante el periodo de desarrollo activo con asistencia LLM).
- Todos los prompts del sistema utilizados **dentro** del pipeline
  (para el atacante y el defensor, objeto de estudio) están
  publicados en los Anexos A y B de este documento y en los ficheros
  fuente `src/aml/attackers/prompts.py` y
  `src/aml/detectors/multi_agent.py`.
- Los prompts utilizados en la asistencia al desarrollo (Claude Code
  CLI) no se persisten sistemáticamente por el diseño de la
  herramienta, pero el resultado final de cada tarea es revisable en
  el diff del commit correspondiente.

### H.5 Responsabilidad y autoría

Conforme al reglamento UC3M sobre uso de IA en TFM, el autor asume
la **plena responsabilidad y autoría** del trabajo presentado. La IA
generativa se ha utilizado exclusivamente como herramienta
complementaria bajo supervisión humana continua; todas las
decisiones sustantivas (diseño, metodología, interpretación) son
atribuibles personalmente al autor, que responderá ante el tribunal
del TFM por la totalidad del contenido.

## Anexo I — Declaración sobre tratamiento de datos personales (RGPD)

Conforme al anexo 3 de la *Guía Básica de Actuación para los Supuestos
de Tratamiento de Datos Personales en el Proceso de Elaboración de una
Tesis Doctoral o un Trabajo Fin de Titulación* del Delegado de
Protección de Datos de la Universidad Carlos III de Madrid, y al
Reglamento (UE) 2016/679 (RGPD) y a la Ley Orgánica 3/2018 de
Protección de Datos Personales y Garantía de los Derechos Digitales
(LOPDGDD), se declara lo siguiente respecto del tratamiento de datos
personales durante la elaboración, desarrollo y defensa del presente
Trabajo Fin de Máster.

### I.1 Datos del estudiante autor del trabajo

- **Nombre y apellidos**: Saleh Sinawi
- **DNI/NIE**: (según expediente académico)
- **Correo electrónico institucional**: sinawisaleh@gmail.com
- **Titulación**: Máster Universitario en Tecnologías del Sector
  Financiero (FinTech), Universidad Carlos III de Madrid.

### I.2 Datos del Trabajo Fin de Máster

- **Título**: Detección adversarial multi-agente de blanqueo de
  capitales en Ethereum: simulación con agentes LLM y detección
  colaborativa federada bajo visibilidad parcial.
- **Curso académico**: 2025-2026.
- **Fecha de depósito**: Septiembre 2026.

### I.3 Declaración sobre tratamiento de datos personales

Por medio de la presente, y tras haber consultado la Guía Básica del
Delegado de Protección de Datos de la UC3M al respecto:

☐ SÍ se van a tratar datos personales.

**☒ NO se van a tratar datos personales.**

### I.4 Justificación de la declaración negativa

El presente TFM utiliza exclusivamente los siguientes tipos de datos:

1. **Datos sintéticos generados por el autor** — 26 campañas
   attacker + 5 corpus benigno v57 (seeds 300-304) + 5 corpus
   benigno v58 (seeds 400-404). Todas las wallet addresses son
   creadas programáticamente mediante `eth_account.Account.create()`
   con seed determinista; no corresponden a ninguna persona física
   identificada o identificable en el mundo real.

2. **Direcciones de contratos y wallets propias en Sepolia testnet**
   — 6 contratos MockUSDT, MockUniswapV2Pool, MockTornado,
   MockBridge, MiMCSponge, Verifier + una wallet deployer
   (`0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4`) titularidad del
   autor. Sepolia es una testnet pública donde estas addresses
   circulan sin valor monetario real y sin correspondencia con
   identidades KYC; su publicación forma parte del ejercicio
   académico de verificabilidad y no compromete la privacidad de
   ninguna persona.

3. **Datasets académicos públicos previamente publicados y
   seudonimizados** — EthereumHeist (Wu et al. 2023), Elliptic++
   (Bellei et al. 2024), OpenAML v1 (Juvinski et al. 2025). Estos
   datasets fueron construidos y publicados bajo licencia de
   investigación por sus autores originales, quienes ya aplicaron
   procesos de seudonimización y agregación conformes a la práctica
   académica estándar. Las wallet addresses contenidas en ellos NO
   pueden vincularse a identidades reales sin información
   adicional externa (KYC de exchange, subpoena judicial), por lo
   que quedan fuera del ámbito de aplicación estricto del art. 4.1
   RGPD. El uso académico secundario de estos datasets ya
   publicados no constituye recolección o tratamiento originario de
   datos personales por parte del autor del TFM.

4. **Ausencia de recolección directa**. El TFM NO ha realizado en
   ningún momento entrevistas, encuestas, capturas de datos de
   personas físicas identificadas, ni cruces de datos entre las
   wallet addresses de los datasets académicos y bases de datos
   de identidad KYC. Todo el análisis se realiza a nivel agregado
   sobre propiedades topológicas del grafo (in_degree, USDT flow,
   mixer usage) sin perfilado individual.

5. **Ausencia de difusión de datos**. Los datasets utilizados se
   mantienen en el mismo régimen de publicación bajo el que ya
   estaban disponibles; el TFM no incorpora addresses adicionales
   procedentes de fuentes no públicas ni republica los datasets
   originales con cambios que puedan comprometer la seudonimización
   ya aplicada por sus autores.

### I.5 Compromiso del autor

El autor se compromete a:

- Utilizar únicamente los datasets académicos citados bajo los
  términos de licencia declarados por sus autores originales.
- No intentar re-identificar a ninguna persona física a partir de las
  wallet addresses contenidas en los datasets.
- No cruzar los datos on-chain con fuentes externas de KYC o
  identidad real.
- Ante cualquier duda razonable sobre el estatus de un dato como
  personal, consultar al Delegado de Protección de Datos de la
  Universidad Carlos III de Madrid (`dpd@uc3m.es`).

### I.6 Firmas

Firmado en Madrid, a __ de septiembre de 2026.

|                       |                       |
|-----------------------|-----------------------|
| **El/La estudiante**  | **El/La tutor/a**     |
| Saleh Sinawi          | (Firma del tutor)     |
|                       |                       |
| Firma: _____________  | Firma: _____________  |


