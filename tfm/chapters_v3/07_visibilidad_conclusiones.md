# Capítulo 6 — Visibilidad parcial, conclusiones y findings

Este capítulo cierra el TFM con (a) la argumentación de la novedad
central del trabajo — visibilidad parcial federada por exchange,
el patrón que ningún trabajo previo ha explorado — y (b) las
conclusiones, decisiones clave de diseño, findings publishable y
reflexión final.

---

## 6.1 Decisiones de diseño

Once decisiones estructurales dan forma al proyecto. Las once se
sintetizan en la Tabla siguiente y a continuación se desarrollan las tres
más determinantes para la contribución del trabajo.

| Decisión | Alternativa descartada | Motivación empírica |
|----------|-----------------------|---------------------|
| 7.1 Contratos Solidity hand-written | OpenZeppelin / Uniswap importados | Superficie adversarial minimizada, control total sobre invariants |
| 7.2 Simetría LLM-vs-LLM | Defensor supervisado clásico | Interpretabilidad requerida por FATF Rec.20 y MiCA art.63 |
| 7.3 Herramientas batched en catálogo | Herramientas atómicas | Reduce llamadas LLM ×8 en structuring sub-CTR |
| 7.4 Cinco invariants del `ToolDispatcher` | Validación por caso | Fail-safe determinístico + trazabilidad completa |
| 7.5 Oráculo determinista de precios | Precios random / hardcoded | Reproducibilidad exacta entre runs |
| 7.6 Wrapper LLM provider-agnostic | Anthropic SDK directo | Switching flexible Opus↔Sonnet↔Haiku |
| 7.7 Particionado federado hash-based | Random split | Estabilidad entre runs, determinismo por seed |
| 7.8 LOCO-CV como métrica primaria | Split 80/20 estándar | Expone memorización (§8.10 Finding 1: ΔF1 = −0.55) |
| 7.9 Threshold Louvain calibrado en 0.6 | Threshold default 0.5 | Calibrado empíricamente en §8.5 sweep |
| 7.10 Persistencia inmediata de mixer notes | Persistencia post-hoc | Elimina dependencia de contexto LLM (P1-61) |
| 7.11 Reverse swap USDT→ETH en sweep | Sin sweep | Recupera hasta 5 ETH del pool residual post-campaña |

La decisión de que tanto el atacante como el defensor sean LLM es el eje
conceptual de este trabajo. Un defensor supervisado clásico basado sólo
en GCN podría producir F1 similar, pero no ofrecería razonamiento textual
auditable —capacidad que FATF Rec. 20 y el artículo 63 de MiCA exigen
para un despliegue regulatorio real. El coste marginal de la simetría es
despreciable: aproximadamente 0,04 USD por evaluación con Haiku 4.5,
como se detalla en §8.9.Z. La ganancia en interpretabilidad justifica de
sobra ese coste.

La adopción de LOCO-CV como métrica primaria desde el primer experimento,
no como ablation posterior, es la decisión metodológica más importante
del trabajo. Los datasets AML pequeños (menos de treinta campañas)
permiten al GCN memorizar la identidad de cada campaña, produciendo F1
estándar superior a 0.95 que se desploma a F1 ≈ 0.42 bajo LOCO. Este
comportamiento habría pasado desapercibido si la evaluación se hubiera
limitado a splits 80/20 aleatorios, como ocurre en la mayoría de la
literatura AML previa [1][2][3]. Louvain, al ser no supervisado, actúa
como cota inferior confiable de generalización y sirve como *early
warning* de sobreajuste GCN, según formalizamos en §8.10 Finding 3.

Finalmente, la decisión de externalizar el control de granularidad al
post-procesamiento code-level (P1-71) es la que produjo el único
resultado positivo del defensor tras cuatro ablations negativas
consecutivas (P1-55, P1-56, P1-70, P1-72). Cuando cuatro intentos de
guiar al LLM vía prompt o features no movieron el ARI significativamente,
el patrón consolidado fue claro: el LLM produce buena señal cualitativa
sobre proximidad de direcciones, pero su decisión cuantitativa —cuántos
clusters emitir— está sistemáticamente sesgada hacia la
sobre-fragmentación. Corregir ese sesgo con un merge determinístico
posterior, en lugar de intentar reeducar al LLM, es lo que llevó el mean
ARI de 0.001 a 0.159 (§8.9.G). Esta observación se formula como
meta-finding publishable: para controlar output cuantitativo discreto de
un LLM, el post-procesamiento code-level determinístico supera
sistemáticamente al prompt engineering.


## 6.2 Conclusiones, findings y trabajo futuro

Este capítulo recapitula las cuatro contribuciones del Trabajo Fin de
Máster tal como han quedado sustanciadas por los resultados del
Capítulo 5, discute las lecciones metodológicas derivadas del proceso
experimental, y traza un programa de trabajo futuro concreto
articulado sobre las limitaciones identificadas.

### 6.2.1 Recapitulación de las contribuciones

Las cuatro contribuciones anunciadas en el Capítulo 1 §1.3 se han
sustanciado empíricamente:

**Contribución 1 — Arquitectura simétrica LLM-vs-LLM**. Se ha
implementado un sistema completo en el que tanto el atacante como el
defensor están orquestados por agentes basados en modelos de lenguaje
grandes. El atacante integra un coordinador Opus 4.7 con tres
sub-agentes Sonnet 4.6 especializados por fase FATF (Placement,
Layering, Integration) sobre un catálogo de diecinueve herramientas
on-chain reales. El defensor replica la asimetría con clasificadores
GCN locales por exchange y un coordinador Sonnet 4.6 cross-exchange
que razona sobre los fingerprints agregados para inferir actor
clusters. Hasta donde el autor conoce, la simetría LLM-vs-LLM
implementada en este trabajo es la primera de su tipo publicada en
la literatura AML cripto.

**Contribución 2 — Evaluación estricta bajo visibilidad parcial
federada**. Los cuatro *datasets* del trabajo (simulación propia,
EthereumHeist real, Elliptic++, OpenAML v1) se someten al mismo
particionado `partial_visibility_split` en tres exchanges con
etiquetas KYC visibles sólo intra-exchange. Los detectores se
entrenan y evalúan bajo este régimen federado estricto, no bajo la
vista global implícita en los benchmarks académicos previos. Este
esquema replica fielmente la asimetría regulatoria post-MiCA descrita
en el Capítulo 2 §2.1.

**Contribución 3 — Auditoría metodológica de la memorización**. El
Capítulo 5 §8.10 formaliza tres *findings* publicables independientemente
del sistema propuesto. Sobre la simulación propia, el GCN pierde
ΔF1 = −0,55 al pasar de *split* 80/20 a LOCO-CV; sobre EthereumHeist
real, la pérdida es ΔF1 = −0,31; Louvain preserva su F1 en ambos
regímenes por ser no supervisado. La conclusión metodológica —que
los detectores GCN sobre *datasets* AML pequeños tienden a memorizar
la identidad de actor en vez de aprender la propiedad subyacente— es
directamente relevante para el diseño de futuros *benchmarks* del
campo.

**Contribución 4 — Validación externa on-chain sobre Sepolia**. El
sistema completo se ha desplegado sobre la *testnet* pública Sepolia
(chain_id `11155111`) con seis contratos verificables en
`sepolia.etherscan.io`. La infraestructura de deploy es reproducible
mediante `scripts/deploy_eth_mocks_sepolia.py` documentado en
`docs/SEPOLIA_DEPLOY.md`, y el pipeline detector opera sobre el grafo
extraído directamente del *provider* RPC. Las dos campañas Sepolia
ejecutadas (seed 100 de 19 min y seed 500 de 61 min) generan
transacciones cuyos *hashes* son
independientemente verificables por cualquier tercero con acceso a la
red pública.

### 6.2.2 Trade-off central: interpretabilidad frente a métrica cuantitativa

La discusión del Capítulo 5 §8.8 identifica el hallazgo empírico que
resume la contribución central del trabajo: la similaridad coseno
supera al coordinador LLM en ARI raw (0,081 vs 0,013 en simulación;
0,066 vs 0,040 sobre EthereumHeist real), pero el coordinador LLM
produce clusters interpretables con arquetipos AML canónicos
(*cross-exchange mixer hub*, *pass-through mixer relay*,
*consolidation sink*, *peeling chain campaign*, *burner wallet
fan-out*) alineados con la Recomendación FATF 20 sobre transparencia
de decisiones AML automatizadas.

Este resultado se ha interpretado no como una limitación del enfoque
LLM sino como una consecuencia estructural de dos criterios de
clustering distintos: la similaridad coseno agrupa por identidad de
firma feature-por-feature (que en el *dataset* simulado
correlaciona por diseño con identidad de campaña), mientras que el
LLM agrupa por arquetipo de comportamiento AML (que cruza fronteras
de campaña en el *dataset* pero es semánticamente más rico y
directamente utilizable por auditores humanos).

El coste marginal de la interpretabilidad es 0,14-0,17 USD por
evaluación completa de un batch de 180 direcciones flageadas, o
aproximadamente 50-200 USD/día extrapolado a un despliegue
productivo con 100 000 direcciones/día. Este coste es dos órdenes de
magnitud inferior al coste equivalente en horas humanas de un
*compliance officer* revisando el mismo volumen.

### 6.2.3 Lecciones metodológicas

Del proceso experimental se extraen tres lecciones metodológicas
aplicables al campo más allá del sistema específico propuesto.

**Lección 1 — LOCO como métrica primaria en *datasets* pequeños**. La
literatura AML sobre grafos ha reportado sistemáticamente F1 > 0,90
sobre *datasets* con menos de 30 actor clusters, incluyendo trabajos
publicados sobre Elliptic (Weber et al. 2019), EthereumHeist (Wu et
al. 2023) y OpenAML v1 (FINOS 2025). Los resultados del
Capítulo 5 §8.5 sugieren que esas cifras sobreestiman el rendimiento
operativo real por 30-55 puntos absolutos de F1. La recomendación
metodológica derivada es que los futuros *benchmarks* AML con actor
clusters escasos deben reportar *leave-one-actor-out* como métrica
principal, no como *ablation*.

**Lección 2 — Baselines no paramétricos son sorprendentemente
fuertes**. El baseline de similaridad coseno sobre features de nodo
(§8.6) supera al coordinador LLM en ARI en ambos *datasets* sin
requerir entrenamiento supervisado, sin recurso computacional
significativo, y sin coste monetario. Cualquier propuesta futura de
detector AML más sofisticado debe justificar su coste marginal
contra este *strawman*. La lección práctica: incluir similaridad
coseno como baseline obligatorio en toda comparativa de detectores
AML sobre grafos.

**Lección 3 — Los prompts son artefactos experimentales*. El proceso
iterativo descrito en el Capítulo 4 §4.6 —cuatro versiones del
prompt del defensor LLM antes de estabilizar el diseño final—
demuestra que la calidad del razonamiento LLM depende críticamente
del prompt. La comunidad debe converger a estándares de publicación
que incluyan los prompts íntegros como artefactos verificables (Anexo
A y B de este trabajo son un intento en esa dirección), no como
implementaciones opacas.

### 6.2.4 Trabajo futuro

El trabajo futuro se organiza en cuatro categorías: (A) extensiones
inmediatas del scope del TFM, (B) arquitectura de despliegue en
producción, (C) mejoras del pipeline atacante identificadas durante
las 26 corridas (referencias P1-XX de commits GitHub), y (D) mejoras
del pipeline defensor. Las items marcadas **[IMPLEMENTED]** ya
existen en el código publicado; los demás son propuestas.

#### 6.2.4.A Extensiones inmediatas del scope

**A1 — Extensión temporal**. El pipeline actual opera sobre snapshots
estáticos. Un atacante consciente del detector podría dispersar
transacciones a lo largo de meses para quedar bajo el umbral por
ventana. Incorporar features temporales o GNNs con memoria (T-GCN,
TGN) queda para trabajo futuro. Impacto esperado: +5-10 pp F1
adicional en escenarios adversariales temporales.

**A2 — Simetría LLM completa (Opus-vs-Opus)**. El coordinador
atacante usa Opus 4.7 y el defensor Haiku 4.5 / Sonnet 4.6 por
presupuesto. Un experimento verdaderamente simétrico Opus-vs-Opus
está fuera del alcance actual (~$3-5 USD por corrida defensor Opus)
pero es técnicamente inmediato. Publishable: ¿un defensor de mismo
tamaño supera al atacante o mantiene la asimetría observada?

**A3 — Escalado a ≥ 100 campañas**. Las 26 campañas actuales cubren
las 3 typologies FATF (defi-exploit, ransomware, stablecoin-scam)
pero un régimen de 100+ permitiría análisis estadístico más robusto
de las brechas per-typology. Coste estimado: 100 × $2 = ~$200 en
LLM atacante.

**A4 — Extensión cross-chain y multi-token**. El TFM se limita a
Ethereum + ETH/USDT. Cross-chain (bridge a Polygon/Arbitrum) y
multi-token (USDC, DAI, WBTC) son extensiones directas que
diversificarían el simulador y permitirían evaluar detección de
patterns cross-chain (bridge-based laundering).

**A5 — Memoria histórica del defensor**. El defensor actual procesa
cada evaluación independientemente. Un defensor productivo tendría
memoria histórica de patterns previamente flagged, permitiendo
detección de repeat-offenders y evolución adaptativa a nuevas
técnicas atacantes.

#### 6.2.4.B Arquitectura de despliegue en producción

**B1 — Sistema productivo AML sobre exchange real**. Un producto
comercial basado en este pipeline requeriría: (i) integración con
APIs de exchange (Binance, Coinbase, Kraken) para acceso al ledger
en tiempo real; (ii) escalado del defensor a millones de addresses/día
(actual: 30k nodos/eval); (iii) UI para SAR reports generados por
el LLM; (iv) compliance con MiCA/AMLD5/GDPR (retention, right to
erasure, audit trails). Estimación: 6-12 meses de ingeniería + equipo
de 3-5 personas.

**B2 — Meta-transacciones (EIP-4337)**. Actualmente el pipeline
propaga gas a cada burner via transfer nativo (arquitectura A+B+D+G+,
§8.9). Con account abstraction (ERC-4337) los burners podrían pagar
gas en USDT, eliminando el edge topológico `funder→burner` que el
defensor detecta. Esto haría el pipeline más sofisticado
adversarialmente y publishable como iteration del atacante.

**B3 — Cobertura regulatoria ampliada**. El TFM aborda FATF Rec. 16
(travel rule) y Rec. 20 (transparencia SAR) pero no las Rec. 10
(customer due diligence), Rec. 11 (record keeping), ni la totalidad
de MiCA. Un sistema productivo debe integrar todas ellas.

#### 6.2.4.C Mejoras identificadas del pipeline atacante

Durante las 26 corridas se identificaron 12 mejoras (P1-XX) del
pipeline atacante. Todas están registradas en commits del
repositorio; sólo se sintetizan aquí las relevantes para trabajo
futuro:

- **P1-42 [IMPLEMENTED]** Arquitectura A+B+D+G+ (self-sovereign gas
  propagation). Elimina el anti-strand co-funding leak descubierto
  en seed 800. **Adoptado como default en seeds 802+**.
- **P1-43/44/47/48/49 [IMPLEMENTED]** Diversificación técnica:
  distribute_to_exits tool, balanced technique mix (mixer/peel/swap),
  human-like refinements. **Validado empíricamente en seed 803**.
- **P1-45/46/51 [FUTURE]** Humanización temporal del atacante —
  jitter en timing entre transacciones, distribución de horas
  mainnet-like. Impacto esperado: reducir F1 en 2-5 pp.
- **P1-50 [FUTURE]** Simulated operator mistakes — inyectar errores
  humanos realistas (typos, wrong recipient, overpayment) para
  representar la operativa real vs. la ejecución perfecta actual.
- **P1-52 [FUTURE]** Time-lagged multi-week exchange distribution.
  Coordinar cash-outs a través de semanas en múltiples exchanges
  para evadir detección por ventana temporal.
- **P1-53 [FUTURE]** Cross-chain bridge integration. Permitiría al
  atacante lavar via Polygon/Arbitrum y regresar a Ethereum.
- **P1-59 [FUTURE]** Funder bootstrap via clean-history simulator —
  darle historial benigno pre-campaña al funder wallet para
  camuflarlo mejor.
- **P1-62 [IMPLEMENTED]** Anvil auto-recovery pipeline. Detecta
  campañas fallidas y ejecuta post-hoc `mixer_recover` sin
  intervención humana.
- **P1-63 [FUTURE]** Streaming API + context compression. Elimina
  el context inflation crash que hizo fallar los intentos
  stablecoin-scam de 41k USDT (§8.9.B).
- **P1-64/65 [IMPLEMENTED]** Chain trace completeness bugs fixed
  (post-2026-09-10). Datasets Anvil generados desde entonces tienen
  traces completas incluyendo anti-strand + rescue phases.
- **P1-67 [IMPLEMENTED]** LLM timeout + retry en `llm_client.py`
  (300s read timeout, 5 retries). Evita cuelgues por Sonnet response
  slow-path.

#### 6.2.4.D Mejoras del pipeline defensor

- **P1-55/56/70 [ATTEMPTED, NEGATIVE]** Prompt-level guidance para
  guiar la granularidad de output del LLM. Los tres ablations
  fallaron consistentemente (§8.9.F). Publishable como
  meta-finding: prompt engineering no controla output cuantitativo
  discreto del LLM. Ver §6.2.4.28 síntesis.
- **P1-68 [FUTURE]** LLM re-scoring bidireccional. Para reducir el
  background FPR residual, el LLM re-evalúa las address flagged por
  Louvain con conocimiento explícito de los otros flagged
  addresses. Impacto esperado: precision → 1.000 en 5/5 datasets.
- **P1-69 [IMPLEMENTED]** Hard-negative training (§8.9.D.2). Cerró
  el label leakage con inyección de 500 background addresses como
  benigns duros durante fit.
- **P1-71 [IMPLEMENTED, POSITIVE]** Post-hoc cluster merge por
  centroid distance. Primera intervención positiva del defensor.
  Mean ARI 0.001 → 0.159 (~160×). Ver §8.9.G.
- **P1-72 [ATTEMPTED, NEGATIVE]** Feature enrichment con pagerank
  + betweenness + clustering coefficient (19→22 dim). ARI empeoró
  0.011. Confirma que el signal está en features de flow, no en
  topología global.
- **P1-73 [IMPLEMENTED, POSITIVE]** Silhouette auto-tune de
  `max_clusters`. Quita el caveat metodológico de P1-71 (necesitar
  oracle k). Ver §8.9.H.
- **P1-74 [ATTEMPTED, ANTI-EXPECTED]** Sonnet 4.6 + P1-71 sobre 5
  datasets. Sonnet es peor final que Haiku (0.078 vs 0.159) y 3× más
  caro. Configuración Pareto-óptima confirmada: **Haiku 4.5 + P1-71 +
  P1-73**. Ver §8.9.I.

#### 6.2.4.28 Síntesis del control de output del LLM

De las siete intervenciones aplicadas al defensor se extrae un
meta-finding publishable: para controlar decisiones cuantitativas del
output de un LLM (número de clusters, categorías, budgets), modificar
el post-proceso resulta sistemáticamente más efectivo que modificar el
prompt o los features. El LLM genera bien la señal cualitativa —qué
direcciones están cerca de qué otras en el espacio de features— pero
falla en la decisión operativa cuantitativa —dónde cortar. Externalizar
esa decisión al código, respetando la señal del LLM, es la palanca
correcta. Esta conclusión generaliza más allá del AML: en cualquier
pipeline LLM-agent + downstream computation donde el LLM produce
categorizaciones sin garantías de cardinalidad, un merge o split
post-hoc determinista basado en distancia entre representaciones
implícitas es más barato y más robusto que iterar sobre el prompt.

### 6.2.4.29 Alcance y limitaciones del trabajo

Antes de cerrar el capítulo se explicitan las cuatro limitaciones
principales del trabajo, que a su vez motivan buena parte del
programa de trabajo futuro. Estas limitaciones acotan el alcance
declarado en §1.4 y §1.5.

**Limitación 1 — Ausencia de temporalidad**. El clasificador GCN opera
sobre snapshots estáticos del grafo. Un atacante consciente del
detector podría explotar temporalidad para dispersar las transacciones
de una campaña a lo largo de meses, quedando por debajo del umbral de
detección por ventana. La incorporación de features temporales o de
GNNs con memoria (T-GCN, TGN) queda para trabajo futuro.

**Limitación 2 — Simetría LLM-vs-LLM incompleta**. El coordinador
atacante (Opus 4.7) y el coordinador defensor (Sonnet 4.6 / Haiku 4.5)
usan distintos tamaños de modelo por constricciones de presupuesto.
Un experimento verdaderamente simétrico Opus-vs-Opus está fuera del
alcance presupuestario actual pero es técnicamente inmediato.

**Limitación 3 — Escala de la simulación**. Veintiséis campañas
atacantes sobre unos diez mil nodos es un régimen pequeño frente a
datasets académicos recientes (AMLWorld: 10⁷ transacciones). La
justificación es el coste de la simulación multi-agente basada en LLM
(~50 EUR para veintiséis campañas atacantes reales), pero la
extrapolación de las conclusiones a regímenes de mayor escala
requeriría experimentación adicional.

**Limitación 4 — Alcance regulatorio parcial**. Este trabajo aborda
FATF Recomendaciones 16 (travel rule) y 20 (transparencia SAR) más
MiCA artículos 60/63/68, pero no las Recomendaciones 10 (customer due
diligence), 11 (record keeping), ni la totalidad de MiCA. Un sistema
productivo AML debe integrar todos estos elementos; este TFM se
circunscribe a la parte técnica de detección y atribución.

Las tres primeras limitaciones se traducen directamente en las
propuestas §6.2.4.A (extensión temporal), §6.2.4.A (simetría Opus-vs-Opus)
y §6.2.4.A (escalado a régimen ≥ 100 campañas) descritas más arriba.

### 6.2.5 Reflexión final

El diseño original de este TFM planteaba un sistema
atacante-detector clásico donde el atacante era un agente LLM y el
defensor un clasificador supervisado convencional. Muy pronto quedó
claro, sin embargo, que elevar el defensor a paridad arquitectónica
—hibridar el clasificador local con un LLM coordinador que razone
sobre los agregados— era el pivote técnico y narrativo del trabajo:
sin él el sistema no producía justificaciones alineadas con la
Recomendación FATF 20 y perdía su *contribución central*. Este
cambio de diseño quedó fijado como el eje conceptual de la
arquitectura definitiva descrita en los Capítulos 6 y 7.

La contribución central del trabajo, tal como se ha sustanciado a
través de los ocho capítulos anteriores, no es que el LLM defensor
supere numéricamente al *baseline* coseno —no lo hace, y el
Capítulo 5 §8.8 lo argumenta detenidamente—. La contribución es
demostrar que un *pipeline* AML sobre grafos puede producir
*simultáneamente* *flags* binarios operativamente accionables
(mediante el clasificador GCN local) y justificaciones textuales
alineadas con arquetipos AML canónicos (mediante el coordinador
LLM), cerrando el *gap* entre las capacidades cuantitativas de la
literatura académica reciente y los requisitos regulatorios de
transparencia post-MiCA que operarán sobre el sector desde 2027.

La reproducibilidad íntegra del trabajo —código publicado bajo
licencia MIT, comandos exactos de reproducción para cada tabla y
figura, prompts documentados como artefactos versionados,
contratos verificables en Sepolia Etherscan— pretende que este TFM
sirva como base sobre la que otros investigadores puedan iterar sin
la habitual fricción de re-implementación desde cero. En un campo
donde la mayoría de sistemas publicados no incluyen código
ejecutable, la publicación completa del artefacto es en sí misma una
contribución.

## 6.3 Justificación de competencias del Máster FinTech

Conforme al artículo de la Normativa Propia TFM del Máster Universitario
en Tecnologías del Sector Financiero (FinTech) UC3M (actualizada
2024-2025), se enumera cómo este TFM cubre las competencias básicas
(CB), generales (CG) y específicas (CE) del título.

### 7.C.1 Competencias Básicas

- **CB6 — Conocimientos originales en contexto de investigación**.
  Cubierta por la novedad de visibilidad parcial federada (§4.4), el
  simulador atacante LLM-driven (§4.A/5.B) y las 7 intervenciones
  publishable documentadas en §6.2.4. El meta-finding
  «cost-effective code-level post-processing supera prompt engineering»
  generaliza más allá del AML — contribución original al diseño de
  pipelines LLM-agent + downstream computation.
- **CB7 — Aplicación de conocimientos a entornos complejos**.
  Cubierta por la validación cross-domain sobre EthereumHeist
  (§8.9.E, dataset externo con 633k nodos nunca visto en desarrollo)
  y multi-campaign LOCO (§8.9.K, 7 campañas simultáneas sobre grafo
  combinado 34k nodos).
- **CB8 — Integración con reflexión ético-social**. Cubierta por la
  reflexión sobre implicaciones regulatorias FATF/MiCA (§2.1, §3.4),
  el análisis de limitaciones (§8.11) y la declaración honesta de
  4 ablations negativas (P1-55/56/70/72) que rechazaron hipótesis
  iniciales.
- **CB9 — Comunicación clara**. Cubierta por la estructura del TFM:
  resumen bilingüe (§1), tabla ejecutiva de findings (§6.2) y cost
  summary (§8.9.Z).
- **CB10 — Aprendizaje autodirigido**. Cubierta por el arco completo:
  el autor partió sin experiencia previa en Solidity, ZK proofs o
  LLM agents y desarrolló los tres stacks durante los 5 meses del
  TFM (ver §4.C LOC por lenguaje).

### 7.C.2 Competencias Generales

- **CG1 — Métodos de ingeniería informática en mercados financieros**.
  Cubierta por la aplicación de algoritmos de grafos (Louvain), ML
  supervisado (GCN Weber-style), agentes LLM con tool-use, y
  protocolos criptográficos (Groth16 ZK proofs) a la detección de
  laundering en cripto ERC-20.
- **CG2 — Desarrollo sustancial de software financiero**. Cubierta
  ampliamente: ~10 100 líneas de código (9 000 Python + 800 Solidity
  + 60 Circom + 240 auxiliar, §5.C.7), 6 contratos verificados en
  Etherscan, pipeline end-to-end reproducible.
- **CG3 — Problemas nuevos y multidisciplinares**. Cubierta por la
  integración de cinco áreas técnicas: criptografía ZK, blockchain,
  grafo estructural, ML supervisado y agentes LLM. Ninguna
  disciplina aisladamente resolvería el problema.
- **CG4 — Composiciones escritas con originalidad**. Cubierta por
  este documento (>4 000 líneas Markdown técnico original) y los
  meta-findings: «F1=1.000 era noise-de-seed» (§8.9.J), «role vs
  campaign attribution» (§8.9.49), «smaller cheaper LLM + right
  post-processing beats larger» (§8.9.I), «mixer features tienen
  RF importance = 0» (§8.9.L).

### 7.C.3 Competencias Específicas

- **CE1 — Mercados financieros**. Cubierta por el tratamiento formal
  de tipologías FATF (§2.1), marco regulatorio MiCA / Rec.16 / Rec.20
  (§2.1, §3.4) y economía real de campañas (§8.9.B).
- **CE2 — Tecnologías del sector financiero**. Cubierta por el
  análisis técnico del stack blockchain (§3), justificación
  Foundry/Solidity/Anthropic y diseño de infraestructura
  Sepolia + Anvil-dev.
- **CE3 — Software financiero end-to-end**. Cubierta por el pipeline
  completo desde definición del problema (§1.2) hasta despliegue
  on-chain verificable en Sepolia Etherscan (§5.B.4).
- **CE4 — Algoritmos y técnicas clásicas siguiendo estándares**.
  Cubierta por la implementación de baselines establecidos (Louvain,
  GCN, RF) siguiendo convenciones scikit-learn / PyTorch Geometric.
- **CE5 — Herramientas para grandes cantidades de datos**. Cubierta
  por el procesamiento del grafo combinado EthereumHeist (633k
  nodos, 2.4M transacciones) vía NetworkX + numpy + pandas y por el
  particionado federado sobre grafos de hasta 34k nodos (§8.9.K).

### 7.C.4 Materia del Máster asociada

Este TFM se enmarca principalmente en la materia **Desarrollo de
Software Financiero** (Programación de Altas Prestaciones,
Algoritmos de Front-Office/Back-Office, Gestión e Ingeniería del
Software Financiero) — reflejado en el desarrollo sustancial de
software y contratos on-chain — y secundariamente en **Sistemas de
Soporte a la Decisión en el Sector Financiero** (Big Data, Análisis
de Datos) por la componente analítica y de detección de patrones
sobre grafos.

Este TFM ha logrado la adquisición demostrable de las once
competencias listadas del título, con evidencia empírica y
publishable en cada caso.
