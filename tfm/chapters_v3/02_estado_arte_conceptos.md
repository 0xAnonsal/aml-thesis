# Capítulo 2 — Estado del arte y conceptos previos

Este capítulo cubre (a) los trabajos previos relevantes en detección
AML sobre criptomonedas y agentes LLM, y (b) los conceptos técnicos
necesarios para entender los capítulos posteriores: LLMs, AI aplicada
a crypto laundering, arquitectura de exchanges, y las herramientas de
prueba en Sepolia y Anvil que este trabajo utiliza.


Este capítulo revisa cuatro cuerpos de literatura relevantes al problema
abordado: **(i)** el marco regulatorio AML aplicable a criptoactivos según
FATF y su transposición europea; **(ii)** los detectores de blanqueo
publicados sobre los principales *datasets* académicos de grafos de
transacciones (Elliptic, Elliptic++, EthereumHeist, OpenAML v1); **(iii)**
los sistemas de simulación adversarial en dominios AML previos; y **(iv)** el
uso emergente de modelos de lenguaje grandes (LLM) en tareas de seguridad
on-chain. Al final de cada sección se identifica la brecha específica que el
presente trabajo pretende cerrar. El detalle bibliográfico completo con
correcciones de citas frente a la memoria del proyecto previo se recoge en
`tfm/references_audit.md`, documento de trabajo que se convertirá en
`tfm/references.bib` (formato BibTeX estándar) antes del *submit* final.

## 2.1 Marco regulatorio y tipologías FATF

El Grupo de Acción Financiera Internacional (FATF) es el organismo intergu-
bernamental que fija los estándares globales contra el blanqueo de capitales
y la financiación del terrorismo. Sus 40 Recomendaciones son transpuestas
por las jurisdicciones miembro; en el caso europeo, mediante las Directivas
AMLD5 (2018) y AMLD6 (2018), y más recientemente el Reglamento (UE)
2023/1113 relativo a la información que acompaña a las transferencias de
fondos y determinados criptoactivos (la llamada *travel rule* aplicada a
cripto), y el Reglamento (UE) 2023/1114 (MiCA) sobre mercados de
criptoactivos.

La Recomendación FATF 16 (*travel rule*) es la pieza central para el
problema técnico de este trabajo: obliga a los proveedores de servicios
de activos virtuales (VASPs, incluyendo exchanges) a intercambiar
información del originador y del beneficiario para transferencias por
encima de 1 000 USD/EUR, pero **no** obliga a compartir el contenido íntegro
de sus grafos de transacciones internas. Esa restricción es la que
motiva el escenario de *visibilidad parcial federada* estudiado en los
capítulos siguientes.

La Recomendación FATF 20 (*reporting of suspicious transactions*)
introduce el requisito adicional que orienta la interpretabilidad del
detector: los reportes de operaciones sospechosas remitidos a las
Unidades de Inteligencia Financiera deben incluir la **motivación**
del flag, no sólo el resultado binario del clasificador. Un detector
que produce únicamente un identificador de cluster opaco no cumple con
el espíritu de la Recomendación 20; un coordinador LLM que genera
justificaciones textuales sí. Esta consideración se retoma en la
discusión del Capítulo 8.

En cuanto a tipologías, el informe FATF (2021) *Virtual Assets Red Flag
Indicators of Money Laundering and Terrorist Financing* organiza los
indicadores en seis categorías de red flags (transacciones, patrones de
transacción, anonimato, emisor/receptor, fuente de fondos, riesgos
geográficos) y documenta a lo largo del texto ocho técnicas concretas de
blanqueo específicas de criptoactivos:

1. Uso de mezcladores y CoinJoin.
2. Uso de intercambios descentralizados (DEX) para saltar controles KYC.
3. Uso de puentes cross-chain para fragmentar el rastreo.
4. Structuring (fragmentación por debajo de umbrales de reporte).
5. Uso de *privacy coins* (Monero, Zcash).
6. Movimiento a través de jurisdicciones no cooperativas.
7. Rapid pass-through wallets (direcciones de un solo uso).
8. Cash-out mediante P2P y OTC brokers.

De estas ocho técnicas, este trabajo implementa las cinco verificables
on-chain sin dependencia de información fuera de cadena: mezcladores (1),
DEX (2), puentes cross-chain (3, contract desplegado pero no ejercitado
en las corridas canónicas), structuring sub-CTR (4) y rapid pass-through
wallets (7). Las tres restantes —privacy coins (5), jurisdicciones no
cooperativas (6) y OTC brokers (8)— quedan fuera del scope por
imposibilidad técnica: privacy coins operan en cadenas distintas al
grafo Ethereum modelado, las jurisdicciones requieren datos KYC externos
al on-chain, y los OTC brokers actúan off-chain por definición.

## 2.2 Detectores sobre benchmarks académicos

La literatura de detección AML sobre grafos de transacciones cripto se
articula alrededor de tres benchmarks públicos.

### 2.2.1 Elliptic (Weber et al. 2019) y Elliptic++ (Elmougy y Liu 2023)

El dataset **Elliptic** (Weber et al. 2019) es históricamente el primer
benchmark académico de gran escala: 203 769 transacciones de Bitcoin
etiquetadas como *lícitas*, *ilícitas* o *desconocidas*, con 166 features
agregadas por transacción. Los autores originales reportan F1 = 0,49 con
un clasificador Random Forest, y F1 = 0,42 con un GCN de dos capas. La
métrica es notoriamente frágil: pequeñas variaciones en el particionado
temporal (los datos incluyen la ventana de intervención sobre el mercado
darknet AlphaBay) alteran los resultados en más de 10 puntos absolutos.

**Elliptic++** (Elmougy y Liu 2023, KDD) extiende Elliptic añadiendo el
grafo de wallets (agregación de direcciones bajo una misma entidad
heurística): 822 942 direcciones Bitcoin con 56 features cada una y 1,27 M
interacciones temporales, con etiquetas *lícito/ilícito/desconocido*
propagadas desde el grafo de transacciones original. Los autores reportan
que el detector GNN a nivel de wallet supera al detector sobre
transacciones aisladas, confirmando la ganancia informacional de la
agregación a nivel de entidad. Este trabajo usa Elliptic++ como una de
las tres validaciones externas del clasificador GCN local (Capítulo 4).

### 2.2.2 EthereumHeist (Wu et al. 2023)

El dataset **EthereumHeist** (Wu et al. 2023) es la referencia obligada
en el paso de Bitcoin a Ethereum. Contiene 23 casos reales de hackeos y
robos sobre la red Ethereum entre 2018 y 2023 —incluyendo el ataque de
Lazarus Group a Upbit (2019), el hackeo de PolyNetwork (2021), y el
ataque de AscendEX (2021)— con un total de 633 057 nodos y 2 452 786
aristas. Cada nodo lleva etiqueta binaria (*heist* o *benign*) propagada
desde una dirección semilla identificada por análisis forense.

Los autores reportan F1 = 0,84 con un GCN de dos capas bajo validación
estándar (split aleatorio 80/20). Este trabajo replica esa evaluación y
demuestra que la métrica cae a F1 ≈ 0,68 bajo *leave-one-heist-out*
cross-validation (Capítulo 8), consistente con la hipótesis de
memorización de los detectores GNN sobre datasets AML pequeños.

### 2.2.3 AMLWorld (Altman, Blanuša, Egressy et al. 2023)

**AMLWorld** (Altman, Blanuša, Egressy et al. NeurIPS 2023) es un dataset
sintético de gran escala generado por un simulador multi-agente
desarrollado por IBM Research y ETH Zúrich. A diferencia de los datasets
anteriores, AMLWorld genera etiquetas *ground truth* a nivel de patrón
(*smurfing*, *bipartite*, *cycle*, *stack*, *scatter-gather*) y no sólo
binarias, con información completa sobre las transacciones subyacentes
etiquetadas como blanqueo. La calibración del generador se hace contra
propiedades estadísticas de transacciones bancarias reales, y el paper
demuestra empíricamente su utilidad para benchmarking de detectores
basados en GNN.

La principal limitación de AMLWorld para el problema de este trabajo es
que los patrones se generan mediante reglas geométricas fijas, sin
adaptación adversarial. El generador no incluye llamadas a mezcladores
ZK reales (Tornado Cash), y las decisiones del *launderer* no dependen
del estado observado del detector.

### 2.2.4 OpenAML (proyecto FINOS/DTCC 2025)

**OpenAML v1** es el marco open-source más reciente sobre Ethereum,
mantenido bajo el paraguas de la Fintech Open Source Foundation (FINOS),
una organización sin ánimo de lucro dentro de la Linux Foundation. Su
origen data del *DTCC AI Hackathon* de la Duke University (equipo
CypherSentinel), y actualmente su mantenedor principal es Luciano Juvinski.
El proyecto distribuye un archivo `training_data.csv` con 34 000 wallets
Ethereum etiquetadas y 16 features por wallet (in-degree, out-degree, ETH
recibido, USDT recibido, unique counterparties, entre otras) extraídas de
transacciones reales de mainnet.

A diferencia de los benchmarks anteriores, OpenAML no es un artefacto
académico publicado en actas sino un repositorio en evolución continua;
la documentación menciona un dataset ampliado ("StableAML v2") pero éste
no está distribuido en el repositorio público al momento de la redacción.
Este trabajo utiliza el CSV `training_data.csv` de la versión 1 como una
de las tres validaciones externas del clasificador GCN local (Capítulo 4).

### 2.2.5 Síntesis

Los cuatro benchmarks comparten una limitación estructural: **asumen una
vista global del grafo**. Ni Elliptic++ ni EthereumHeist ni AMLWorld ni
OpenAML consideran la partición del grafo en múltiples exchanges con
visibilidad restringida por diseño regulatorio. En consecuencia, los
detectores reportados no son directamente aplicables al escenario federado
que describe la realidad operativa post-MiCA en Europa.

## 2.3 Simulación adversarial en dominios AML

Los frameworks de simulación AML publicados hasta la fecha se dividen en
dos generaciones.

**Primera generación (basada en reglas)**. **AMLSim** (IBM Research
2020) es el simulador seminal: genera transacciones sintéticas siguiendo
templates de patrones de blanqueo (*fan-in*, *fan-out*, *cycle*,
*bipartite*) parametrizados por distribuciones estadísticas. Es la base
conceptual sobre la que posteriormente **AMLWorld** (Altman, Blanuša,
Egressy et al. 2023, §2.2.3) extiende el enfoque a un régimen
multi-agente de mayor escala. Ambos son deterministas en el sentido
adversarial: el *launderer* no observa al detector ni adapta su
comportamiento en base a él.

**Segunda generación (basada en agentes adaptativos)**. Un cuerpo
emergente de literatura desde 2024 propone simuladores donde el
*launderer* es un agente adaptativo con objetivos, estado interno y
capacidad de decisión que se refina contra el detector. El framework
*hide-and-seek* publicado en *Complex & Intelligent Systems* (2025)
describe una arquitectura de dos poblaciones de agentes —transacciones
lícitas y transacciones ilícitas— coevolucionando contra un detector
supervisado, con evaluación sobre el dataset Elliptic real. Trabajos
recientes como **AMLNet** (arXiv:2509.11595) extienden el enfoque
sustituyendo los agentes reglados por agentes basados en LLM, con
patrones de blanqueo generados por prompting adversarial.

Este trabajo se inscribe explícitamente en la segunda generación pero
con dos diferencias específicas: (i) usa Claude en lugar de GPT como
backend LLM y ejecuta transacciones sobre una blockchain real (Anvil o
Sepolia) en lugar de sobre grafos abstractos; y (ii) introduce simetría
multi-agente por primera vez, con coordinador + sub-agentes
especializados por tipología FATF en el atacante *y* en el defensor, en
lugar de un único agente monolítico por lado.

## 2.4 LLMs en seguridad on-chain y detección de fraude

El uso de LLMs para tareas de seguridad sobre blockchains es un campo
emergente. Se identifican cuatro líneas de trabajo relevantes.

**Análisis de contratos inteligentes**. **GPTScan** (Sun, Wu et al.
ICSE 2024) es el trabajo de referencia en la aplicación de LLMs a la
detección de vulnerabilidades lógicas en Solidity, combinando GPT-4 con
análisis estático de programa para reducir la tasa de falsos positivos
inherente al uso de LLM en solitario. Los autores reportan precisión
> 90 % en contratos token y ~57 % sobre proyectos Web3Bugs de mayor
escala, con recall > 70 % incluyendo 9 vulnerabilidades nuevas no
detectadas previamente por auditores humanos. El enfoque de descomponer
cada tipo de vulnerabilidad en *scenarios* y *properties* que el LLM
verifica selectivamente es directamente análogo al enfoque adoptado en
este trabajo, donde el LLM defensor razona sobre arquetipos AML en lugar
de sobre vulnerabilidades Solidity.

**Razonamiento sobre grafos**. Un cuerpo creciente de literatura desde
2024 explora la capacidad de los LLMs para razonar sobre grafos
representados en texto. **Fatemi, Halcrow y Perozzi** (ICLR 2024, *Talk
Like a Graph*) demuestran empíricamente que la elección del *encoding*
textual del grafo (adjacency list vs. incidence matrix vs. natural
language) altera significativamente la performance del LLM en tareas de
node classification y link prediction, y que los LLMs pueden ser
competitivos con GNNs especializados cuando el grafo cabe en el
contexto. Este resultado es directamente aplicable al coordinador
cross-exchange descrito en el Capítulo 3: el número de direcciones
flageadas por cada exchange (~60) es lo bastante pequeño para que el
LLM pueda razonar sobre la concatenación de las tres vistas locales
dentro de una única llamada.

**Sistemas multi-agente**. **AutoGen** (Wu, Bansal et al. 2023,
arXiv:2308.08155) es la propuesta seminal de Microsoft Research para
orquestar múltiples agentes LLM con roles especializados, permitiendo
combinaciones de LLMs, humanos y herramientas en flujos conversacionales.
**Park et al.** (2023) introducen la idea de agentes generativos con
memoria y planificación en el marco de la simulación de sociedades
sintéticas. Este trabajo se apoya en las ideas arquitectónicas de esos
frameworks pero implementa una capa propia, dada la necesidad de
control estricto sobre los prompts, el catálogo de herramientas
on-chain, y la contabilidad exhaustiva del coste LLM.

**Síntesis**. Ningún trabajo publicado hasta la fecha aplica la
combinación específica *arquitectura multi-agente LLM simétrica* +
*visibilidad parcial federada* + *validación sobre transacciones
Ethereum reales* al problema AML.

## 2.5 Mezcladores basados en pruebas de conocimiento cero

Los mezcladores basados en zk-SNARKs constituyen el elemento técnico
más distintivo del blanqueo moderno sobre Ethereum. **Tornado Cash**
(Pertsev, Semenov y Storm, 2019) es la implementación de referencia:
un contrato acepta depósitos de un valor fijo (*denomination*),
almacena un compromiso Pedersen-MiMC de las credenciales del depositante
en un árbol de Merkle on-chain, y permite retiradas a cualquier
dirección mediante una prueba Groth16 que demuestra la pertenencia del
depósito al árbol sin revelar cuál. El resultado es que el par
(depositante, receptor) es teóricamente indistinguible entre todos los
depositantes previos del mismo *anonymity set*.

Los ataques publicados al anonimato de Tornado Cash explotan patrones
conductuales antes que el criptosistema en sí. **Béres, Seres, Benczúr
y Quintyne-Collins** (IEEE DAPPS 2021, *Blockchain is Watching You:
Profiling and Deanonymizing Ethereum Users*) introducen heurísticas
basadas en reutilización de direcciones, correlaciones temporales
depósito-retirada, y el *Danaan-gift attack* mediante *value
fingerprinting*, y muestran que garantizar anonimato requiere
intervalos aleatorizados que sólo el software de wallet puede imponer.
Más recientemente, el trabajo *Attacking Anonymity Set in Tornado Cash
via Wallet Fingerprints* (ACM SAC 2025) demuestra que las firmas
específicas del software de wallet (patrones de gas, orden de fields
en la transacción) reducen el anonymity set en un 37 %, generando
13 203 vínculos identificables sobre 66 248 transacciones. La
implicación metodológica es clara: un detector AML competitivo debe
apoyarse en features conductuales del grafo, no en criptoanálisis del ZK.

El simulador ofensivo de este trabajo implementa una réplica funcional
de Tornado Cash (`MockTornado.sol`) con circuito Groth16 real,
verificador on-chain, y árbol de Merkle de profundidad 10. Los
depósitos y retiradas del atacante son criptográficamente indistinguibles
de un Tornado Cash real; el detector sólo puede apoyarse en el grafo
observable.

## 2.6 Brecha identificada

De la revisión anterior se extraen cuatro brechas concurrentes que
motivan el enfoque de este TFM:

1. **Visibilidad parcial ausente**. Ningún benchmark académico particiona
   el grafo por exchange con restricciones KYC. La totalidad de los
   detectores publicados asume una vista global.
2. **Simulación adversarial simétrica ausente**. Los simuladores de
   segunda generación introducen un único agente adaptativo (el
   *launderer*); no existe un análogo defensor con capacidad de
   razonamiento en lenguaje natural.
3. **Interpretabilidad no evaluada como criterio**. La literatura reporta
   ARI, F1 y AUC pero no evalúa la capacidad del detector para producir
   justificaciones textuales alineadas con FATF Rec. 20.
4. **Validación externa on-chain infrecuente**. La mayoría de trabajos
   opera exclusivamente sobre datasets *offline*. Las validaciones
   *live* sobre testnets públicas con transacciones verificables por
   terceros son excepción.

El sistema descrito en los capítulos siguientes aborda las cuatro
simultáneamente.
