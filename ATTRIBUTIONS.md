# Atribuciones de terceros — aml-thesis

Inventario exhaustivo del código, artefactos criptográficos y datasets de terceros integrados en este proyecto. Ver `LICENSE` para la licencia MIT del código propio.

La versión ampliada en español (con contexto académico) vive como **Anexo C** en `tfm/chapters_v3/A_anexos.md`.

---

## Contratos Solidity adaptados

### Tornado Cash — [`tornadocash/tornado-core`](https://github.com/tornadocash/tornado-core) (repositorio GPL-3.0; los ficheros `.sol` llevan cabecera MIT)

| Fichero propio                             | Origen                             | Adaptación                                                                                        |
|--------------------------------------------|------------------------------------|---------------------------------------------------------------------------------------------------|
| `contracts/MockTornado.sol`                | `contracts/ETHTornado.sol`         | Denominación única, sin relayer, comentarios ampliados                                            |
| `contracts/MerkleTreeWithHistory.sol`      | mismo nombre                       | Preservación literal de estructura + hashLeftRight; cambios sólo en comentarios e idioms 0.8+     |
| `circuits/withdraw.circom`                 | mismo nombre                       | Adaptación: witness preservado; profundidad reducida de `Withdraw(20)` a `LEVELS=10`, `fee=refund=0` (sin relayer) |

El *core* criptográfico se preserva con cambios mínimos (profundidad del árbol, sin relayer) para garantizar las mismas propiedades de soundness/zero-knowledge que el sistema real.

### Uniswap V2 — [`Uniswap/v2-core`](https://github.com/Uniswap/v2-core) (GPL-3.0)

| Fichero propio                          | Naturaleza                                                                       |
|-----------------------------------------|-----------------------------------------------------------------------------------|
| `contracts/MockUniswapV2Pool.sol`       | Reimplementación *hand-written* del AMM constant-product, no reutiliza código   |

---

## Primitivas criptográficas y artefactos generados

### snarkjs — [`iden3/snarkjs`](https://github.com/iden3/snarkjs) (GPL-3.0, Copyright 2018-2020 0KIMS association)

- `contracts/Verifier.sol` — auto-generado por `snarkjs zkey export solidityverifier`, distribuido sin modificar bajo GPL-3.0 heredada
- Uso off-chain para generación de pruebas Groth16

### circomlib / circomlibjs — [`iden3/circomlib`](https://github.com/iden3/circomlib) y [`iden3/circomlibjs`](https://github.com/iden3/circomlibjs) (circomlib LGPL-3.0; circomlibjs GPL-3.0)

- Primitiva MiMCSponge consumida por `circuits/withdraw.circom`
- Bytecode del contrato MiMCSponge generado por `circomlibjs.mimcSpongecontract.createCode("mimcsponge", 220)` y desplegado por `src/aml/chains/mimc.py`

### Hermez Powers of Tau — [`iden3/snarkjs#7-prepare-phase-2`](https://github.com/iden3/snarkjs#7-prepare-phase-2)

- Fichero `powersOfTau28_hez_final_*.ptau` (fase 1 universal Hermez) reutilizado como trusted setup del circuito withdraw
- `scripts/setup_zk.sh` documenta el flujo de descarga y verificación

### Foundry — [`foundry-rs/foundry`](https://github.com/foundry-rs/foundry) (Apache-2.0 / MIT dual)

- Toolchain de compilación, testing y despliegue Solidity (`forge`, `anvil`, `cast`)
- Binarios invocados por scripts, no redistribuidos

### circom — [`iden3/circom`](https://github.com/iden3/circom) (GPL-3.0)

- Compilador del lenguaje circom para producir R1CS + WASM witness generator

---

## Datasets académicos

| Dataset            | Cita                                                                                                                                                       | Provisión                                          | Rol                                       |
|--------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------|-------------------------------------------|
| **Elliptic**       | Weber et al. 2019, *Anti-Money Laundering in Bitcoin*, arXiv 1908.02591                                                                                    | Kaggle público                                     | Baseline histórico                        |
| **Elliptic++**     | Elmougy & Liu 2023, *Demystifying Fraudulent Transactions and Illicit Nodes in the Bitcoin Network*, arXiv 2306.06108 (KDD 2023)                                     | GitHub `git-disl/EllipticPlusPlus`                 | Sanity check del pipeline (RF/LR)         |
| **EthereumHeist**  | Wu et al. 2023, *Toward Understanding Asset Flows in Crypto Money Laundering...*, IEEE TIFS 19: 1994-2009 (2024)                                                | Dropbox (enlace en el artículo)                    | Validación externa real                   |
| **OpenAML v1**     | FINOS OpenAML v1 (DTCC AI Hackathon dataset), Linux Foundation                                                                                             | GitHub `finos-labs/dtcch-2025-OpenAML` (`Project_DTCC_AI_Hackathon/data/processed.csv`, 45 086 wallets) | Sanity check del pipeline (RF/LR)         |
| **AMLWorld/AMLSim**| Altman et al. 2023, NeurIPS Datasets and Benchmarks                                                                                                        | GitHub `IBM/AMLSim`                                | Referenciado (no ejecutado)               |

**Datos de precios**: snapshots diarios de CoinGecko API pública gratuita, cacheados en `data/prices/*.csv` para reproducibilidad.

---

## Algoritmos de referencia (baselines implementados)

- **GCN** — Kipf & Welling 2017, ICLR — implementado via `torch_geometric.nn.GCNConv`
- **GAT** — Veličković et al. 2018, ICLR — implementado via `torch_geometric.nn.GATConv`
- **EvolveGCN** — Pareja et al. 2020, AAAI — referenciado, no implementado
- **Louvain** — Blondel et al. 2008 — consumido via `networkx.algorithms.community.louvain_communities`

### Métricas
- **ARI** — Hubert & Arabie 1985 — via `sklearn.metrics.adjusted_rand_score`
- **Homogeneidad / Completitud** — Rosenberg & Hirschberg 2007 — via `sklearn.metrics.homogeneity_completeness_v_measure`

---

## Bibliotecas software

| Biblioteca             | Licencia   | Rol                                                    |
|------------------------|------------|--------------------------------------------------------|
| PyTorch                | BSD-3      | Backend numérico + autograd para GCN/GAT               |
| PyTorch Geometric      | MIT        | Capas de convolución sobre grafos                       |
| NetworkX               | BSD-3      | Manipulación de grafos + Louvain baseline               |
| scikit-learn           | BSD-3      | Métricas ARI/homogeneidad/completitud                   |
| web3.py                | MIT        | RPC hacia Anvil/Sepolia + firma de transacciones        |
| eth-account            | MIT        | Generación determinista de wallets                      |
| snarkjs (npm)          | GPL-3.0    | Generación de pruebas Groth16 off-chain                 |
| circomlibjs (npm)      | GPL-3.0    | Bytecode del contrato MiMCSponge on-chain                |
| Anthropic SDK          | MIT        | Cliente HTTP para llamar a los modelos Claude          |
| pytest                 | MIT        | Framework de tests                                     |

---

## Contratos escritos de cero (sin adaptación externa)

- `contracts/MockUSDT.sol` — ERC-20 mínimo 6 decimales, hand-written
- `contracts/MockBridge.sol` — bridge cross-chain simplificado, hand-written
- `contracts/IHasher.sol` y `contracts/IVerifier.sol` — interfaces mínimas

---

## Reglas de redistribución

- Redistribuir sólo código propio (Python + hand-written contracts + docs) → MIT únicamente
- Redistribuir el stack ZK completo (incluyendo `Verifier.sol`) → respetar además GPL-3.0 heredada de snarkjs
- Redistribuir circuitos que dependen de MiMCSponge → respetar LGPL-3.0 de circomlib y GPL-3.0 de circomlibjs
