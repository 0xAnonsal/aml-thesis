# TFM AML Multi-Agente — Roadmap del proyecto

> **Documento de planning inicial (mayo 2026)** preservado por trazabilidad.
> El estado final y los resultados del proyecto están en el TFM completo en
> `tfm/chapters/`. Los cronogramas y estados de este documento son históricos.

**Título de trabajo:** *Sistemas adversariales multi-agente para blanqueo de capitales en criptoactivos: generación red-team y detección colaborativa sobre Ethereum, con detección de laundering en stablecoins y smurfing*

---

## 1. Planteamiento del problema

Los detectores AML existentes para transacciones cripto —predominantemente redes neuronales de grafos entrenadas sobre datasets etiquetados como Elliptic— se evalúan contra transacciones ilícitas históricas o perturbaciones sintéticas adversariales simples. Quedan dos gaps abiertos:

1. **Generación adversarial realista.** Los ataques basados en gradientes producen perturbaciones de transacciones que satisfacen objetivos de evasión ML pero no son realistas tipológicamente. No reflejan cómo operan las organizaciones reales de laundering: roles divididos, patrones reconocidos por FATF (placement / layering / integration), uso de mezcladores, smurfing entre múltiples wallets controladas, **denominación en stablecoin (USDT) — el perfil dominante de flujos ilícitos reportado por Chainalysis 2024–2025.**
2. **Detección colaborativa bajo restricciones de privacidad, con clustering a nivel de actor.** El AML del mundo real requiere que múltiples exchanges compartan señales de sospecha sobre vistas parciales del grafo de transacciones, pero restricciones legales y jurisdiccionales (GDPR, Directiva AML, MiCA) impiden el pooling de datos crudos. La mayoría de detectores publicados asumen visibilidad completa del grafo *y* operan a granularidad per-address — flageando direcciones individuales como ilícitas. El laundering real utiliza muchas wallets burner de vida corta por campaña; la tarea de detección es por tanto **identificar qué direcciones pertenecen al mismo actor**, no sólo clasificar cada dirección aislada.

Esta tesis aborda ambos gaps construyendo (a) un blanqueador multi-agente basado en LLM que genera patrones de ataque tipológicamente realistas sobre un entorno Ethereum simulado —enfocado en flujos denominados en USDT y un mezclador zero-knowledge real— y (b) un detector colaborativo multi-agente basado en LLM que opera sobre vistas parciales del grafo, realiza clustering a nivel de actor de wallets relacionadas (detección de smurfing), y respeta límites explícitos de privacidad entre agentes exchange simulados.

---

## 2. Novedad (requisito de *novedad*)

### Trabajo previo / baselines contra los que comparar

| Referencia | Contribución | Uso |
|---|---|---|
| Weber et al. 2019 (*Anti-Money Laundering in Bitcoin*) | Dataset Elliptic + baseline GCN | Detector víctima |
| Pareja et al. 2020 (*EvolveGCN*) | GNN temporal para grafos evolutivos | Detector víctima |
| Cardoso et al. 2022 | Benchmarks GNN AML sobre Bitcoin | Referencia metodológica |
| Egressy et al. 2023 | Ataques adversariales sobre GNN AML (gradiente) | Baseline atacante |
| Altman et al. 2023 (*IBM AMLworld / AMLSim*) | Simulador AML sintético con ground truth | Entorno |

### Contribuciones novel específicas

1. **Primer sistema multi-agente LLM que genera ataques de laundering tipológicamente fundamentados** en Ethereum (roles FATF placement-layering-integration + smurfing multi-wallet) en lugar de perturbaciones basadas en gradientes.
2. **Evaluación contra ataques que combinan uso real de mezclador ZK (Tornado-style con Groth16) y laundering en stablecoin (USDT-ERC20)** — reflejando el perfil dominante de flujos ilícitos 2024–2025 según Chainalysis. La mayor parte de la literatura académica AML sigue focalizada en flujos transparentes nativos Bitcoin; las combinaciones stablecoin + privacy-mixer están infrarrepresentadas.
3. **Primer detector colaborativo multi-agente que realiza clustering a nivel de actor (smurfing / identificación de wallets relacionadas) bajo visibilidad parcial del grafo.** La mayoría de detectores publicados clasifican direcciones individualmente; la tarea más difícil y prácticamente relevante es reconocer que las direcciones A, B, C, D pertenecen al mismo blanqueador a pesar de no tener enlace directo on-chain, viendo sólo un subconjunto del grafo global de transacciones (la vista legal-realista a nivel de exchange).

---

## 3. Metodología

### 3.1 Entornos

| Activo / fuente | Fuente | Uso |
|---|---|---|
| Ethereum | Fork local vía Anvil/Hardhat. Contratos mock desplegados en el fork: ERC-20 estilo USDT (6 decimales), pool swap estilo Uniswap-V2 (USDT↔ETH), mezclador ZK real (estilo Tornado: verifier Groth16 + commitments Pedersen + árbol Merkle MiMC). | Experimentos de laundering en stablecoin y mezclador |
| Bitcoin | Dataset Elliptic / Elliptic++ | Entrenar detectores baseline víctima únicamente — Bitcoin **no** es objetivo de ataque en esta tesis. Sirve como fundación de datos etiquetados para las baselines GNN que el atacante LLM intenta evadir. |
| Transacciones ETH reales (opcional) | Muestreo del archivo Etherscan (free tier) | Tráfico de fondo realista para el training set del defender exchange-agent, mezclado con transacciones sintéticas del blanqueador |

Más allá de los entornos cadena descritos, un **oráculo histórico de precios** compartido provee OHLC horario para ETH y USDT vs USD hasta 2018, vía el free tier de CoinGecko y pre-descargado a un caché CSV local para reproducibilidad. El oráculo se describe como servicio en §3.2; tanto atacante como defensor lo llaman como herramienta, pero no es en sí mismo un agente LLM.

### 3.2 Arquitectura

**Atacante (Blanqueador) — multi-agente LLM:**
- *Coordinador* (Claude Opus 4.7): planifica la campaña de laundering, asigna volumen entre roles, genera el conjunto de wallets burner usadas por la campaña (`secrets.token_bytes` + `eth_keys`), observa feedback del detector.
- *Agente Placement* (Claude Sonnet 4.6): orquesta depósitos iniciales / ofuscación on-ramp, denominado en USDT donde realista.
- *Agente Layering* (Claude Sonnet 4.6): genera peel chains, swaps de token (USDT↔ETH para acceso al mezclador), depósitos y retiradas del mezclador ZK a direcciones burner nuevas, patrones smurfing dimensionados bajo los umbrales USD FATF.
- *Agente Integration* (Claude Sonnet 4.6): selecciona rampas de salida y cadencia de retirada.

**Defensor — multi-agente LLM:**
- *N agentes exchange* (Claude Sonnet 4.6 cada uno): cada uno ve un subgrafo parcial (depósitos/retiradas en "su" exchange). Computa features de sospecha per-dirección localmente.
- *Coordinador* (Claude Opus 4.7): agrega señales de sospecha entre exchanges y realiza **clustering a nivel de actor** — infiere qué conjuntos de direcciones probablemente están controlados por el mismo blanqueador basado en origen de financiación común, co-ocurrencia temporal, sinks downstream compartidos, fingerprints gas-price / nonce, y estructura de comunidad del grafo. Emite alertas a nivel de cluster, no de dirección.
- *Restricción de privacidad:* los agentes exchange comparten sólo scores de sospecha y resúmenes de features — nunca grafos de transacciones crudos.

**Detectores baseline víctima (off-the-shelf, usados como señal de recompensa del atacante):**
- GCN, GAT, EvolveGCN entrenados sobre Elliptic + AMLworld
- Reproducir F1 / precision / recall publicados antes de cualquier experimento de ataque

**Infraestructura compartida (usada por atacante y defensor — NO es agente LLM):**
- *PriceOracle*: servicio Python determinista que mapea `(activo, timestamp) → precio USD`. Respaldado por caché CSV pre-descargado de CoinGecko (OHLC horario para ETH y USDT, 2018–presente).
  - El atacante lo usa para dimensionar cada leg de laundering bajo los umbrales FATF USD (ej. umbral smurfing de $10k), elegir USDT vs ETH para holding value-stable durante layering, y timing de rampas de salida relativas a movimientos recientes de precio.
  - El defensor lo usa para normalizar en USD flujos observados entre activos heterogéneos y aplicar reglas de sospecha denominadas en USD.
  - El pricing se trata como **dato, no estrategia**: el oráculo es una tool Python que los agentes LLM llaman. NO gastamos tokens LLM en price lookups, y no hay agente "pricing" dedicado en ninguna cara.
  - USDT default: $1.00. Eventos de depeg configurables para ablation.

### 3.3 Optimización de coste

- Desarrollar con sub-agentes Haiku 4.5 (~10× más barato que Sonnet)
- Experimentos finales del paper con sub-agentes Sonnet 4.6 + coordinadores Opus 4.7
- Prompt caching agresivo sobre system prompts y definiciones de tools (~90 % reducción coste input en cache hits)
- LLM sólo en decisiones estratégicas; Python determinista ejecuta transacciones individuales
- Presupuesto total estimado: $600–2 000 para toda la tesis

---

## 4. Cronograma (12 semanas)

| Semana | Fase | Estado |
|---|---|---|
| 1 | Setup + datos: conda env, Elliptic + AMLworld descargados, notebook EDA | ✅ hecho |
| 2 | Detectores baseline: GCN F1=0.476, GAT F1=0.480 sobre Elliptic (batió Weber 2019); servicio PriceOracle | ✅ hecho |
| 3 | Simulador Ethereum: Anvil + cuatro contratos mock (USDT, Uniswap-pool, Tornado, Bridge) — 26 tests de integración pasando | ✅ hecho |
| 4 | Upgrade ZK Tornado: reemplazar mezclador keccak-mock con Groth16 + Pedersen + Merkle tree reales (circuito circom, setup snarkjs, Verifier.sol generado, wrapper Python de la prueba) | ✅ hecho |
| 5–6 | Agentes atacantes v1: Coordinator + 3 sub-agentes, smurfing multi-burner-wallet, swaps USDT/ETH, uso real del mezclador ZK, evadir baseline GCN single | ✅ hecho |
| 7 | Evaluación atacante: Attack Success Rate, scoring de realismo tipológico, ASR ponderado por volumen USD | ✅ hecho |
| 8–9 | Agentes defensor: detector multi-exchange partial-view con clustering a nivel de actor (smurfing / identificación de wallets relacionadas) | ✅ hecho |
| 10 | Batallas end-to-end: atacante vs defensor, múltiples seeds, ablations (con/sin uso mezclador, con/sin smurfing-clustering, con/sin timing price-aware) | ✅ hecho |
| 11 | Análisis: análisis de coste, análisis de errores, comparativa vs ataques gradient, breakdown per-activo | ✅ hecho |
| 12 | Writeup | ✅ hecho |

El baseline EvolveGCN se difirió a un PR separado pequeño alongside semana 5 (la forma de datos de temporal-snapshot es distinta a la de GCN/GAT estático).

---

## 5. Métricas de evaluación

**Lado atacante:**
- Attack Success Rate (ASR): proporción de campañas de laundering no detectadas por el modelo víctima.
- **ASR ponderado por volumen USD**: ASR ponderado por el volumen USD-equivalente exitosamente laundered, así una campaña que mueve $1M domina sobre una que mueve $1k. Previene que la cifra headline se infle por muchos éxitos de bajo valor.
- Realismo tipológico: scoring automatizado contra tipologías FATF + spot-check por el advisor.
- Coste por evasión exitosa (spend API USD / campaña exitosa).

**Lado defensor:**
- **Tasa de detección a nivel de dirección** (vs atacante gradient-baseline, para comparación directa con las cifras de Egressy 2023 y Cardoso 2022).
- **F1 a nivel de actor (cluster)**: de los clusters ground-truth de wallets controladas por el blanqueador en cada campaña, ¿qué fracción identifica el coordinador del defensor como un único cluster relacionado? Esta es la métrica headline de la novedad #3.
- Tasa de falsos positivos sobre transacciones benignas y clusters de direcciones benignas.
- Eficiencia de sharing de información (señales de sospecha intercambiadas por detección).
- Precision/recall USD-equivalente: precision/recall ponderado por valor USD de flujos correctamente flageados.

**Comparativas:**
- Atacante LLM vs atacante gradient estilo PGD sobre el mismo modelo víctima.
- Defensor multi-agente vs baseline single-model GCN (nivel dirección).
- Defensor multi-agente vs baseline single-model GCN + post-hoc graph-clustering (nivel cluster — esta es la comparativa significativa para la contribución #3).
- Breakdown per-activo (ETH nativo vs USDT) para volumen de laundering y detección.
- **Ablation timing price-aware**: blanqueador con acceso completo al PriceOracle vs precios ocultos — ¿cambia mesurablemente el ASR el price-awareness intra-campaña?
- **Ablation detección smurfing**: defensor con vs sin subrutina de clustering a nivel de actor — ¿supera el clustering explícito a la clasificación per-address + agrupación naive?

---

## 6. Ética y alcance legal

- **Todo el trabajo sobre datos sintéticos y un fork Anvil local. Nunca fondos reales en mainnet.**
- Tornado Cash está sancionado por OFAC en Estados Unidos. Desplegamos un mock research escrito a mano (mock primero, upgrade ZK real en semana 4) en el fork local — nunca los contratos live desplegados en mainnet.
- El mock USDT-ERC20 es un artefacto research sin relación con el contrato Tether real; se despliega sólo en el fork Anvil local.
- El scaffold del contrato MockBridge existe para la extensión futura cross-chain (ver §8) pero no se ejercita en los experimentos v1.
- Filing del comité de ética en semana 1. **Action item: confirmar con el advisor qué requiere tu universidad (IRB / CEI / equivalente).**
- Todo el código atacante claramente etiquetado como artefacto research; no empaquetado para redistribución. Licencia: research-use-only (específica TBD con el advisor).

---

## 7. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| 4 GB VRAM GPU (GTX 1650 Ti) insuficiente para entrenamiento completo GNN Elliptic | Confirmado como aceptable: GCN entrena en 25 s, GAT en 220 s sobre el grafo completo a 1000 épocas |
| Costes API LLM exceden presupuesto | Caps estrictos por episodio de experimento; Haiku en dev; prompts cacheados; LLM sólo en decisiones estratégicas |
| Setup ZK (semana 4) tarda más de lo planeado | Reutilizar Hermez phase-1 trusted setup (`powersOfTau28_hez_final_*.ptau`); el circuito estilo Tornado está bien documentado y adaptamos en vez de diseñar desde cero; el mezclador keccak-mock existente sigue siendo fallback si ZK se pasa |
| Detección smurfing falla en batir baselines naive | Comparar contra baseline post-hoc graph-clustering (Louvain, Leiden) en el mismo setup partial-view; si multi-agente empata o pierde, eso mismo es un resultado publicable negativo |
| Reto a la "novedad" por revisores | Mantener tabla explícita de comparativa con trabajo previo; pre-registrar diseño experimental con el advisor antes de correr |
| Reproducibilidad | Todos los experimentos con semilla controlada; configs en `experiments/` como YAML; logging de runs W&B |
| Sensibilidad legal Tornado Cash | Contratos mock sólo en fork local; documentar esto prominentemente; sign-off del advisor antes de semana 5 |
| Rate limits o cambios de schema del free tier CoinGecko rompen experimentos | Pre-descargar todos los precios históricos necesarios a un caché CSV local versionado; PriceOracle lee del caché, no de la API live, durante los experimentos |

---

## 8. Trabajo futuro (fuera del scope v1)

Las siguientes extensiones están explícitamente fuera del scope de esta tesis pero la arquitectura está diseñada para que cada una se pueda añadir sin rework:

- **Simulador Tron + USDT TRC-20**. Replayer custom de event-logs + grafo real muestreado de USDT TRC-20 vía TronGrid (read-only). Extendería la contribución #2 a laundering stablecoin cross-chain. El scaffold del contrato MockBridge ya está presente (semana 3.4) así que laundering vía bridge cross-chain se vuelve un single-PR una vez existe el lado Tron.
- **Laundering vía bridge como táctica de layering activa**. El contrato MockBridge está shipped pero los agentes blanqueadores no ejercitan transferencias cross-chain en v1. Reactivarlo es una adición de tool al Layering-agent, sin trabajo de contrato.
- **Laundering en privacy-coins en el borde**. Monero, Zcash, y cadenas similares privacy-by-default derrotan el análisis on-chain por diseño — no pueden modelarse como grafos de transacción. El framing research realista es *boundary detection*: un atacante usa ETH→XMR→ETH (vía exchanges) como hop de layering; el defensor correlaciona timing y monto a través del gap de privacidad. Estructuralmente similar al problema de detección de mezclador ya en scope, con fricción extra cross-asset.
- **Denominaciones y familias adicionales de mezclador**. El mock estilo Tornado actual es single-denomination. Extender a pools multi-denomination (0.1 / 1 / 10 / 100 ETH) es un follow-up natural; mismo shape de circuito, parámetros distintos.
- **Escenarios de despliegue en tiempo real**. El setup actual corre offline contra datos sintéticos e históricos. Un despliegue de live-monitoring (agentes defensor leyendo eventos mainnet en tiempo real) es operations-engineering, no research, pero una extensión práctica clara.
