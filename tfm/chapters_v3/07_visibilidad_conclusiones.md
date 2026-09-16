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

El trabajo futuro se organiza en cuatro categorías: (A) extensiones
inmediatas del scope del TFM, (B) arquitectura de despliegue en
producción, (C) mejoras del pipeline atacante identificadas durante
las 26 corridas (referencias P1-XX de commits GitHub), y (D) mejoras
del pipeline defensor. Las items marcadas **[IMPLEMENTED]** ya
existen en el código publicado; los demás son propuestas.

### 10.4.A Extensiones inmediatas del scope

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

### 10.4.B Arquitectura de despliegue en producción

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

### 10.4.C Mejoras identificadas del pipeline atacante

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

### 10.4.D Mejoras del pipeline defensor

- **P1-55/56/70 [ATTEMPTED, NEGATIVE]** Prompt-level guidance para
  guiar la granularidad de output del LLM. Los tres ablations
  fallaron consistentemente (§8.9.F). Publishable como
  meta-finding: prompt engineering no controla output cuantitativo
  discreto del LLM. Ver §10.4.28 síntesis.
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

## 7.C Justificación de competencias del Máster FinTech

Conforme al artículo de la Normativa Propia TFM del Máster Universitario
en Tecnologías del Sector Financiero (FinTech) UC3M (actualizada
2024-2025), se enumera cómo este TFM cubre las competencias básicas
(CB), generales (CG) y específicas (CE) del título.

### 7.C.1 Competencias Básicas

- **CB6 — Conocimientos originales en contexto de investigación**.
  Cubierta por la novedad de visibilidad parcial federada (§4.4), el
  simulador atacante LLM-driven (§5.A/5.B) y las 7 intervenciones
  publishable documentadas en §10.4. El meta-finding
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
  resumen bilingüe (§1), tabla ejecutiva de findings (§7.B) y cost
  summary (§8.9.Z).
- **CB10 — Aprendizaje autodirigido**. Cubierta por el arco completo:
  el autor partió sin experiencia previa en Solidity, ZK proofs o
  LLM agents y desarrolló los tres stacks durante los 5 meses del
  TFM (ver §5.C LOC por lenguaje).

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
