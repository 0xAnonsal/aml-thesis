# Capítulo 7 — Visibilidad parcial, conclusiones y findings

Este capítulo cierra el TFM con (a) la argumentación de la novedad
central del trabajo — visibilidad parcial federada por exchange,
el patrón que ningún trabajo previo ha explorado — y (b) las
conclusiones, decisiones clave de diseño, findings publishable y
reflexión final.

---

## 7.A Decisiones clave de diseño

Este capítulo consolida las once decisiones de diseño mayores del
proyecto, cada una con su motivación, alternativas descartadas y
justificación empírica o teórica. Las decisiones están agrupadas
por *ámbito*: (i) contratos y stack criptográfico; (ii) arquitectura
multi-agente LLM; (iii) evaluación del defensor; (iv) reproducibilidad
y operaciones.

## 7.1 Contratos hand-written vs importación de OpenZeppelin / Uniswap

**Decisión**: Escribir a mano `MockUSDT`, `MockUniswapV2Pool` y
`MockBridge` en lugar de importar `OpenZeppelin/erc20` + `Uniswap V2
core` + un bridge open source.

**Motivación**:
1. Los contratos originales traen ≈ 40 MB de git submodule con
   dependencias transitivas y features irrelevantes al *research*
   (blacklists Tether, LP tokens Uniswap, multisig bridges,
   upgradability por proxy).
2. El marcado `SPDX-License-Identifier: UNLICENSED` + los comentarios
   prominentes `NEVER deploy on a real chain` reducen el riesgo de
   que un tercero re-utilice los mocks en producción.
3. Reduce la superficie de código a auditar (63-96 líneas por
   contrato frente a 400+ líneas de las versiones canónicas).

**Alternativa considerada**: importar OpenZeppelin como git submodule.
Descartada porque añade ≈ 250 ficheros al repo para necesitar 1
contrato.

**Excepción**: los tres contratos derivados de Tornado Cash
(`MockTornado`, `MerkleTreeWithHistory`, `Verifier`) SÍ se
adaptaron del código original bajo licencia MIT (respetada en el
header). Motivo: la criptografía DEBE preservarse literalmente para
que la simulación mantenga las propiedades de *soundness* y
*zero-knowledge* del sistema real.

## 7.2 Simetría LLM-vs-LLM en atacante y defensor

**Decisión**: Estructurar tanto atacante como defensor como sistemas
multi-agente basados en LLMs, en lugar del patrón asimétrico común
(atacante clásico gradient-based + defensor LLM, o al revés).

**Motivación**:
1. **Contribución novel**: hasta donde el autor conoce, la simetría
   LLM-vs-LLM completa no está publicada en la literatura AML cripto.
   Trabajos previos (Weber 2019, Cardoso 2022) evalúan detectores
   contra ataques *gradient-based* (Egressy 2023) o perturbaciones
   sintéticas.
2. **Realismo tipológico del atacante**: los ataques *gradient-based*
   producen perturbaciones que satisfacen objetivos de evasión ML
   pero no son realistas tipológicamente. Un LLM instrumentado con
   herramientas on-chain reales genera comportamiento alineado con
   las tipologías FATF (Placement / Layering / Integration) por
   diseño.
3. **Interpretabilidad del defensor**: un LLM coordinador cross-
   exchange produce clusters de actor con arquetipos AML
   *nombrables* (*"cross-exchange mixer hub"*, *"pass-through mixer
   relay"*, etc.), mientras un baseline no supervisado produce
   grupos sin semántica.

**Alternativa considerada**: LLM solo del lado atacante + GNN clásico
del lado defensor. Descartada porque no aporta valor semántico al
defender ni permite el ratio coste-defensa que se reporta en §8.9.5.

## 7.3 Herramientas *batched* en el catálogo del atacante

**Decisión**: Añadir variantes *batched* (`smurf_split`,
`smurf_eth_split`, `peel_chain`, `mixer_batch_deposit`,
`mixer_batch_withdraw`) al catálogo del atacante como
complemento a las primitivas atómicas.

**Motivación**:
1. **Coste de tokens LLM**: cada llamada al LLM cuesta *input
   tokens* proporcionales al contexto acumulado. Encapsular N
   operaciones en un solo *round trip* reduce el consumo de tokens
   en factor 3-6× frente al bucle atómico.
2. **No compromete granularidad forense**: cada operación individual
   dentro del batch aparece como una transacción on-chain
   independiente en el `chain_trace.jsonl`, indistinguible desde el
   punto de vista del detector.
3. **Coordinador razona a granularidad estratégica**: *"quiero hacer
   10 ciclos por el mezclador"* en lugar de 10 × *"hacer un
   ciclo"* + 10 × *"hacer el siguiente"*. Más natural para el
   planning multi-turno.

**Nota metodológica**: en la corrida canónica seed 403 el atacante
Sonnet privilegió las tools atómicas sobre las batched por
consideraciones de granularidad forense (evitar el patrón "una
transacción origen con N destinos" que Louvain identificaría como
*hub*), produciendo un consumo de tokens ≈ 30-50 % superior
al mínimo teórico. Este es un *ajuste de prompt* identificado como
mejora incremental de trabajo futuro.

## 7.4 Cinco invariants estructurales del `ToolDispatcher`

**Decisión**: Aplicar cinco restricciones operativas transversalmente
en el `ToolDispatcher` (no en cada herramienta individual):

1. **Guardarraíl del deployer**: la dirección
   `0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4` (deployer de
   contratos en Sepolia) NO puede aparecer como `from`, `to`,
   `gas_payer` ni `recipient` de ninguna llamada.
2. **Cap dinámico de burners**: el agente no puede solicitar más
   de max(30, min(250, 3 × ⌈USD/999⌉)) direcciones burner por
   campaña — es decir, mínimo 30 burners para dar flexibilidad
   táctica en campañas pequeñas, máximo 250 para evitar bucles
   patológicos, y una cota intermedia proporcional al volumen
   USD dividido por el umbral CTR de $999.
3. **Rotación del funder pool**: `_pick_funder` rota entre wallets
   del pool y solo solicita *refill* cuando el balance cae por
   debajo de 0,01 ETH.
4. **Gas reserve** por defecto de 0,005 ETH sobre cada burner.
5. **`sweep_funder_pool`** consolida el residual del pool al final
   de la campaña.

**Motivación**: los invariants derivan de fallos empíricos
observados en corridas tempranas —seed 306 generó 236 burners para
1 ETH (bucle patológico); seed 200 metió al deployer en el pool
operativo (contaminó las métricas de recovery); seeds 202-204
sobrescribieron balances con reserves inconsistentes—. Codificarlos
en el dispatcher garantiza que ninguna sub-agente pueda violarlos
sea cual sea la deriva del prompt.

**Alternativa considerada**: dejar los invariants como
recomendaciones textuales en el prompt sistema del sub-agente.
Descartada por la propia experiencia de la deriva de prompt: los
LLM ignoran recomendaciones textuales bajo presión de tarea.

## 7.5 Uso del oráculo determinista en atacante y defensor

**Decisión**: Ambos jugadores del sistema (atacante Coordinator y
defensor LLM Coordinator) consumen el mismo helper
`build_market_context()` para inyectar el precio spot ETH/USDT/TRX
en su system prompt.

**Motivación**:
1. **Simetría de información**: sin este mecanismo, el atacante
   razonaría sobre umbrales USD (999 CTR) mientras el defensor
   podría estar razonando sobre un régimen de precios distinto,
   invalidando la comparativa.
2. **Reproducibilidad byte-idéntica**: el helper `resolve_campaign_ts`
   fija la fecha a una jornada específica de caché CSV (CoinGecko
   pre-descargado), garantizando que dos corridas del mismo seed
   producen el mismo bloque MARKET CONTEXT.
3. **Sin llamada API en tiempo real**: el CSV local se refresca
   una vez por sesión de trabajo mediante
   `scripts/download_prices.py`; las corridas nunca llaman a
   CoinGecko live, evitando dependencia de red + rate limits.

**Alternativa considerada**: hardcodear precios en el prompt.
Descartada porque los precios cripto derivan de mercado y hardcodear
introduciría un artefacto no verosímil (un atacante real razona sobre
precios de hoy, no de una fecha arbitraria).

## 7.6 Wrapper LLM abstracto (provider-agnostic)

**Decisión**: encapsular el SDK de Anthropic tras una interfaz
`complete(prompt, system, model, max_tokens) -> LLMResult` en
`src/aml/attackers/llm_client.py`.

**Motivación**:
1. **Uniformar cálculo de coste**: los precios por millón de tokens
   input/output difieren en dos órdenes de magnitud entre Haiku,
   Sonnet y Opus. El SDK crudo devuelve counts; el wrapper convierte
   a USD.
2. **Sustituir proveedor sin tocar el resto del código**: si se
   quisiera evaluar GPT-4 o Gemini en trabajo futuro, bastaría con
   reimplementar `complete` sobre el SDK correspondiente.
3. **Inyectar `MockLLMClient` en tests**: los tests unitarios no
   consumen créditos API. Corren en ≈ 10 segundos sin depender
   de la red.

## 7.7 Particionado federado hash-based con semilla determinista

**Decisión**: `partial_visibility_split(combined, num_exchanges=3,
seed=42)` asigna cada dirección a exactamente un exchange
mediante `hash(str(v)) % 3`.

**Motivación**:
1. **Realismo regulatorio**: replica la asimetría post-MiCA descrita
   en el Capítulo 2 —cada exchange observa sólo transacciones
   incidentes en sus usuarios KYC, sin acceso a las tablas KYC de
   competidores—.
2. **Determinismo**: la misma dirección siempre cae en el mismo
   exchange sea cual sea la corrida, permitiendo comparar detectores
   entrenados independientemente.
3. **n = 3 balance específico**: n = 1 colapsa al escenario
   centralizado no federado; n ≥ 10 produce subgrafos
   demasiado ralos para entrenar el GCN local. Con n = 3 cada
   exchange observa aproximadamente el 33 % de las direcciones
   y ≈ 55 % de las aristas.

**Alternativa considerada**: asignación basada en volumen (exchanges
proporcionalmente al *market share* real). Descartada por
introducir variables confundidoras: el detector tendría más señal
en el exchange grande solo por tamaño.

## 7.8 LOCO-CV como métrica primaria vs split 80/20

**Decisión**: reportar Leave-One-Campaign-Out cross-validation
(LOCO-CV) como métrica principal de detección binaria, y limitar
el split 80/20 a rol de referencia comparativa con la literatura
previa.

**Motivación**:
1. **Los benchmarks académicos anteriores sobreestiman**: la
   literatura AML reporta sistemáticamente F1 > 0,90 sobre
   datasets con menos de 30 actor clusters. Nuestros propios
   experimentos muestran que sobre la simulación propia el GCN cae
   de F1 = 0,97 (split 80/20) a F1 = 0,42 (LOCO-CV) —
   ΔF1 = -0,55 absoluto—.
2. **LOCO mide generalización real**: cada campaña C_j actúa como
   test set con las demás como training, evitando la memorización a
   nivel de campaña.
3. **Sobre EthereumHeist real** la degradación es menor
   (ΔF1 = -0,31) pero sigue siendo significativa y valida el
   patrón cross-dataset.

**Auditoría publicable**: los cuatro chequeos del script
`scripts/audit_f1_memorization.py` (solapamiento train/test,
Cohen's d por feature, LOCO completo, baseline mixer-only) son el
argumento cuantitativo detrás del *finding* metodológico principal
del §8.10 del Capítulo 8.

## 7.9 Threshold calibrado (0,6) para Louvain

**Decisión operativa**: el defensor de producción se despliega con
Louvain (baseline no supervisado) con threshold de clasificación
0,6 en lugar del 0,5 por defecto.

**Motivación empírica** (§8.9.6 del Capítulo 8):
1. **Sobre background Sepolia real** (1 779 direcciones seed 100,
   7 319 seed 500), el threshold 0,5 produce FPR = 99,78 %
   / 99,95 % respectivamente —completamente inservible—.
2. **Subir a 0,6 baja el FPR a 0,06 %** manteniendo
   recall > 92 %. La distribución de scores Louvain es
   esencialmente binaria: casi todas las addresses reciben 0,5
   (comunidad no distinguible) o 1,0 (comunidad distintiva);
   subir el umbral elimina la masa masiva de 0,5 y preserva
   los atacantes.
3. **GCN no admite fix comparable**: ni threshold sweep, ni retrain
   con background, ni más épocas bajan el FPR a < 1 % con
   recall razonable. Rescate requiere trabajo mayor reservado a
   §10.4.

**Alternativa descartada**: ensemble Louvain(0,6) AND
GCN(0,5). Reproduce Louvain(0,6) sin ganancia, con complejidad
operativa añadida.

## 7.10 Persistencia inmediata de mixer notes (post-2026-08-18)

**Decisión**: el `ToolDispatcher` acepta un parámetro `notes_file:
Path | None` que, cuando está definido, persiste cada `deposit_note`
a un fichero JSONL en el instante en que el `receipt` del deposit
se confirma.

**Motivación empírica** (§8.9.5 seed 500 del Capítulo 8):
- Sobre Sepolia, 9/10 retiradas del mezclador fallaron por
  desincronización de raíz Merkle bajo latencia 12s / rate limits
  del RPC.
- El sub-agente Layering perdió el `halt` después de los errores
  repetidos.
- Las 9 notes correspondientes vivían sólo en el contexto
  conversacional del sub-agente y desaparecieron con él, dejando
  $16 870 USD locked permanentemente en el contrato.
- La persistencia inmediata garantiza que cualquier fallo posterior
  (halt del sub-agente, crash del runner, eviction de contexto)
  no puede destruir la información necesaria para reclamar el ETH.

El `scripts/mixer_recover.py` complementario lee el JSONL y ejecuta
`mixer_withdraw` sobre las notes cuyo nullifier no está aún marcado
como *spent* on-chain.

## 7.11 Reverse swap USDT → ETH en el sweep operativo

**Decisión**: `scripts/sweep_sepolia.py` incluye una fase opcional
final (por defecto activa) en la que el deployer, tras acumular
USDT residual de todas las clean exits swept, aprueba al pool
`MockUniswapV2Pool` y ejecuta `swapUSDTForETH` para convertir el
stablecoin de vuelta a ETH.

**Motivación**:
1. Antes del fix, cada corrida perdía ≈ 1,5 ETH por corrida
   en el pool (USDT que quedaba en las clean exits, recuperable
   como USDT pero no como ETH).
2. Con el reverse swap, la pérdida real cae a ≈ 0,07 ETH
   (gas + 0,3 % fee del pool + 2 % slippage máximo).
3. Multiplica por ≈ 20× el número de corridas Sepolia
   posibles antes de necesitar re-faucet.

## 7.12 Resumen — el hilo conductor

Las once decisiones anteriores comparten un patrón subyacente: cada
una emerge de un *fallo empírico* concreto (memorización 80/20 en
seed 200; contaminación deployer en seed 100 pre-fix; bucle
patológico burners seed 306; colapso FPR sobre Sepolia real seed
100+500; pérdida de 9 ETH seed 500), no de un requisito abstracto
*ex ante*. El diseño del sistema es *evidence-driven* —cada
constante *locked*, cada invariante del dispatcher, cada umbral
calibrado tiene su corrida experimental de referencia—. Este es el
patrón metodológico que el §8.10 del Capítulo 8 sistematiza como
contribución independiente del proyecto.

---

## 7.B Conclusiones, findings y trabajo futuro

Este capítulo recapitula las cuatro contribuciones del Trabajo Fin de
Máster tal como han quedado sustanciadas por los resultados del
Capítulo 8, discute las lecciones metodológicas derivadas del proceso
experimental, y traza un programa de trabajo futuro concreto
articulado sobre las limitaciones identificadas.

## 10.1 Recapitulación de las contribuciones

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
Capítulo 8 §8.10 formaliza tres *findings* publicables independientemente
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

## 10.2 Trade-off central: interpretabilidad frente a métrica cuantitativa

La discusión del Capítulo 8 §8.8 identifica el hallazgo empírico que
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

## 10.3 Lecciones metodológicas

Del proceso experimental se extraen tres lecciones metodológicas
aplicables al campo más allá del sistema específico propuesto.

**Lección 1 — LOCO como métrica primaria en *datasets* pequeños**. La
literatura AML sobre grafos ha reportado sistemáticamente F1 > 0,90
sobre *datasets* con menos de 30 actor clusters, incluyendo trabajos
publicados sobre Elliptic (Weber et al. 2019), EthereumHeist (Wu et
al. 2023) y OpenAML v1 (FINOS 2025). Los resultados del
Capítulo 8 §8.5 sugieren que esas cifras sobreestiman el rendimiento
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

## 10.4 Trabajo futuro

Las cuatro limitaciones identificadas en el Capítulo 8 §8.11 se
traducen en un programa de trabajo futuro concreto.

### 10.4.1 Extensión temporal del pipeline

**Motivación**: el clasificador GCN actual opera sobre snapshots
estáticos del grafo. Un atacante consciente del detector puede
explotar la temporalidad para dispersar transacciones a lo largo de
meses, quedando por debajo del umbral de detección por ventana
temporal.

**Propuesta**: sustitución del GCN por una arquitectura *temporal
graph network* (TGN, Rossi et al. 2020) o *dynamic graph neural
network* que incorpore *timestamps* como features nativas y mantenga
memoria por nodo entre ventanas. La incorporación es incremental
—el resto del pipeline (`partial_visibility_split`, `LLMDefenderCoordinator`)
es agnóstico al detector local—.

**Esfuerzo estimado**: 3-4 meses de trabajo adicional, incluyendo un
generador de campañas atacantes temporalmente distribuidas para
evaluar la ganancia.

### 10.4.2 Simetría LLM completa: Opus-vs-Opus

**Motivación**: la evaluación reportada emplea Opus 4.7 en el
atacante y Sonnet 4.6 en el defensor por constricciones de presupuesto
del TFM (≈ 50 USD totales). Un experimento simétrico Opus-vs-Opus
permitiría evaluar el efecto de tamaño de modelo aislado de la
asimetría de rol.

**Propuesta**: replicar las evaluaciones del Capítulo 8 §8.6 con Opus
4.7 en ambos roles. Estimación de coste extrapolando linealmente:
≈ 6 USD por corrida de 20 campañas simuladas + ≈ 3 USD por
corrida EthereumHeist con `--exclude-big-hacks`. Con cinco
repeticiones para varianza, el experimento cuesta ≈ 45 USD.

**Esfuerzo estimado**: 1-2 semanas de trabajo, con la mayor parte del
tiempo en re-ejecución y análisis.

### 10.4.3 Escalado a régimen ≥ 100 campañas

**Motivación**: el *dataset* simulado propio contiene 20 campañas
atacantes sobre 10 311 nodos, comparativamente pequeño frente a los
10⁷ nodos de AMLWorld (Altman et al. 2023). La extrapolación de
las conclusiones a regímenes de mayor escala requiere experimentación
adicional.

**Propuesta**: generar 100-500 campañas atacantes adicionales,
combinando: (i) escenarios pre-definidos existentes con variaciones
paramétricas; (ii) escenarios nuevos que cubran las tipologías FATF
5-8 (privacy coins, jurisdicciones no cooperativas, rapid pass-through,
P2P/OTC cash-out) actualmente no simuladas.

**Esfuerzo estimado**: 4-6 semanas, con coste LLM estimado en
150-300 USD para el pipeline atacante y50-100 USD para las
evaluaciones defensivas.

### 10.4.4 Extensión cross-chain y multi-token

**Motivación**: el sistema actual opera exclusivamente sobre
Ethereum con un único ERC-20 (`MockUSDT`). Las tipologías reales
modernas combinan múltiples cadenas (Ethereum, Tron, Solana) y
múltiples tokens (USDT, USDC, ETH, WBTC).

**Propuesta**: extensión del grafo a un grafo heterogéneo con nodos
por (cadena, token) y aristas que codifican transferencias
intra-cadena, swaps DEX y bridges cross-chain reales. Requiere:
(i) implementación de adapters `src/aml/chains/tron.py` y análogos
para las cadenas objetivo; (ii) sustitución del GCN por una
*heterogeneous graph neural network* (HGT, Hu et al. 2020); (iii)
extensión del catálogo de herramientas atacante con primitivas
cross-chain reales (no mock).

**Esfuerzo estimado**: 6-9 meses, más presupuesto significativamente
mayor (>$500 USD) por el volumen de datos multi-cadena.

### 10.4.5 Memoria histórica del defensor

**Motivación**: cada evaluación del pipeline defensivo actual es
independiente —el detector no aprende de flags previos. Un defensor
productivo mantendría estado entre bloques y podría refinar sus
decisiones basándose en direcciones flageadas históricamente.

**Propuesta**: incorporación de una capa de memoria persistente al
`LLMDefenderCoordinator` mediante *retrieval-augmented generation*
(RAG) sobre una base de datos de flags históricos. El coordinador
consultaría, para cada nueva evaluación, las direcciones o clusters
similares vistos anteriormente y su resolución final (confirmado
como ilícito / falso positivo).

**Esfuerzo estimado**: 2-3 meses, con el reto principal en el diseño
del sistema de embeddings + retrieval que preserve la restricción de
visibilidad parcial federada.

### 10.4.6 Arquitectura para despliegue en producción

**Motivación**: el pipeline actual se ejecuta como *scripts batch*
locales (entorno conda + Python + API LLM remota). No existe
`FastAPI`, no hay `Docker`, no hay servicios corriendo en background:
cada evaluación arranca los procesos, entrena los GCN de novo,
consulta el LLM vía HTTPS y termina. Este *setup* es adecuado para
research reproducible pero insuficiente para un despliegue operativo
en entidades reguladas (exchanges centralizados, VASP, unidades de
inteligencia financiera) que requieren latencia acotada,
disponibilidad continua y trazabilidad auditable.

**Propuesta**: encapsular el pipeline en una arquitectura de
microservicios con las siguientes capas:

1. **API REST** (`FastAPI`): endpoint `POST /detect` que recibe una
   lista de direcciones más el subgrafo relevante y devuelve las
   alertas junto con el *reasoning* del coordinador LLM; endpoint
   `GET /alerts` para consulta histórica y compliance auditing.
2. **Persistencia de modelos GCN**: entrenamiento único por exchange
   con `torch.save` y *hot-swap* en el servicio, eliminando el
   re-entrenamiento por *request* (actualmente ~5 min por exchange).
3. **Cache de respuestas LLM** (`Redis`): *responses* del coordinador
   cacheadas por *hash* del prompt para reducir coste API en
   consultas repetidas sobre las mismas direcciones flageadas.
4. **Base de datos de alertas** (`PostgreSQL`): persistencia auditable
   de decisiones + prompts + *timestamps* + resolución final, requerida
   por la Recomendación FATF 11 (*record keeping*) y por MiCA Art. 68
   sobre trazabilidad de sistemas automatizados.
5. **Cola asíncrona** (`Celery` + Redis, o `Kafka` para *streaming*):
   procesamiento *batch* nocturno de lotes grandes, o pipeline
   *streaming* en tiempo casi real para exchanges con SLA de
   detección < 10 min.
6. **Contenedores**: `Dockerfile` por servicio + `docker-compose` para
   desarrollo local + `Helm charts` para despliegue en `Kubernetes` de
   producción, con réplicas horizontales para los servicios de
   inferencia GCN.
7. **Observabilidad**: `Prometheus` para métricas (latencia por
   *request*, coste LLM por día, *precision drift* sobre alertas
   confirmadas) y `Grafana` dashboards para el equipo *compliance*.

**Contraste con el estado actual del TFM**. El sistema descrito en
los Capítulos 3 y 4 no incluye ninguno de estos componentes: los
seis modelos (Louvain, GCN, GAT, cosine, LLM coordinator) viven en
Python *in-process*, los resultados se serializan a JSON en disco y
la evaluación termina en un exit code. Esta simplicidad es
deliberada —el objetivo es reproducibilidad académica, no
producción— pero implica que un adoptante industrial debería
construir toda la capa de servicio antes de operar el sistema en
un exchange real.

**Esfuerzo estimado**: 6-9 meses de trabajo *full-stack* adicional
(Python backend + DevOps + integración con los proveedores KYC del
exchange objetivo), con presupuesto de infraestructura 100-500
USD/mes según carga. La extensión no aporta contribución académica
pero es el paso natural para transferir el trabajo a un proveedor
comercial de *compliance* (Chainalysis, TRM Labs, Elliptic) o a la
capa AML interna de un exchange centralizado.

### 10.4.7 Cobertura regulatoria ampliada

**Motivación**: este trabajo aborda las Recomendaciones FATF 16
(*travel rule*) y 20 (transparencia SAR) explícitamente, pero no las
Recomendaciones 10 (customer due diligence) ni 11 (record keeping), y
sólo parcialmente MiCA.

**Propuesta**: extensión del pipeline con componentes que aborden
CDD (integración con proveedores de identidad on-chain como
Chainlink Identity o Polygon ID), record keeping (persistencia
auditable de decisiones + prompts en formato IPFS o similar), y
generación automática de reportes SAR en el formato requerido por
las UIF europeas.

**Esfuerzo estimado**: proyecto autónomo de doctorado o
industria-academia, no encaja en el alcance de un TFM.

### 10.4.8 *Mock pool* con precio conectado a oráculo

**Motivación**: el `MockUniswapV2Pool` desplegado en Sepolia usa la
matemática *constant-product* estándar de Uniswap V2 (`x · y = k`), y
su *spot price* queda **congelado** en el ratio de reservas fijado
durante el `bootstrap()` — no consulta ningún oráculo externo. En
Uniswap V2 real este mecanismo funciona porque una capa continua de
arbitrajistas mantiene el *spot* del *pool* alineado con el precio
verdadero de los agregadores (Chainlink, DEX Screener) — cualquier
desvío es explotado en segundos. En el *mock*, sin esa capa de
arbitraje, el *spot* solo se corrige (a) por los *swaps* del propio
atacante durante la campaña, o (b) redesplegando el contrato con
reservas nuevas alineadas al oráculo.

Durante esta memoria se ha adoptado el enfoque (b) — el *script*
`scripts/topup_sepolia_pool.py` lee `data/prices/eth.csv` (oráculo
CoinGecko) y calcula la reserva USDT como `eth_amount ×
oracle_price` para que el `bootstrap()` produzca un *spot* consistente
con el mercado del día. La consecuencia es que **cada vez que el
precio real deriva más del ~15% desde el último despliegue**, hay
que ejecutar `drain_sepolia_pool.py` + `topup_sepolia_pool.py` para
resincronizar. Es funcional pero manual, y consume ETH del deployer
en cada redespliegue (recuperable al final del TFM).

**Propuesta**: reemplazar el *mock pool* actual con una de las
siguientes tres alternativas, ordenadas por rigor creciente:

1. *Owner-triggered reset* (bajo esfuerzo, 15 min de desarrollo).
   Añadir al contrato una función `resetReserves(uint256 newEth,
   uint256 newUsdt)` protegida por `onlyOwner` que actualice los
   *reservas* sin necesidad de redeploy. El operador ejecuta esta
   función pre-corrida en lugar de desplegar un contrato nuevo,
   ahorrando ~0,003 ETH de gas de deploy y preservando la dirección
   del *pool* entre corridas (importante para el análisis histórico
   on-chain de la campaña).

2. *Chainlink integration* (esfuerzo medio, ~2 h de desarrollo +
   configuración) — **IMPLEMENTADO durante el desarrollo del TFM,
   véase §8.9.18**. El contrato `MockOraclePool.sol` incorpora una
   referencia inmutable a un `AggregatorV3Interface` (Chainlink
   ETH/USD feed en Sepolia,
   `0x694AA1769357215DE4FAC081bf1f309aDC325306`) y calcula cada
   *swap* al *spot rate* exacto reportado por
   `latestRoundData()`. USDT se mintea *on-demand* vía
   `MockUSDT.mint()` permissionless en la dirección de *ETH→USDT*;
   ETH sale del *reserve* del contrato en la dirección inversa
   (*bootstrap* mínimo de 0,5 ETH). El *zero slippage* elimina por
   completo el sesgo metodológico documentado en §10.4.9. Coste
   extra: ~30-40k gas por *swap* (~$0,30) por la llamada al
   oráculo. Coste ahorrado: ~$2 500-4 800 por campaña de 20 ETH
   por eliminación del *slippage-artefacto*.

3. *Simulated arbitrageur bot* (esfuerzo alto, ~1 semana). Un
   servicio externo que monitoriza el *pool* on-chain y ejecuta
   *swaps* correctivos cuando el *spot* del *pool* se desvía > 1%
   del *spot* de Chainlink. Reproduce fielmente la dinámica de
   mercado real y permite estudiar cómo un atacante LLM interactúa
   con arbitrajistas competidores (interesante línea de
   investigación aparte).

**Esfuerzo estimado**: (1) 15 min, (2) 2 h, (3) 1 semana. La opción
(1) es candidata inmediata para un *pull request* de mejora
operativa; (2) y (3) merecen un TFM propio o proyecto industria.

### 10.4.9 *Mock pool* con profundidad realista para *swaps* a escala real

**Motivación**: relacionado pero distinto del anterior. El
`MockUniswapV2Pool` desplegado tiene profundidad **finita**
(típicamente 5 ETH + reserve equivalente en USDT), impuesta por el
*budget* del *deployer* en Sepolia (∼22 ETH totales). En Uniswap V2
mainnet, en cambio, el par ETH/USDT tiene profundidad del orden de
**10 000 ETH** — un *swap* de 10 ETH representa 0,1% de las reservas
y produce *slippage* < 0,05%. En el *mock*, un *swap* de 10 ETH
representaría el 200% de las reservas (imposible: la fórmula
*constant-product* lo rechaza).

Consecuencia práctica: para campañas de escala real (≥ 10 ETH
laundered), el atacante debe fragmentar los *swaps* en decenas de
*chunks* pequeños (0,1-0,3 ETH cada uno) para mantener el *slippage*
por operación bajo. Cada *chunk* paga las fees Uniswap del 0,3%, y
los *chunks* consecutivos empeoran la posición del *pool* (después
de meter 1 ETH, el ratio se ha movido y el siguiente *chunk* enfrenta
*slippage* mayor que el anterior). Empíricamente, un *swap* fraccionado
de 15 ETH en el *mock* de 5 ETH de profundidad genera *slippage*
efectivo agregado del ∼10-15% — vs. el ∼0,05% que tendría en Uniswap
mainnet con la misma cantidad. Ese ∼10-15% se manifiesta en la
métrica `economically_lost_pct` de la campaña (documentada en
§8.9.16) y es artefacto del *mock*, no señal de una campaña
subóptima.

Este *finding* tiene dos implicaciones metodológicas:

1. **Las métricas de eficiencia económica** (`delivered_to_exits_pct`,
   `economically_lost_pct`) reportadas para *runs* Sepolia deben
   interpretarse **con la profundidad del pool como covariable**. Un
   82% de *delivered* con un *pool* de 5 ETH no equivale al 82% con
   un *pool* de 500 ETH — el primero incorpora 10-15 puntos
   porcentuales de *slippage-artefacto*; el segundo estaría cerca
   del 95-97% para el mismo comportamiento del atacante.

2. **El *dataset* Anvil resulta epistemológicamente superior para
   *ablations*** de la política del atacante: en Anvil no hay
   restricción de *budget* sobre las reservas del *pool*, así que se
   puede desplegar un *mock* con 10 000 ETH + 25M USDT y evaluar el
   comportamiento del atacante bajo *slippage* ~0. Sepolia queda como
   demostración de *end-to-end* on-chain, con la advertencia
   explícita de que sus métricas económicas están sesgadas por la
   profundidad del *pool*.

**Propuesta**: cuatro opciones no exclusivas:

1. **Escalar el *mock pool* en Anvil** al orden de 10⁴ ETH (posible
   sin coste real) y reportar la métrica `delivered_to_exits_pct`
   por separado para `run_type ∈ {Sepolia thin pool, Anvil deep
   pool}`. Permitiría descomponer el efecto de la política del
   atacante del efecto de la fricción del *pool*.

2. **Sustituir el *pool constant-product* por un *pool de precio
   constante***: un contrato mock que ejecuta *swaps* a un ratio
   fijo (leído del oráculo) sin drenar reservas — matemáticamente
   equivalente a un *market maker* con liquidez infinita. Rompe la
   analogía con Uniswap V2 pero elimina el sesgo de profundidad para
   experimentos donde el *slippage* no es la variable de estudio.

3. **Modelar el *slippage* explícitamente en el análisis**: incluir
   en `run_sepolia_campaign.py` un post-cálculo que estime cuánto
   *slippage* habría en Uniswap V2 mainnet (según profundidad
   pública actual) y reporte una métrica *counterfactual*
   `delivered_to_exits_pct_at_mainnet_depth` junto a la métrica
   *mock*. Barato de implementar (unas 30 líneas), permite comparar
   *mock* vs realidad sin cambiar el contrato.

4. **Publicar la relación empírica *slippage-artefacto* vs. razón
   `campaign_amount / pool_depth`** como un *finding* metodológico
   independiente. Cualquier estudio futuro que use un *pool mock*
   para simulaciones AML tendría una tabla de referencia para
   dimensionar sus experimentos.

**Esfuerzo estimado**: (1) 1 día, (2) 2-3 h, (3) 1 h, (4)
compatible con este TFM (una tabla adicional en §8.9). Recomendación:
implementar (3) como mejora inmediata para el próximo *release* del
*pipeline*, y (1) + (4) como líneas de trabajo futuro más rigurosas.

### 10.4.10 Auditoría formal de mecanismos de *automated cleanup* en pipelines AML multi-agente

**Motivación**: §8.9.21 documenta un *finding* metodológico
independiente del sistema propuesto — el *anti-strand co-funding
leak* — en el que un mecanismo automático de recuperación de fondos
introdujo una firma criminalística (Alice → 47 salidas directas) que
ninguna métrica económica estándar (`delivered_pct`, `recovery_pct`,
`slippage`) detectaba. La lección generalizable es que **cualquier
capa de gestión de estado post-hoc en un sistema AML de agentes
puede reintroducir *trazabilidad* que las etapas de *placement* +
*layering* + *integration* habían roto**, y este riesgo no aparece
en la evaluación estándar porque las métricas de éxito son
económicas (¿cuánto llegó a las salidas?) mientras que la
vulnerabilidad es criminalística (¿quién financió a quién?).

**Propuesta**: definir formalmente un *audit protocol* que todo
sistema de laundering multi-agente debería pasar antes de reportar
sus métricas de evasión. El protocolo mínimo debería verificar:

1. **No hay wallet con nonce anómalamente alto en el rango temporal
   de la campaña** (identifica funders forzados).
2. **No hay clusters `k-core > 1` centrados en la fuente original**
   (identifica co-funding leaks).
3. **La distribución empírica de gas dust recibido por las salidas
   no forma una moda estrecha** (0.005 ETH exacto en 47 salidas es
   evidencia de un script, no de un humano rebalanceando).
4. **La ventana temporal de financiamiento de salidas no está
   contenida en < 100 bloques** (indica script automatizado post-hoc,
   no dispersión temporal humana).

**Aportación al campo**: sería, hasta donde se conoce, la primera
propuesta de *audit protocol* específico para pipelines LLM
adversarial AML. Formaría un anexo directamente publicable como
short paper en un venue de *security* (USENIX Security workshop
sería el candidato natural).

**Esfuerzo estimado**: 2 semanas — diseño formal del protocolo +
implementación como *linter* automático que corre sobre `chain_trace.jsonl`
+ evaluación cruzada sobre las 20 corridas del *dataset*.

### 10.4.11 Meta-transacciones (EIP-4337) para eliminar el *gas dust leak*

**Motivación**: §8.9.22 identifica el mecanismo que forzó el
*co-funding leak*: cada wallet de salida en un pipeline AML clásico
necesita ETH nativo propio para poder emitir su primera
transacción. Sin ese *gas dust*, la wallet retiene USDT pero es
incapaz de moverlo (*stranded*). El *fix* P1-41 mueve la fuente del
gas desde Alice a un pool de funders aleatorios, lo cual rompe el
cluster co-funding pero **no elimina la señal**: el detector aún ve
que 47 wallets recibieron 0.005 ETH cada una desde un conjunto
pequeño de fuentes.

**Propuesta**: reemplazar el modelo de *externally-owned accounts*
(EOA) por *smart contract accounts* (SCA) compatibles con
**EIP-4337 (Account Abstraction)**. En este modelo, la wallet de
salida:

- No tiene ETH nativo propio.
- Firma una `UserOperation` (no una transacción convencional).
- Un *bundler* (Pimlico, Biconomy, Gelato) empaqueta la operación
  y paga el gas al validador.
- Un *paymaster* — que puede ser el mismo *bundler*, un contrato
  patrocinador, o un contrato que cobra en el token que se está
  moviendo (USDT en nuestro caso) — reembolsa al *bundler*.

**Ganancia AML-adversarial**: cero pistas on-chain de que las 47
salidas están relacionadas por origen de gas. El detector
Louvain sobre el subgrafo ETH deja de tener aristas dust-funding, y
la única señal restante es semántica (patrón sub-\$999) — que es
exactamente la que se pretende que el LLM defensor detecte.

**Ganancia AML-defensor**: si se demuestra que ataques AML
sofisticados usarán EIP-4337 en próximas generaciones, el defensor
debe aprender a razonar sobre `UserOperation` events y no solo
sobre `Transfer(from, to, value)` events. Esto motiva una extensión
del dataset (Elliptic++ no incluye UserOps porque son de 2023 en
adelante) y una modificación del *feature extractor* del GCN.

**Esfuerzo estimado**: 3 semanas — integración con SDK del bundler
Pimlico (2 semanas) + modificación del feature extractor del
defensor para procesar `UserOperationEvent` (1 semana). Publicable
como *system paper* en ACM CCS AISec workshop.

### 10.4.12 *Capture-all Sepolia dataset* para evaluación adversarial del defensor

**Motivación**: §8.11 (Limitación 5) documenta que el
`chain_trace.jsonl` capturado en cada corrida Sepolia se filtra a
transacciones que involucran wallets del sistema (Alice, deployer,
funders, burners, exits) y sus contratos (pool, mixer, USDT),
descartando el tráfico paralelo. Un análisis empírico durante el
smoke 801 mostró que en un bloque típico (11 643 800) hay 146 tx
totales, de las cuales sólo 3 son nuestras; extrapolado al *span*
completo de una campaña son del orden de 19 000 tx benignas
concurrentes no indexadas. El defensor evaluado sobre nuestros
datasets Sepolia obtendría un F1 trivial de 1.0 porque el 100% del
subgrafo capturado es atacante.

**Propuesta**: implementar un pipeline `capture_all_sepolia.py`
que, durante o después de una campaña, indexe **todas** las
transacciones del rango de bloques (no sólo las nuestras),
etiquetando:

- `label = "attacker"` para las 100–300 wallets registradas en
  `addresses.json` (Alice, deployer, funders, burners, exits) más
  las contract calls a nuestros `pool` / `mixer` / `USDT`.
- `label = "benign"` para todo el resto del tráfico Sepolia
  concurrente.

**Beneficio metodológico**:

1. **Primer *dataset* AML adversarial con tráfico *testnet* real**
   —hasta donde se conoce, ninguna publicación previa combina un
   pipeline atacante multi-agente LLM con un *background* de
   testnet en vivo. Es un artefacto publicable
   independientemente del defensor propuesto en este TFM.

2. **Evaluación del defensor con clase desequilibrada realista**
   (aguja en un pajar de ~19 000 : 300). El F1 sobre esta
   distribución es informativo del rendimiento en producción,
   donde el ratio ilícito/benigno mainnet es del orden de 0.5%–2%.

3. **Robustez de la comparación con *baselines***. El *baseline*
   coseno de §8.5 obtiene métricas altas sobre Elliptic++ porque el
   *dataset* está pre-etiquetado; enfrentarlo a un subgrafo Sepolia
   sin etiquetas iniciales expone su dependencia de features
   pre-computadas y motiva el diseño del defensor híbrido GCN+LLM.

**Caveat metodológico obligatorio**: el tráfico benigno de Sepolia
es *dev-testnet* (despliegues, faucet claims, tests aleatorios), no
representativo del tráfico de usuarios en producción (transferencias
P2P, *swaps*, *staking*, uso de exchanges). Cualquier publicación
sobre este *dataset* debe incluir un *disclaimer* explícito y
reportar métricas separadas para (a) Sepolia como stress-test de
generalización, (b) Elliptic++ / OpenAML como benchmark de dominio.

**Esfuerzo estimado**: 1–2 semanas — implementar el
`capture_all_sepolia.py` con paginación de bloques vía
`eth_getLogs` + Alchemy `getAssetTransfers` (2–3 días), diseño del
esquema de etiquetado y almacenamiento (~5 GB / campaña, ~50 GB para
20 campañas, 1 día), evaluación del defensor sobre el nuevo
*dataset* (3–5 días). Publicable como *dataset paper* en NeurIPS
Datasets & Benchmarks track.

### 10.4.13 Humanización temporal del atacante (P1-45 / P1-46 / P1-51)

**Motivación**: §8.9.24 documenta que el pipeline atacante actual
completa una campaña 22.6 ETH en ~60 min de wall-clock con
transacciones espaciadas por `advance_blocks(2-5)` uniforme
(~24-60 s entre tx). Esta cadencia regular es en sí misma un
fingerprint automation — humanos operan en ráfagas irregulares
con pausas 0-24 h entre acciones. Además, todas las tx pagan el
mismo priority fee (2 gwei), otro fingerprint.

**Propuesta**: tres refinamientos temporales:

- **P1-45 — Jittered inter-tx delays**. Reemplazar
  `advance_blocks(2-5)` uniforme por
  `sleep(random.exponential(scale=300))` con outliers hasta 24 h
  para el 5 % de tx. La distribución exponencial replica el patrón
  Poisson de arribos humanos observado en Chainalysis 2024.

- **P1-46 — Human-hour operating window**. Los `run_sepolia_campaign.py`
  fires 24/7. Real launderers concentran actividad en horas laborables
  regionales (08:00-22:00 UTC±3). Requiere gating por
  `datetime.utcnow().hour` en el dispatcher.

- **P1-51 — Variable priority fee**. Reemplazar `priority = 2 gwei`
  constante por sampled `random.choice([0.5, 1, 1.5, 2, 3, 5])`.
  Un usuario real varía prioridad según urgencia percibida.

**Alcance**: NO se implementa en Sepolia para el TFM actual — el
deadline no permite validación empírica temporal (necesitaría corridas
multi-día). Sí se implementará y validará en **Anvil**, donde el
control de tiempo es total (chain clock advanciable). El impacto forense
en Anvil se documentaría como *ablation study* separado.

**Esfuerzo estimado**: 3-5 días código + 1 semana ablation Anvil.

### 10.4.14 Cross-chain bridge integration (P1-53)

**Motivación**: los perfiles Lazarus / Ronin / Nomad reales mueven
20-30 % del volumen lavado a través de puentes cross-chain (Wormhole,
Nomad, Ronin Bridge, LayerZero). Nuestro pipeline actual es 100 %
ETH-only en Sepolia — un fingerprint «no bridge usage» que un
detector avanzado detecta como amateur. Ya tenemos `MockBridge.sol`
deployed pero no utilizado por Sonnet.

**Propuesta**: añadir al `LAYERING_SYSTEM` prompt una nueva Route E
(bridge) con budget 10-15 % del `alice_amount`, e implementar la
`bridge_deposit` / `bridge_withdraw` tool que interactúe con
`MockBridge`. Extender el dispatcher para trackear cross-chain flows
(`from_chain`, `to_chain`, `bridge_tx`) en el `chain_trace.jsonl`.

**Alcance**: fuera del scope del TFM actual pero recomendado como
próximo release. **Esfuerzo estimado**: 1 semana desarrollo + 3 días
validación empírica. Publicable como extensión del pipeline para
representar Lazarus-tier sophistication (~ Ronin Bridge hack 2022).

### 10.4.15 Simulated operator mistakes (P1-50)

**Motivación**: nuestro pipeline ejecuta el 100 % de sus tx sin
errores. Real humanos comenten errores realistas: enviar a wrong
address y luego mandar dust request (~1 % de campaigns Chainalysis),
retry con higher gas (~5 %), cancel un pending tx (~2 %), over-slippage
por error de cálculo (~3 %). **Cero errores** en 20 corridas es un
fingerprint estadísticamente detectable.

**Propuesta**: inyectar errores realistas con probabilidad tunable:

- 3-5 % de `transfer_usdt` calls con typo simulado en `to_address`
  (enviar a `0x…41` cuando debía ir a `0x…4F`), seguido de una
  segunda tx corrigiendo (recall pattern).
- 2-4 % de tx con `min_out` demasiado agresivo → revert, retry
  con mejor slippage.
- 1-2 % de `mixer_deposit` con recovery-note perdida (Sonnet
  intenta withdraw pero encuentra note ya spent).

**Riesgo**: puede degradar reliability del run (más iteraciones, más
budget). Requiere validación cuidadosa de que las «mistakes» no
inflan artificialmente `economic_loss_pct` más allá de baseline.

**Esfuerzo estimado**: 1 semana implementación + 1 semana ablation.

### 10.4.16 Time-lagged multi-week exchange distribution (P1-52)

**Motivación**: Sonnet fondea las 3-4 exchanges (Binance, Coinbase,
Kraken) en la misma window de ~60 min. Real Lazarus ops distribuyen
across days: fondean Binance esta semana, Coinbase la próxima, Kraken
la tercera. Cero temporal spread es fingerprint automation.

**Propuesta**: multi-campaign coordination donde el Coordinator
persiste estado entre `run_sepolia_campaign.py` invocaciones y ejecuta
la misma campaign lógica en 3-5 ventanas separadas por 3-7 días
cada una. Requiere estado atómico persistente (extension del
`dispatcher_state.json`) + orquestador cron externo.

**Alcance**: fuera del scope del TFM (requiere runs multi-semana).
Publicable como extension propuesta.

**Esfuerzo estimado**: 2 semanas coordination framework + 2 semanas
validación empírica.

### 10.4.17 P1-55 — Enforcement en código de patterns críticos

**Motivación**: §8.9.28 documenta que Sonnet ejecutó 0 llamadas a
`distribute_to_exits` (la nueva *tool* atómica P1-43) durante seed 803,
a pesar de que el prompt de `INTEGRATION_SYSTEM` fue actualizado con la
instrucción explícita «PREFER distribute_to_exits over manual
consolidation». En su lugar, Sonnet ejecutó 66 llamadas individuales
a `transfer_usdt` que replicaron el patrón hub-and-spoke que
`distribute_to_exits` fue diseñado para prevenir.

**Finding metodológico generalizable**: prompt engineering, incluso con
lenguaje directivo («PREFER», «MUST»), NO garantiza adopción en
sub-agentes LLM cuando existe un patrón conocido más simple que
el modelo puede ejecutar autonómamente. La **default policy learned**
sobrepasa la instrucción textual. Este finding es publishable
independientemente del sistema propuesto — implicaciones para
cualquier operator que despliegue LLM-driven pipelines con features
nuevas.

**Propuesta P1-55**: implementar *code-level enforcement* que
redirija a Sonnet cuando use el patrón subóptimo:

```python
def _transfer_usdt(self, from_addr, to_addr, amount_usdt):
    # P1-55: soft-block direct transfer_usdt to registered exits
    # when there are unfunded exits pending. Redirige a
    # distribute_to_exits vía error message educativo.
    exits = self.registered_clean_exits
    unfunded = [e for e in exits
                if self.w3.eth.get_balance(e['address']) == 0
                and self.usdt.functions.balanceOf(e['address']).call() == 0]
    if to_addr.lower() in {e['address'].lower() for e in exits}:
        if len(unfunded) >= 5:  # only enforce when batch worth it
            return ToolResult(error=(
                "P1-55 SOFT BLOCK: 5+ registered exits still unfunded. "
                "Use distribute_to_exits(source_wallets=[...]) instead "
                "of individual transfer_usdt calls — it partitions the "
                "exits across sources, reserves gas per source, and "
                "distributes atomically. Overrides fragmentado producen "
                "el hub-and-spoke pattern P1-43 was designed to prevent."
            ))
    # ... normal flow
```

**Consideraciones**:

- El bloqueo debe ser *soft* (recomendación) para no romper legitimate
  edge cases (e.g., corrections como fase 5 de seed 803).
- El threshold (`5+ unfunded exits`) evita disparar el bloqueo cuando
  solo quedan 1-2 exits por fondear (donde individual transfer_usdt
  es apropiado).
- Publicable como **finding metodológico sobre governance de LLM-driven
  pipelines**: cuando adoptar un feature vía prompt vs. vía code
  enforcement.

**Esfuerzo estimado**: 30 min de código + 30 min de validación en
Anvil + un run empírico en Sepolia para confirmar que Sonnet ahora usa
`distribute_to_exits` post-P1-55.

### 10.4.18 P1-56 — Minimum-quotas para técnicas underused en balanced mix

**Motivación**: §8.9.28 documenta que P1-44 (balanced technique mix)
funcionó parcialmente en seed 803: el uso del mixer bajó exitosamente
de 98% (seed 802) a 40% (dentro del banda 30-45%), pero
`peel_chain` quedó en 8% (bajo del objetivo 25-35%) y `swap_direct`
disparó a 52% (sobre el objetivo 15-25%). Sonnet **compensó** la
reducción del mixer con más swaps ETH↔USDT (una técnica más simple
mecánicamente) en lugar de absorber la reducción con peel_chain.

**Hipótesis explicativa**: `peel_chain` es cara cognitivamente para
el sub-agente porque una sola llamada ejecuta 6-8 hops como una unidad
atómica que requiere planning inicial completo (num_hops, peel_pct,
peel_jitter, asset choice). Múltiples swaps atómicos simples son más
fáciles de emitir individualmente.

**Propuesta P1-56**: reemplazar las bandas objetivo actuales (que
Sonnet interpreta como "sugerencias") con **minimum quotas hard-cap**
en la instrucción de layering:

```
BALANCED TECHNIQUE MIX P1-56 (mandatory MINIMUMS):
  MINIMUM 4 peel_chain calls  (each 5-8 hops, 2-4 % peel_pct)
  MINIMUM 8 mixer_deposit calls
  MINIMUM 3 swap ETH→USDT calls
  MINIMUM 1 cycle back-edge
  If any minimum is unmet before finish_task, the Coordinator
  MUST re-delegate Layering with an explicit reminder.
```

Con minimums en lugar de bandas porcentuales, Sonnet no puede
"omit" una técnica declarando que otra la cubre. También añadir un
**check automático** post-Layering que valide los conteos y
force retry si no se cumplen.

**Esfuerzo estimado**: 20 min de prompt update + 20 min de
`_validate_layering_mix` code + 1 run empírico. Publicable como
extensión metodológica de P1-44.

### 10.4.19 P1-59 — Funder bootstrap via clean-history simulator

**Motivación**: §8.9.32 documenta que en el chain trace de seed 830
aparecen 5 aristas visibles `Deployer → funder wallet` (blocks 10-14,
montos 0.15-0.34 ETH cada uno). Aunque el deployer está tagged como
`operator_wallet` en `addresses.json` (infrastructure, no attacker),
estas aristas SÍ crean un patrón detectable: cualquier defensor que
mire el subgrafo verá 5 wallets recibiendo ETH de una fuente central
y luego funcionando como funders de burners aguas abajo — un patrón
característico de operación coordinada.

En un ataque real, los funders serían **wallets con historia previa**
que el atacante creó semanas/meses antes: recibieron fondos de un
exchange withdrawal, de un OTC purchase, o de un mixer previo con
notas orphaned. No aparecen out-of-nowhere en el chain trace de la
campaña.

**Propuesta**: implementar un `funder_bootstrap_simulator.py` que,
antes de la fase de placement, ejecute para cada funder:

1. Recibe ETH de un "clean source wallet" (fondeado 2-4 semanas
   simuladas antes vía advance_blocks large).
2. Ejecuta 5-15 tx no-relacionadas (transfers a wallets random,
   swaps pequeños ETH↔USDT, quizás una interacción con un
   contract legítimo simulado).
3. Sólo después empieza a operar como funder del pipeline atacante.

**Efecto forense**: la arista Deployer → funder ya NO existe.
Reemplazada por: Deployer → clean_source → funder (después de 5-15
tx history de por medio). El defensor no puede clusterizar los 5
funders como "grupo bootstrap coordinado" porque cada uno tiene
historia individual.

**Esfuerzo estimado**: 2-3 días código + 1 día validación empírica
con re-run seed 830. Publishable como
`funder_provenance_obfuscation` metodológico.

### 10.4.20 P1-62 — Anvil auto-recovery pipeline

**Motivación**: §8.9.32 empíricamente reveló que Sonnet's Layering
sub-agent, presionado por `max_iterations`, deja notas Groth16
orfanas en el mixer (4 notas = ~14 ETH en seed 830). En Sepolia
existe un pipeline post-hoc (`mixer_recover.py` + `sweep_sepolia.py`)
que recupera estos fondos, pero **Anvil no tiene equivalente
integrado en `run_campaign.py`** — el chain se destruye al final,
dejando la aparente pérdida en las métricas del dataset.

**Propuesta**: extender la fase post-coordinator existente en
`aml.attackers.run_campaign` (líneas 269-292) con dos pasos
adicionales, ejecutados sólo si chain_id == 31337 (Anvil):

1. **`mixer_recover_orphans()`**: escanea `dispatcher._mixer_notes`
   por notas con `spent=False`, ejecuta `mixer_withdraw` para cada
   una hacia un fresh recipient. Recupera al mixer contract residual.
2. **`sweep_pool_to_exits()`**: reverse-swap del pool residual
   (USDT balance > threshold) → ETH → distribute a exits ya
   registrados como funded amounts adicionales.

**Efecto empírico esperado**: seed 830 delivered % subiría de 45.2 %
a ~85 % (14 ETH recovered + ~4 ETH del pool distribuidos post-hoc).
Esto matches mejor el baseline Chainalysis 2024 para operaciones
profesionales completadas (80-95 % delivery).

**Trade-off**: el defensor evaluado sobre estos datasets ya no verá
"orphan notes" como signal de amateur — el dataset queda más
homogéneo hacia el patrón "professional attacker + post-hoc
cleanup". Esto es deseable para F1 metrics stratified (§8.5-8.6)
porque prevents el defensor de sobre-fittear a artifacts del LLM
sub-agent's max_iterations hits.

**Esfuerzo estimado**: 4-6 horas código + validación. Bajo riesgo
(las funciones de recovery ya existen como scripts standalone —
sólo hay que integrarlas al pipeline `run_campaign.py`).

### 10.4.21 P1-63 — Streaming API + context compression para prevenir APITimeout crashes

**Motivación**: §8.9.34 documenta que los intentos v1 y v2 de la
corrida Anvil seed 840 (stablecoin-scam 41 672 USDT) fallaron con
`anthropic.APITimeoutError` durante el coordinator loop. El
root-cause fue **context inflation**: el scenario requería ~63
exits sub-$999, forzando muchas delegaciones y accumulando >100k
tokens de contexto para el mensaje 65. Sonnet's response al mensaje
65+ tardó >90 s (nuestro `httpx.Timeout(read=90.0)`), y las 2
retries también fallaron.

**Tres propuestas complementarias:**

**1. Streaming API con reconnect on drop.**

Reemplazar `messages.create()` (blocking) por `messages.stream()` en
`aml.attackers.llm_client`. Streaming API:
- Envía tokens incrementalmente en vez de esperar respuesta completa
- Sobrevive network blips mid-generation (reconnect a partir del
  último token recibido)
- Reduce el timeout aparente a nivel per-token, no per-response

Esfuerzo: 2-3 días para refactorizar `LLMClient.complete()`.

**2. Context compression del coordinator.**

Cuando el coordinator llegue a message >30, comprimir los tool_results
antiguos:
- Los primeros N-20 mensajes se sumarizan por un mini-LLM (Haiku)
- El summary reemplaza los mensajes originales
- El contexto real enviado a Sonnet queda cap en ~30 mensajes vivos

Esfuerzo: 1 semana. Impacto: contexto del coordinator ~lineal en vez
de exponencial.

**3. Amount-adaptive `read_timeout`.**

En `llm_client.py`, calcular `read_timeout` basado en el amount:

```python
def _adaptive_timeout(amount_usd: float) -> float:
    # Base 90s, +15s por cada 10k USD extra
    return 90.0 + 15.0 * (amount_usd - 10000) / 10000
```

Para stablecoin 41 672 USDT: `read_timeout = 90 + 15*3.16 = 137 s`
en vez de 90 s. Fix trivial (5 líneas) que solo aborda el síntoma,
no la causa raíz — pero valdría la pena aplicarlo antes que las
soluciones 1-2 más caras.

**Publicabilidad**: los 3 findings son publishable como paper
metodológico sobre LLM-driven multi-agent pipelines en el AAAI
Workshop on Advances in Large Language Models (AdvLLM) o similar.
El finding «context inflation × read_timeout interaction» está poco
documentado en la literatura (mayoría de LLM pipelines son
single-turn o short-horizon).

### 10.4.22 P1-68 — LLM re-scoring bidireccional para reducir background FPR

**Motivación**: §8.9.37 documenta que el pipeline defensor híbrido
(Phase 1 Louvain + Phase 2 LLM Coordinator) NO reduce el background
FPR (99 % Sepolia, 43 % Anvil) porque el LLM sólo procesa las
addresses ya flagged por Phase 1 — el LLM puede clusterizar mejor,
pero NO puede "unflag" a un false positive.

**Propuesta**: introducir un tercer paso *bidireccional* donde el
Coordinator emite:

1. **New flag list** con addresses del background que NO estaban
   flagged por Phase 1 pero que su reasoning identifica como
   attacker-like.
2. **Un-flag list** con addresses previamente flagged por Phase 1
   cuyo reasoning ("this appears to be a legitimate exchange hot
   wallet based on high in-degree from many small users, no
   outbound to sub-CTR chunks") justifica removerlas.

**Implementación**: modificar `_build_llm_user_prompt` para incluir
además de las 60 flagged per exchange, un sample aleatorio de 20-30
background addresses. El sistema prompt del Coordinator instruye:

> "Emit `actor_clusters` for flagged addresses AND, when evidence is
> strong, `unflag_addresses` for false positives AND
> `newly_flagged_addresses` for background addresses that show
> attacker-consistent behavior."

**Efecto esperado en background FPR**:

- Sepolia bgFPR actual: 99.9 %
- Sepolia bgFPR post-P1-68: **10-25 %** (LLM identifica dev-testnet
  patterns como benignos con reasoning: "deploy tx, faucet claim,
  no mixer interaction → not attacker")
- Trade-off: recall en attacker puede caer 3-5 pp por unflags erróneos

**Riesgo**: LLM re-scoring introduces subjectivity y potencial para
gaming (un ataque adversarial que se disfrace como dev-testnet
pattern). Requiere ablation cuidadosa y comparación con L1-only.

**Esfuerzo estimado**: 1 semana implementación + 3 días validación
sobre los 5 datasets existentes + regeneración de F1/FPR tables.
Publishable como *system paper* en ACM SIGKDD Applied Data Science
Track.

### 10.4.23 P1-70 — Clustering granularity guidance en el prompt del LLM defender

**Motivación**: §8.9.39 (EthereumHeist cross-domain eval) reveló que
el LLM defender **over-segmenta clusters**: 22 predichos vs 3 hacks
reales en el top-60 flagged (ratio 7×). El mismo patrón fue visible
en §8.9.37 sobre nuestros datasets (19-48 clusters predichos).

**Root cause**: el system prompt actual del defensor pide "cluster
addresses into distinct actor groups" sin especificar granularidad
esperada. El LLM interpreta cualquier variación en features (degree,
volumen, ratio in/out) como actor separado, cuando en realidad la
misma operación de laundering puede tener muchas sub-topologías.

**Propuesta P1-70**: extender el system prompt con guidance
explícita sobre coarser clustering:

```
Real Ethereum hacks typically comprise a single coordinated actor
even when their addresses show varied behaviors (mixer users, peel
chains, exit distributors are ALL part of one campaign). Prefer
FEWER, LARGER clusters over many small ones. Only split a cluster
when features differ dramatically (>3σ on at least 2 dimensions).
Aim for 3-10 clusters per 60 flagged addresses, not 20-50.
```

**Ablation propuesto**: re-run los 6 datasets (5 in-domain + 1
EthereumHeist) con el prompt P1-70 vs baseline. Metric target:

- LLM ARI **should double**: 0.03 → ~0.06 in-domain; 0.06 → ~0.15
  EthereumHeist
- LLM cost: unchanged (~$0.04-0.007 per eval)
- F1 binary: unchanged (Phase 1 dominates)

**Esfuerzo estimado**: 30 min prompt engineering + 30 min ablation
run + 30 min analysis. Cost: ~$0.30 total (6 datasets × Haiku).

**Impacto TFM**: convierte el "weak ARI" finding actual en "ARI
significantly improved by prompt engineering" — resultado
publishable como contribución metodológica sobre LLM-driven
graph clustering.

**UPDATE 2026-09-14 — ABLATION EJECUTADA, RESULTADO NEGATIVO**:

El ablation P1-70 se ejecutó (§8.9.40). El LLM **ignoró la guidance**
y produjo MÁS clusters (29 vs 26 baseline) en lugar de MENOS. ARI
empeoró: 0.026 → −0.023 (peor que aleatorio).

Esto es un finding metodológico publishable **negativo**: prompt-only
guidance NO es suficiente para controlar output structure de LLMs.
3/3 ablations similares fallidas en el TFM (P1-55, P1-56, P1-70)
sugieren un patrón: **prompt engineering para influir en cuentas /
categorías cuantitativas es sistemáticamente unreliable en Sonnet /
Haiku actuales**.

P1-70 REVERTED. Ver §10.4.24 (P1-71) para propuesta alternativa
code-level.

### 10.4.24 P1-71 — Enforcement code-level de max cluster count (IMPLEMENTED, POSITIVE)

**Estado (2026-09-14)**: **IMPLEMENTADO Y VALIDADO** en `src/aml/detectors/multi_agent.py`.
Detalle empírico completo en §8.9.42. Resumen:

| Métrica       | Baseline | max_c=3 | max_c=5 |
|---------------|---------:|--------:|--------:|
| Mean ARI      |    0.001 |   0.159 |   0.095 |
| Improvement   |     —    | ~160×   |  ~95×   |

Primer resultado positivo del defensor tras 4 ablations fallidas
(P1-55, P1-56, P1-70, P1-72). Coste: $0.194 total × 5 datasets.

**Motivación**: P1-70 falló (§8.9.40). Dado que prompt guidance no
controla output structure LLM, la alternativa efectiva es
post-processing code-level:

```python
def _cap_clusters(pred: dict[str, int], max_clusters: int) -> dict[str, int]:
    """P1-71: merge similar clusters until count <= max_clusters."""
    if len(set(pred.values())) <= max_clusters:
        return pred
    # 1. Compute per-cluster centroids (mean of member features)
    # 2. Iteratively merge two closest clusters until count == max
    # 3. Reassign addresses to merged cluster IDs
    ...
```

Ventajas vs P1-70:
- Deterministic (no LLM randomness)
- Guaranteed cluster count constraint
- Post-hoc — no re-cost del LLM

Desventaja:
- Feature-distance heuristic puede merge campañas diferentes que
  comparten fingerprint superficial. Requiere ablation cuidadosa.

**Esfuerzo real (ex-post)**: 2 h implementación (algoritmo + integración
en `multi_agent.py`) + 15 min sweep sobre 5 datasets. Coste real: $0.194
LLM (una llamada por dataset — el merge es post-hoc puro). Impacto real:
**~160× mejor que baseline** (Δ absoluto +0.157), superando ampliamente
la estimación conservadora de 2-3×. Ver §8.9.42.

### 10.4.25 P1-72 — Extended graph-native features (attempted, negative)

**Motivación**: complementaria a P1-70. Si el prompt no controla la
granularidad de output del LLM (P1-55/56/70 todos fallidos), quizá
enriquecer el input (fingerprints con features de topología global)
sí lo haga.

**Ejecución (§8.9.41, 2026-09-14)**: extendido fingerprint 19-dim →
22-dim añadiendo `pagerank_x1e6`, `betweenness_x100` (k=200 aprox),
`clustering_x100` computados sobre el grafo combinado completo
(8 554 nodos). Re-run seed 803 con Haiku 4.5.

**Resultado — NEGATIVE**:

| Métrica              | Baseline 19-dim | 22-dim ext. | Δ         |
|----------------------|----------------:|------------:|----------:|
| ARI (actor cluster)  |         0.026   |    0.0154   | −0.011 ❌ |
| Clusters predichos   |            26   |        50   | +24 ❌    |
| LLM cost             |        $0.043   |    $0.049   | +14 %     |

El LLM **sobre-particionó más** con features extendidas (50 vs 26).
Cuarto ablation consecutivo (P1-55, P1-56, P1-70, P1-72) sin mejorar
el ARI del defensor.

**Patrón consolidado**: la granularidad de clustering del LLM parece
insensible tanto a prompt engineering como a feature engineering. La
ruta prometedora restante es P1-71 (enforcement code-level post-hoc),
que sigue pendiente de implementación.

**Estado**: P1-72 REVERTED. `scratchpad/feature_eng_eval.py`
retenido en repo como registro de reproducibilidad del ablation.

### 10.4.26 P1-73 — Silhouette auto-tune de max_clusters

**Estado (2026-09-14)**: **IMPLEMENTADO Y VALIDADO** en §8.9.44.
Resuelve el único caveat metodológico de P1-71: la elección del
hyperparámetro `max_clusters` cuando el true actor count es
desconocido. Aplica silhouette score data-driven sobre los
centroides candidatos → selección automática y label-free.

**Resultado empírico (6 datasets: 5 propios + EthereumHeist)**:

| Estrategia                | Mean ARI | Requiere oracle |
|---------------------------|---------:|:---------------:|
| Baseline LLM              |   0.020  |       ❌         |
| max_clusters=3 (oracle)   |   0.150  |       ✓         |
| **max_c=silhouette (P1-73)** | **0.194** |    ❌       |

Silhouette gana en 4/6 datasets y produce mejor mean ARI que
cualquier constante fija sin requerir ground truth. Implementación
en `_auto_pick_max_clusters()`.

### 10.4.27 P1-74 — Sonnet ablation: contraintuitive positive finding

**Estado**: ejecutado (§8.9.45). Sonnet 4.6 tiene mejor baseline ARI
(0.013 vs Haiku 0.001, ~13×) pero **peor** ARI final con P1-71
(0.078 vs Haiku 0.159, mitad) y cuesta 3× más ($0.13 vs $0.04 por eval).

**Publishable**: "smaller-and-cheaper + right post-processing beats
larger-and-more-expensive" — Haiku 4.5 + P1-71 es la configuración
Pareto-óptima. Deployment cost 3× menor para el mismo ARI.

### 10.4.28 Síntesis final — control de LLM output tras 7 intervenciones

Cerrando el arco de experimentos sobre cómo influir en la
granularidad de clustering del LLM defensor, la evidencia acumulada
en el TFM soporta la siguiente jerarquía de intervenciones:

| Nivel de intervención           | Ejemplo               | Coste   | Efectividad         | Determinismo |
|---------------------------------|-----------------------|--------:|:-------------------:|:------------:|
| Prompt hint (soft)              | P1-55/56/70           | ~$0     | ❌ Nula              | ❌ No        |
| Feature enrichment (input)      | P1-72                 | ~$0.05  | ❌ Nula              | ❌ No        |
| Bigger model (Sonnet vs Haiku)  | P1-74                 | +$0.60  | ❌ Peor final ARI    | ❌ No        |
| Training data injection         | P1-69 (hard-negative) | $0.20   | ✓ F1 +0.010         | Parcial     |
| **Code-level post-hoc merge**   | **P1-71**             | $0.19   | **✓✓ ARI ×160 (5)** | **✓ Total** |
| **Cross-domain validation**     | **P1-71 + EthHeist**  | $0.01   | **✓✓ ARI 0.077→0.41** | **✓**     |
| **Silhouette auto-tune (P1-73)**| **P1-73**             | $0      | **✓✓ Mean ARI 0.19** | **✓ Total** |

**Meta-finding publishable**: para controlar decisiones cuantitativas
del output de un LLM (número de clusters, categorías, budgets),
**modificar el post-proceso ES sistemáticamente más efectivo que
modificar el prompt o los features**. El LLM genera bien la SEÑAL
(quién está cerca de quién en feature-space); lo que falla es su
DECISIÓN OPERATIVA (dónde cortar). Externalizar esa decisión al
código, respetando la señal del LLM, es la palanca correcta.

Esta conclusión generaliza más allá del AML: en cualquier pipeline
LLM-agent + downstream computation donde la LLM produce
categorizaciones sin garantías de cardinalidad, un merge/split
post-hoc determinista basado en distancia entre representaciones
implícitas (centroides, embeddings, log-probs) es cheaper y más
robusto que iterar sobre el prompt.

## 10.5 Reflexión final

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
Capítulo 8 §8.8 lo argumenta detenidamente—. La contribución es
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
