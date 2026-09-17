# Glosario de siglas y términos técnicos

Este glosario recoge las siglas y términos técnicos utilizados a lo
largo del TFM, organizados por dominio.

## Regulación y AML

- **AML** (*Anti-Money Laundering*) — normativa contra el blanqueo
  de capitales.
- **CTR** (*Currency Transaction Report*) — umbral de reporte
  regulatorio (10 000 USD en EEUU, ≤999 EUR/USD «sub-CTR» para
  evadirlo).
- **FATF** — Financial Action Task Force. Organismo internacional
  emisor de estándares AML. Rec. 16 = travel rule, Rec. 20 =
  reporting SAR.
- **KYC** (*Know Your Customer*) — proceso de verificación de
  identidad de los usuarios de un exchange.
- **MiCA** — Regulation (EU) 2023/1114 on Markets in Crypto-Assets.
  Vigencia plena en 2027.
- **SAR** (*Suspicious Activity Report*) — reporte de actividad
  sospechosa que un exchange debe emitir a la autoridad competente.

## Blockchain y criptografía

- **ERC-20** — estándar Ethereum para tokens fungibles (USDT, USDC,
  DAI).
- **EVM** — Ethereum Virtual Machine.
- **DEX** — Decentralized Exchange (ej. Uniswap V2).
- **DeFi** — Decentralized Finance.
- **ZK / zk-SNARK** — Zero-Knowledge Succinct Non-interactive
  ARgument of Knowledge. Prueba criptográfica.
- **Groth16** — protocolo ZK específico usado por Tornado Cash.
- **UTXO** — Unspent Transaction Output (modelo Bitcoin).
- **RPC** — Remote Procedure Call (interfaz al nodo Ethereum).
- **Sepolia** — testnet pública de Ethereum, chain_id 11155111.
- **Anvil** — EVM local ephemeral de Foundry.

## Machine Learning y grafos

- **F1** — media armónica de precisión y recall. Métrica binaria.
- **ARI** — Adjusted Rand Index. Métrica de similitud entre dos
  particiones (clusterings).
- **LOCO-CV** — Leave-One-Campaign-Out cross-validation.
- **GCN** — Graph Convolutional Network (Kipf & Welling 2017).
- **GAT** — Graph Attention Network.
- **GNN** — Graph Neural Network (categoría general).
- **RF** — Random Forest.
- **LLM** — Large Language Model.

## Sistema propio y taxonomía interna

- **P1-XX** — identificadores internos de mejoras / iteraciones del
  pipeline (ej. P1-42 = self-sovereign gas propagation, P1-71 =
  post-hoc cluster merge). Todos trazables en commits del repositorio.
- **Placement / Layering / Integration** — las tres fases FATF del
  blanqueo de capitales.
- **Fingerprint** — vector de features 19-dim que describe el
  comportamiento de una dirección (`in_degree`, `log_usdt_in`, etc.).
- **Held-out validation** — evaluación sobre datos genuinamente
  nunca vistos durante desarrollo (seeds 900-901).
- **Multi-campaign LOCO** — variante del LOCO donde múltiples
  campañas se evalúan simultáneamente.

## Datasets

- **EthereumHeist** — dataset de Wu et al. 2023 con 23 hackeos
  reales de mainnet.
- **Elliptic++** — dataset de Elmougy & Liu 2023 sobre Bitcoin.
- **OpenAML v1** — dataset Ethereum del FINOS DTCC Hackathon 2025.
- **AMLWorld** — dataset sintético de Altman et al. NeurIPS 2023.

## Legal / privacidad

- **GDPR / RGPD** — General Data Protection Regulation
  (Reglamento UE 2016/679).
- **LOPDGDD** — Ley Orgánica 3/2018 de Protección de Datos
  Personales y Garantía de los Derechos Digitales.
- **DPO** — Data Protection Officer / Delegado de Protección de
  Datos.

## Institucional

- **UC3M** — Universidad Carlos III de Madrid.
- **TFM** — Trabajo Fin de Máster.
- **BOEL** — Boletín Oficial de la Universidad Carlos III de Madrid.
- **CB / CG / CE** — Competencias Básicas / Generales / Específicas
  del título.

---
