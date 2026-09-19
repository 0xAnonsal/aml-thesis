# Capítulo 3 — Análisis del problema y tecnologías

Este capítulo cataloga las tecnologías —contratos inteligentes, primitivas
criptográficas, bibliotecas de *machine learning*, herramientas de
desarrollo *blockchain* y APIs LLM— empleadas para construir el sistema.
Cada entrada indica su rol funcional dentro del pipeline, la versión
utilizada y la licencia bajo la que se distribuye. El detalle exhaustivo
de las atribuciones de código de terceros vive en el §Anexo G; este
capítulo se centra en las tecnologías como *conjunto operativo* que
justifica las decisiones arquitectónicas del Capítulo 6.

## 3.1 Stack *blockchain* (capa de contratos y ejecución)

### 3.1.1 Solidity 0.8.20

Lenguaje de contratos inteligentes objetivo de EVM (Ethereum Virtual
Machine) empleado para los seis contratos *hand-written* del sistema.
La versión 0,8 aporta *overflow checks* automáticos en operaciones
aritméticas —crítico para contratos que gestionan valor— y sintaxis
moderna que reduce boilerplate frente a 0,7. Los contratos usan
únicamente características estándar del lenguaje sin dependencias
externas (`OpenZeppelin`, `Uniswap V2 core`) para minimizar la
superficie de código, justificado en el Capítulo 6 §7.1.

### 3.1.2 Foundry (toolchain compilación + ejecución)

[`github.com/foundry-rs/foundry`](https://github.com/foundry-rs/foundry),
licencia Apache-2.0 / MIT dual. Suite de herramientas Rust que
proporciona:

- **`forge`**: compilador Solidity con caché incremental + framework
  de tests unitarios escrito en el propio Solidity (usados para las
  ~40 pruebas de contrato en `test/*.t.sol`).
- **`anvil`**: nodo EVM local en memoria con soporte para bloques
  automáticos o manuales (usados para simular latencia). Ephemeral —
  el estado se destruye al terminar el proceso. Fondea diez cuentas
  con 10 000 ETH cada una al arrancar.
- **`cast`**: cliente CLI para consultas de bajo nivel (usado en
  scripts de despliegue Sepolia para verificar bytecode publicado).

Se prefirió Foundry sobre Hardhat por: (i) compilación ≈ 10×
más rápida; (ii) integración nativa con Anvil sin proceso separado;
(iii) sin dependencia de Node.js para tests del lado contrato.

### 3.1.3 Anvil (EVM local ephemeral)

Subcomponente de Foundry. Backend principal para la evaluación §5
del Capítulo 5 (Implementación y pruebas). Ventajas frente a Sepolia
para desarrollo: coste 0, velocidad de bloque instantánea (o
controlada mediante `advance_blocks`), reproducibilidad byte-idéntica
entre corridas del mismo seed.

### 3.1.4 Sepolia (testnet Ethereum pública)

`chain_id = 11155111`. Testnet oficial post-*The Merge* mantenida por
la fundación Ethereum. Empleada como validación externa on-chain
verificable por terceros (Capítulo 5 §5.6). *Provider* RPC: Alchemy
free tier para envío de transacciones; `publicnode.com` para
`eth_getLogs` paginados (Alchemy free tier limita el rango a 10
bloques por *request*).

### 3.1.5 web3.py 6.x

[`github.com/ethereum/web3.py`](https://github.com/ethereum/web3.py),
licencia MIT. Cliente Python para JSON-RPC Ethereum. Utilizado en
todo `src/aml/chains/` y `src/aml/attackers/tools.py` para: consulta
de balances, firma de transacciones con `eth_account`, envío al
mempool, espera de *receipts*, y decodificación de eventos on-chain.
Compatible tanto con Anvil como con Sepolia mediante la misma
abstracción `Web3(HTTPProvider(url))`.

### 3.1.6 eth-account 0.10

[`github.com/ethereum/eth-account`](https://github.com/ethereum/eth-account),
licencia MIT. Firmado local de transacciones sin envío. Utilizado
para generar wallets *burner* deterministas (con seed) o aleatorias
(con `secrets.token_bytes`) y firmar transacciones antes de enviarlas
al RPC.

## 3.2 Stack *zero-knowledge*

### 3.2.1 circom 2.0

[`github.com/iden3/circom`](https://github.com/iden3/circom), licencia
GPL-3.0. Lenguaje de dominio específico para escribir circuitos
aritméticos que se compilan a R1CS (Rank-1 Constraint System).
Utilizado para el circuito `withdraw.circom` del mezclador ZK
adaptado de Tornado Cash. La sintaxis usa signals públicos y privados
que definen la matemática que la prueba Groth16 satisface.

### 3.2.2 snarkjs 0.7+

[`github.com/iden3/snarkjs`](https://github.com/iden3/snarkjs), licencia
GPL-3.0, Copyright 2021 0KIMS association. Toolchain JavaScript para
generar pruebas Groth16 off-chain más el verifier Solidity on-chain:

- **`snarkjs groth16 prove`**: consume el *zkey* del circuito + el
  *witness* específico de la instancia (nullifier, secret, path
  Merkle) y produce la prueba en ≈ 5 segundos.
- **`snarkjs zkey export solidityverifier`**: auto-genera el contrato
  `Verifier.sol` (196 líneas) con la función `verifyProof` que evalúa
  el emparejamiento Groth16 sobre la curva BN128 on-chain.

Se ejecuta via Node.js desde el script `scripts/zk_helpers.js`
invocado por `src/aml/chains/mimc.py` y por el atacante en tiempo de
`mixer_withdraw`.

### 3.2.3 circomlib / circomlibjs

[`github.com/iden3/circomlib`](https://github.com/iden3/circomlib) +
[`github.com/iden3/circomlibjs`](https://github.com/iden3/circomlibjs),
licencia MIT. Biblioteca de primitivas criptográficas para circom.
Se consume únicamente `mimcsponge.circom` (permutación Feistel MiMC
con constantes fijas) tanto en el circuito de retirada como
off-chain para calcular el commitment de cada depósito. El bytecode
del contrato `MiMCSponge` on-chain se genera dinámicamente por
`circomlibjs.mimcSpongecontract.createCode("mimcsponge", 220)` y se
despliega mediante `src/aml/chains/mimc.py`.

### 3.2.4 Powers of Tau Hermez

Ceremonia de *trusted setup* pública para curvas BN128
[`github.com/iden3/snarkjs#7-prepare-phase-2`](https://github.com/iden3/snarkjs#7-prepare-phase-2).
Se reutiliza el fichero final `powersOfTau28_hez_final_*.ptau` como
fase 1 universal; el setup específico del circuito `withdraw`
(fase 2) se ejecuta localmente por `scripts/setup_zk.sh` en ≈ 30
segundos.

## 3.3 Stack de aprendizaje automático

### 3.3.1 PyTorch 2.4 + CUDA 12.1

[`github.com/pytorch/pytorch`](https://github.com/pytorch/pytorch),
licencia BSD-3. Backend numérico + autograd para las redes neuronales
sobre grafos. Compatible con GPU NVIDIA (utilizada para entrenamiento
de GCN sobre el *dataset* Elliptic ≈ 200 000 nodos) y CPU
(usada para GCN sobre los subgrafos federados ≈ 3 000-10 000
nodos donde el overhead GPU no compensa).

### 3.3.2 PyTorch Geometric 2.5

[`github.com/pyg-team/pytorch_geometric`](https://github.com/pyg-team/pytorch_geometric),
licencia MIT. Extensión de PyTorch para redes neuronales sobre
grafos. Aporta capas convolucionales especializadas (`GCNConv`
para Kipf & Welling 2017; `GATConv` para Veličković et al. 2018)
y la representación `Data(x, edge_index, y)` para grafos etiquetados.
Consume módulos auxiliares `torch-scatter` y `torch-sparse`
instalados via `--find-links` sobre las wheels específicas de
CUDA 12.1.

### 3.3.3 NetworkX 3.x

[`github.com/networkx/networkx`](https://github.com/networkx/networkx),
licencia BSD-3. Manipulación de grafos de propósito general. Utilizado
para: (i) el detector baseline `LouvainDetector` mediante
`networkx.algorithms.community.louvain_communities`; (ii) la
representación intermedia `MultiDiGraph` etiquetado que sirve tanto
al pipeline propio como al conversor a `torch_geometric.data.Data`;
(iii) el particionado federado `partial_visibility_split`.

### 3.3.4 scikit-learn 1.5

[`scikit-learn.org`](https://scikit-learn.org), licencia BSD-3.
Métricas de clustering (`adjusted_rand_score`,
`homogeneity_completeness_v_measure`) y utilidades de división train/
val/test (`train_test_split` con `stratify` + `random_state`
determinista).

### 3.3.5 NumPy + pandas + matplotlib + seaborn

Stack numérico estándar. NumPy para vectorización de las 19 features
por nodo; pandas para carga de CSVs de precios; matplotlib + seaborn
para las figuras del §Anexo (grafos de campañas atacantes en
`src/aml/detectors/viz.py`).

## 3.4 APIs LLM (agentes ofensivo y defensivo)

### 3.4.1 Anthropic SDK (paquete `anthropic`)

[`github.com/anthropics/anthropic-sdk-python`](https://github.com/anthropics/anthropic-sdk-python),
licencia MIT. Cliente Python para la API de Anthropic. Utilizado
para las llamadas a los tres modelos empleados como
*herramienta metodológica* dentro del sistema:

- **Claude Opus 4.7** (`claude-opus-4-7`): Coordinador del atacante
  y modelo *headline* del defensor cuando se busca calidad máxima
  de razonamiento. Precio: ≈ \15/Mtokens input,~ \75/M
  tokens output.
- **Claude Sonnet 4.6** (`claude-sonnet-4-6`): sub-agentes del
  atacante (Placement, Layering, Integration) y opción *balanced*
  del coordinador defensor. Precio: ≈ \3/Minput,~ \15/M
  output.
- **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`): tier económico
  para iteración durante desarrollo y sensibilidad de coste del
  defensor. Precio: ≈ $0,25/Minput,~ \1,25/M
  output.

El SDK gestiona autenticación por *API key*, versionado de la API,
*retry* automático ante errores transitorios (429 rate limit, 500),
y serialización de respuestas. La aplicación lo encapsula tras un
*wrapper* propio (`src/aml/attackers/llm_client.py`) que uniforma
el cálculo de coste USD por modelo y permite inyectar un mock en
tests unitarios.

### 3.4.2 CoinGecko API (oráculo de precios)

[`coingecko.com/api`](https://www.coingecko.com/api). Endpoint público
gratuito consumido por `scripts/download_prices.py` para obtener
histórico diario OHLC de ETH, USDT y TRX desde 2018. Los datos se
cachean localmente en `data/prices/*.csv` para reproducibilidad
byte-idéntica entre corridas —la corrida no hace llamada *live* a
CoinGecko en ningún caso, evitando dependencias de red y *rate
limits*—.

## 3.5 Herramientas de desarrollo, testing y despliegue

### 3.5.1 pytest 7+

Framework de tests para el paquete Python. Suite de más de 340
tests en `tests/` organizados por módulo (`test_dataset.py`,
`test_detectors_baselines.py`, `test_coordinator.py`, etc.), con
cobertura > 85 %. Los tests estructurales corren en
≈ 10 segundos sin dependencias externas; los tests con Anvil
requieren `foundry` instalado; los tests *live* con LLM requieren
`ANTHROPIC_API_KEY` y consumen ≈ $0,30 de créditos por
corrida.

### 3.5.2 conda + pip

Gestión de entornos Python. El fichero `environment.yml` fija Python
3.11 + PyTorch 2.4 + CUDA 12.1 y las dependencias de canal
`conda-forge`; las dependencias sensibles a la versión de CUDA
(PyTorch Geometric + extensiones) se instalan via `pip --find-links`
sobre las wheels específicas.

### 3.5.3 python-docx

[`github.com/python-openxml/python-docx`](https://github.com/python-openxml/python-docx),
licencia MIT. Generación del *draft* del TFM en formato `.docx` a
partir de los ficheros Markdown de los capítulos, mediante el script
`scratchpad/md_to_docx.py`. Alternativa a pandoc (que requiere
instalación de LaTeX + XeLaTeX para PDFs de calidad) sin dependencias
del sistema.

### 3.5.4 GitHub + `gh` CLI

Alojamiento del repositorio + gestión de PRs. El comando
`gh pr merge` se emplea para integraciones a `main` desde ramas
feature (documentado en el CHANGELOG del repositorio). El repositorio
público está en
[`github.com/0xAnonsal/aml-thesis`](https://github.com/0xAnonsal/aml-thesis).

## 3.6 Datasets externos

Se emplean cuatro *datasets* académicos externos citados en el
§Anexo G:

- **Elliptic** (Weber et al. 2019, Kaggle público): baseline
  histórico sobre Bitcoin.
- **Elliptic++** (Elmougy & Liu 2023, GitHub `git-disl/EllipticPlusPlus`):
  extensión a nivel de wallet.
- **EthereumHeist** (Wu et al. 2023, IEEE TIFS 18): 23 casos reales
  de hackeos Ethereum mainnet.
- **OpenAML v1** (FINOS 2025, GitHub `finos/OpenAML`): 34 000
  wallets Ethereum etiquetadas por el consorcio DTCC / Linux
  Foundation.

Los detalles de cada *dataset* (tamaños, features, uso en el
pipeline) se desarrollan en el Capítulo 9.

## 3.7 Resumen del stack

| Capa                  | Tecnologías                                            |
|-----------------------|--------------------------------------------------------|
| Contratos             | Solidity 0.8.20, Foundry, Anvil, Sepolia                |
| Zero-knowledge        | circom 2.0, snarkjs, circomlib, Powers of Tau Hermez    |
| Cliente blockchain    | web3.py 6.x, eth-account 0.10                           |
| ML sobre grafos       | PyTorch 2.4, PyTorch Geometric 2.5, NetworkX 3.x, scikit-learn |
| Numérico              | NumPy, pandas, matplotlib, seaborn                      |
| LLM agents            | Anthropic SDK (Opus 4.7 / Sonnet 4.6 / Haiku 4.5)       |
| Oracle precios        | CoinGecko API + caché CSV local                         |
| Tests                 | pytest 7+ (> 340 tests, cobertura > 85 %)     |
| Entornos              | conda + pip                                             |
| Documentación         | Markdown + python-docx                                  |
| Versionado            | git + GitHub + `gh` CLI                                 |

Este stack se mantiene deliberadamente conservador: cada tecnología es
madura (> 2 años de estabilidad), open source, y su reemplazo
por una alternativa comparable requeriría cambio mínimo del código
(en particular el *wrapper* LLM que permite sustituir Anthropic por
OpenAI/Google/etc. con reimplementación de una única interfaz —ver
Capítulo 6 §7.6).
