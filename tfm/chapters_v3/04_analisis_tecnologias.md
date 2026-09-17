# Capítulo 4 — Análisis del problema y tecnologías

## 4.1 Análisis del problema

El problema a resolver combina cuatro dimensiones técnicas: (1)
ejecutar transacciones adversariales realistas sobre EVM, (2) generar
pruebas ZK verificables on-chain, (3) entrenar detectores sobre
grafos, y (4) orquestar agentes LLM con tool-use. La sección §4.2
justifica el stack técnico elegido para cada dimensión.

## 4.2 Stack técnico

### 4.2.1 Blockchain (Solidity + Foundry)

- **Solidity 0.8.20** — lenguaje estándar para contratos EVM. La
  versión 0.8+ incluye overflow checks nativos que eliminan una
  clase de bugs típica en versiones anteriores. Compilador `solc`
  invocado vía Foundry.
- **Foundry** (Forge + Anvil + Cast) — toolchain unificado
  compilación + testing + local devnet. Alternativa descartada:
  Hardhat (más lento en compilación, ecosistema Node.js más pesado).
- **Anvil** — EVM local ephemeral con startup ~2s, mine on demand,
  ETH ilimitado. Sandbox principal para iteración rápida.
- **Sepolia** — testnet Ethereum pública. Cada contrato desplegado
  se verifica con `forge verify-contract` para inspección de source
  code en Etherscan.
- **web3.py 6.x** — cliente Python para interactuar con nodo
  Ethereum (Anvil o Sepolia RPC).
- **eth-account 0.10** — generación determinista de wallets y firma
  de transacciones off-chain.

### 4.2.2 Zero-knowledge (Circom + snarkjs)

- **Circom 2.0** — DSL para circuits ZK. El circuit del mezclador
  implementa Merkle-tree verification sobre `commitment(secret,
  nullifier)`. Compila a R1CS.
- **snarkjs 0.7+** — implementación JavaScript del protocolo
  Groth16. Genera `verification_key.json` + contrato `Verifier`
  Solidity. Alternativa descartada: bellman/Rust (curva de
  aprendizaje mayor sin ganancia funcional para el TFM).
- **circomlib / circomlibjs** — biblioteca de gadgets ZK
  reutilizables (MiMCSponge hasher, Poseidon, RSA gadgets).
- **Powers of Tau Hermez** — ceremony pública trusted setup para
  Groth16, phase 1. Se usa el `ptau12` (12-bit) que cubre nuestro
  circuit de 4-bit tree depth con margen.

### 4.2.3 Machine Learning (PyTorch + PyG + scikit-learn)

- **PyTorch 2.4 + CUDA 12.1** — backend para el GCN baseline (Weber
  style). GPU utilizada durante entrenamiento del clasificador.
- **PyTorch Geometric 2.5** — extensión de PyTorch para GNNs.
  Implementa las `GCNConv`, `GATConv` y utilities para procesar
  grafos como tensores.
- **NetworkX 3.x** — manipulación de grafos en Python. Usado por
  `partial_visibility_split` y `extract_features`.
- **scikit-learn 1.4** — Louvain via `community_louvain`, RandomForest,
  métricas F1/ARI/silhouette.

### 4.2.4 APIs LLM (Anthropic Claude)

- **Claude Opus 4.7** — coordinador atacante. Modelo frontera de
  Anthropic con máxima capacidad de razonamiento estructurado y
  tool-use robusto. Coste: ~$15/M input, ~$75/M output.
- **Claude Sonnet 4.6** — evaluación headline del defensor (§8.9.I
  ablation). Coste: ~$3/M input, ~$15/M output.
- **Claude Haiku 4.5** — coordinador defensor bulk (config
  Pareto-óptima). Coste: ~$0.80/M input, ~$4/M output.
- **anthropic-sdk-python 0.34** — cliente oficial, envuelto por
  `LLMClient` para agnosticismo de proveedor.
- **Alternativas evaluadas**: OpenAI GPT-4o (equivalente en
  capacidad, precio similar, elegido Anthropic por preferencia
  personal del autor + tool-use más maduro en Claude 4.x); Google
  Gemini 2 (menos maduro en tool-use durante 2026); Llama
  self-hosted (calidad insuficiente en tareas de razonamiento
  estructurado sobre grafos).

### 4.2.5 Datasets externos

- **Elliptic++** (Elmougy & Liu 2023) — 822k wallets Bitcoin, 1.27M
  interacciones temporales. Repo: `github.com/git-disl/EllipticPlusPlus`.
- **EthereumHeist** (Wu et al. 2023) — 23 hacks reales de mainnet,
  633k nodes, 2.4M transacciones. arXiv:2305.14748.
- **OpenAML v1** (FINOS 2025) — Ethereum AML benchmark del DTCC AI
  Hackathon 2025. Repo: `github.com/finos-labs/dtcch-2025-OpenAML`.
- **AMLWorld** (Altman et al. 2023) — 10⁷ transacciones sintéticas
  sin comportamiento adversarial. Usado como referencia comparativa,
  no como test set principal.

### 4.2.6 Herramientas de desarrollo y despliegue

- **Git + GitHub** — control de versiones + repo público MIT.
- **VS Code + WSL2** — IDE + integración con Ubuntu 22.04 sobre
  Windows 11.
- **conda + pip** — gestión de entornos Python.
- **Etherscan API** — verificación de source code de contratos en
  Sepolia.
- **CoinGecko API** — precios ETH/USDT diarios en cache local.

