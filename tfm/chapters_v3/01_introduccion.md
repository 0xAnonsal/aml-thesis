# Capítulo 1 — Introducción, contexto, motivación y objetivos

## Resumen (Español)

Este Trabajo Fin de Máster aborda la detección de blanqueo de capitales
sobre criptoactivos en la red Ethereum bajo restricciones realistas de
visibilidad parcial federada por exchange. Se propone un sistema dual
multi-agente: (i) un simulador ofensivo basado en agentes LLM que
ejecuta campañas siguiendo la taxonomía FATF (colocación, layering,
integración) mediante herramientas on-chain reales (transferencias
ERC-20, swaps Uniswap V2, mezclador ZK estilo Tornado con pruebas
Groth16, structuring sub-CTR); y (ii) un detector defensivo multi-agente
donde cada exchange federado observa únicamente su vista local y
colabora vía fingerprints de features sin compartir datos crudos.

El detector combina un clasificador binario Louvain para Phase 1 (F1)
con un coordinador LLM para Phase 2 (actor clustering) que produce
razonamiento textual auditable. Se validó frente a baselines
establecidos (Louvain community detection, GCN estilo Weber) sobre
tres conjuntos de datos: dataset simulado propio con features
específicas de mezclador (26 campañas), benchmark real EthereumHeist
(Wu et al. 2023, 23 hacks de mainnet incluyendo Upbit-Lazarus y
PolyNetwork), y validaciones auxiliares sobre Elliptic++ y OpenAML v1.
Se aplicó leave-one-campaign-out cross-validation para evitar
memorización, held-out validation con seeds nunca vistos, y
multi-campaign LOCO como test escalabilidad.

Resultados principales: F1 mean = 0.97 sobre datos simulados
(in-distribution + held-out), F1 = 0.93 sobre EthereumHeist real; ARI
role-attribution = 0.43 tras la intervención P1-71 (post-hoc cluster
merge). Como validación externa el sistema completo se desplegó sobre
la testnet pública Sepolia con verificabilidad on-chain vía
Etherscan. Coste total del pipeline de validación: ~1.60 USD en
llamadas LLM.

## Abstract (English)

This Master's Thesis addresses money laundering detection on
Ethereum cryptoassets under realistic federated partial-visibility
constraints per exchange. A dual multi-agent system is proposed:
(i) an offensive LLM-agent simulator that executes campaigns
following the FATF typology (placement, layering, integration) via
real on-chain tools (ERC-20 transfers, Uniswap V2 swaps, Tornado-style
ZK mixer with Groth16 proofs, sub-CTR structuring); and (ii) a
defensive multi-agent detector where each federated exchange observes
only its local view and collaborates via feature fingerprints without
sharing raw data.

The detector combines a Louvain binary classifier for Phase 1 (F1)
with an LLM coordinator for Phase 2 (actor clustering) that produces
auditable textual reasoning. It was evaluated against established
baselines (Louvain community detection, Weber-style GCN) on three
datasets: an own simulated dataset with mixer-specific features (26
campaigns), the real EthereumHeist benchmark (Wu et al. 2023, 23
mainnet hacks including Upbit-Lazarus and PolyNetwork), and auxiliary
validations on Elliptic++ and OpenAML v1. Leave-one-campaign-out
cross-validation was applied to prevent memorization, together with
held-out validation on unseen seeds and multi-campaign LOCO as
scalability test.

Main results: mean F1 = 0.97 on simulated data (in-distribution +
held-out), F1 = 0.93 on real EthereumHeist; role-attribution ARI =
0.43 after P1-71 intervention (post-hoc cluster merge). As external
validation, the entire system was deployed on the Sepolia public
testnet with on-chain verifiability via Etherscan. Total pipeline
validation cost: ~1.60 USD in LLM calls.

**Keywords**: AML, cryptocurrency, Ethereum, adversarial simulation,
multi-agent LLM, federated learning, partial visibility, Sepolia,
FATF, Louvain, actor clustering.

## 1.1 Contexto y motivación

El blanqueo de capitales en criptoactivos ha dejado de ser un problema
periférico para convertirse en un vector sistémico del crimen financiero
global. Según el informe *Crypto Crime Trends 2025* de Chainalysis [27],
el volumen de fondos identificados como procedentes de actividad ilícita
superó los 40 000 millones de dólares en 2024, con una concentración
creciente en la red Ethereum: los *stablecoins* USDT y USDC acumularon más
del 84 % del volumen de fraude cripto verificado en el ejercicio 2025.
Este desplazamiento desde Bitcoin hacia Ethereum modifica de raíz el
problema de detección. La unidad de análisis deja de ser una UTXO y pasa a
ser una cuenta con estado; los flujos ilícitos se enmascaran mediante
interacciones con contratos inteligentes de propósito general —intercambios
descentralizados, puentes cross-chain y mezcladores basados en pruebas de
conocimiento cero— y el rastro on-chain se fragmenta entre múltiples
plataformas de intercambio regulado (*exchanges*) que, por diseño
regulatorio, sólo observan una vista parcial del grafo global.

Esta última restricción es la más limitante en la práctica y, sin embargo,
está prácticamente ausente de la literatura académica reciente. Un exchange
sometido a la normativa KYC/AML —FATF Recomendación 16 sobre la *travel
rule* [6], Reglamento (UE) 2023/1113 [8], MiCA (Reglamento (UE)
2023/1114) [7]— sólo puede etiquetar como *entidad conocida* aquellas
direcciones que ha verificado directamente. El resto del grafo, incluyendo
las direcciones de otros exchanges, sólo es observable como *contrapartes
anónimas*. Ningún actor individual dispone entonces de la visión global
que asumen implícitamente los detectores publicados en los principales
*benchmarks* académicos [1][2][3]. En este TFM abordamos esa brecha
proponiendo una arquitectura donde cada exchange entrena su propio
clasificador sobre su vista local y un coordinador cross-exchange, basado
en un modelo de lenguaje grande (LLM), razona sobre los *fingerprints*
agregados para inferir qué direcciones flageadas en distintos exchanges
pertenecen al mismo actor adversarial subyacente.

Paralelamente, el estado del arte en simulación adversarial de blanqueo
tampoco recoge la sofisticación reciente de los actores reales. Los datasets
sintéticos disponibles —AMLSim [4], OpenAML [5]— generan campañas de
*smurfing* clásico o patrones geométricos de *fan-out/fan-in*, pero no
simulan el uso combinado de mezcladores ZK (Tornado Cash) [20], puentes
cross-chain, intercambios descentralizados con *slippage* real y
estructuración por debajo del umbral de reporte CTR. En este TFM
proponemos un simulador ofensivo basado en agentes LLM que ejecuta cinco
de las ocho técnicas de blanqueo cripto documentadas por FATF en el
informe *Virtual Assets Red Flag Indicators* [6] —mezcladores, DEX,
puentes cross-chain, structuring sub-CTR y rapid pass-through wallets,
dejando fuera privacy coins, jurisdicciones no cooperativas y OTC brokers
por no ser verificables on-chain sin datos externos— mediante herramientas
on-chain reales sobre una copia local de la red Ethereum (Foundry/Anvil)
[26] y validamos el sistema completo sobre la *testnet* pública Sepolia
con verificabilidad on-chain vía Etherscan.

## 1.2 Problema

Toda la actividad ERC-20 sobre Ethereum durante una ventana temporal
puede representarse como un grafo dirigido: los nodos son las
direcciones (cuentas de usuario y contratos), y las aristas son las
transferencias individuales, cada una anotada con el valor, el *token*
y el instante en que ocurrió. Dentro de ese grafo, un subconjunto de
direcciones son adversariales y se agrupan en **campañas de blanqueo**
—cada campaña la orquesta un mismo actor sobre varias direcciones
coordinadas—.

Sobre ese mismo grafo actúan además varios *exchanges* regulados
(típicamente tres en este trabajo). Cada uno tiene visibilidad parcial
estricta: sólo observa las transferencias en las que participa alguna
de las direcciones que él mismo ha verificado por KYC. Ninguna de las
plataformas ve el grafo completo, y ninguna comparte sus tablas KYC
con las demás.

Sobre este planteamiento abordamos dos problemas encadenados:

1. **Detección binaria local**. Cada *exchange* entrena su propio
   clasificador con la información que sí puede ver dentro de su
   perímetro. La salida es binaria: para cada dirección observable,
   *sospechosa* o *no sospechosa*.
2. **Atribución cross-exchange**. Tomando como entrada las direcciones
   marcadas como sospechosas por los detectores locales de las tres
   plataformas, un coordinador central agrupa esas direcciones en
   *clusters* que representen a un mismo actor. Este coordinador nunca
   ve las aristas que cruzan de un *exchange* a otro ni el grafo
   completo; sólo dispone de las direcciones flageadas más sus
   *fingerprints* topológicos.

La primera parte es un problema clásico de clasificación sobre grafos.
La segunda es la contribución técnica no trivial: dos direcciones
marcadas por *exchanges* distintos deben acabar en el mismo *cluster*
si pertenecen al mismo actor, aunque ningún detector local haya visto
la arista que las conecta.

## 1.3 Contribuciones

Realizamos cuatro aportaciones principales.

**Arquitectura simétrica LLM-vs-LLM**. Proponemos un sistema donde tanto
el atacante como el defensor están orquestados por agentes LLM. El
atacante utiliza un coordinador Opus 4.7 con sub-agentes especializados
(Placement, Layering, Integration) basados en Sonnet 4.6, cada uno con
acceso a un catálogo de herramientas on-chain reales. El defensor replica
la asimetría: clasificadores GCN locales por exchange (rol *ML filter*)
más un coordinador Sonnet 4.6 cross-exchange (rol *LLM agent*) que razona
sobre los fingerprints agregados para inferir clusters de actores. Hasta
donde tenemos constancia, esta simetría LLM-vs-LLM es novedosa en la
literatura AML cripto.

**Evaluación estricta bajo visibilidad parcial federada**. A diferencia
de los detectores publicados sobre Elliptic++ [2] o AMLWorld [4], que
asumen implícitamente una vista global del grafo, evaluamos cada
componente bajo particiones del grafo en n=3 exchanges, midiendo
separadamente el rendimiento *intra-exchange* (F1 sobre etiquetas binarias
locales) y el rendimiento *cross-exchange* (ARI sobre atribución de actor
clusters).

**Auditoría metodológica de la memorización en detectores AML**.
Identificamos y documentamos un artefacto experimental común en la
literatura: los detectores GCN sobre datasets pequeños (< 30 campañas)
tienden a memorizar en lugar de generalizar, produciendo F1 > 0.95 en
validación estándar pero cayendo a F1 ≈ 0.42 bajo *leave-one-campaign-out*
cross-validation. Aplicamos LOCO-CV a los tres detectores (Louvain, GCN,
MultiAgent-LLM) y reportamos los diferenciales.

**Validación externa on-chain**. Desplegamos el sistema completo sobre
Sepolia con seis contratos verificables (MockUSDT, MockUniswapV2Pool,
MockTornado con verificador Groth16, MockBridge, MiMCSponge, Verifier),
permitiendo que un lector independiente reproduzca las campañas y
verifique los hashes de transacción en Etherscan.

## 1.4 Objetivos y requisitos del proyecto

### 1.4.1 Objetivo general

Diseñar, implementar y evaluar un sistema dual multi-agente basado en
modelos LLM que (i) simule campañas realistas de blanqueo de capitales
sobre Ethereum siguiendo la taxonomía FATF (placement, layering,
integration) y (ii) detecte esas campañas bajo restricciones realistas
de visibilidad parcial federada por exchange, con outputs auditables
alineados a los requisitos regulatorios de reporting (FATF Rec. 20,
MiCA, Reglamento (UE) 2023/1113).

### 1.4.2 Objetivos específicos

1. **O1 — Atacante multi-agente LLM**. Implementar un simulador
   ofensivo capaz de ejecutar 5 de las 8 técnicas FATF de blanqueo
   cripto (mixer ZK, DEX, cross-chain bridge, structuring sub-CTR,
   rapid pass-through) mediante herramientas on-chain reales sobre
   Anvil y Sepolia.
2. **O2 — Defensor multi-agente cross-exchange**. Implementar un
   detector con arquitectura Louvain Phase 1 (binaria) + LLM
   coordinador Phase 2 (clustering) operando bajo visibilidad parcial
   federada 3-exchange sin compartir datos crudos.
3. **O3 — Validación empírica**. Evaluar el sistema sobre 5 datasets
   simulados propios + EthereumHeist (Wu 2023) + auxiliares
   (Elliptic++, OpenAML v1), reportando F1 binary y ARI actor
   clustering.
4. **O4 — Auditoría metodológica**. Aplicar LOCO-CV, held-out
   validation con seeds nunca vistos, y multi-campaign LOCO para
   detectar y cuantificar memorización.
5. **O5 — Reproducibilidad y validación externa on-chain**. Desplegar
   los 6 contratos del sistema en Sepolia con source code verificado
   en Etherscan y publicar el código bajo MIT en GitHub.

### 1.4.3 Requisitos del proyecto (verificables)

**Requisitos funcionales**:

| Id | Requisito | Criterio de verificación |
|----|-----------|--------------------------|
| RF1 | El atacante debe ejecutar campañas siguiendo FATF placement/layering/integration | 26 campañas ejecutadas con las 3 fases identificables en el `chain_trace.jsonl` |
| RF2 | El atacante debe usar mezclador ZK con pruebas Groth16 verificables | Cada `mixer_withdraw` produce un proof verificado on-chain por el contrato Verifier |
| RF3 | El defensor debe operar bajo visibilidad parcial 3-exchange | `partial_visibility_split` con seed determinista, cada exchange sólo ve sus visible_addresses |
| RF4 | El defensor debe producir binary F1 + actor clustering ARI | Métricas reportadas en §8.5 y §8.6 sobre 5 datasets in-dist + 2 held-out |
| RF5 | Los outputs del LLM Phase 2 deben ser texto auditable | `llm_reasoning` string persistido en cada eval JSON |

**Requisitos no funcionales**:

| Id | Requisito | Criterio de verificación |
|----|-----------|--------------------------|
| RNF1 | Reproducibilidad determinista | Todo random_state=42, mismo comando reproduce mismos JSON |
| RNF2 | Cost budget máximo 50 USD | Coste real acumulado 26.6 USD (atacante + defensor) |
| RNF3 | El sistema debe compilar y ejecutarse en un portátil estándar | i7-11800H + 32 GB RAM + WSL Ubuntu 22.04 — ver §8.2 hardware |
| RNF4 | Código publicado bajo licencia open source | MIT en `github.com/0xAnonsal/aml-thesis` |
| RNF5 | Contratos on-chain verificables por auditor externo | 6 contratos Sepolia con source verified en Etherscan |
| RNF6 | Compliance con normativa uso IA generativa UC3M | Declaración en Anexo H |

## 1.5 Alcance y limitaciones

Este trabajo asume explícitamente las siguientes restricciones:

- **Cadena única (Ethereum)**. Las tipologías cross-chain se simulan mediante
  un `MockBridge` local; no se ejecutan puentes reales a Tron, Solana ni
  Bitcoin. Un trabajo futuro extendería el detector a grafos heterogéneos
  multi-cadena.
- **Tokens únicos (USDT/ETH)**. El simulador soporta un único ERC-20
  (`MockUSDT`) y el activo nativo. No se modelan agrupamientos multi-token
  ni farm/yield laundering.
- **Sin memoria histórica del defensor**. Cada corrida se evalúa
  independientemente. Un defensor productivo mantendría estado entre
  bloques y aprendería de flags anteriores; ese componente está fuera del
  alcance del TFM.
- **LLMs propietarios (Anthropic Claude)**. Los agentes están instanciados
  sobre la familia Claude (Opus 4.7, Sonnet 4.6, Haiku 4.5). El código
  abstrae el proveedor mediante `aml.attackers.llm_client.LLMClient`, pero
  la reproducción exacta requiere una API key comercial. Se documentan los
  costes reales de cada experimento.

## 1.6 Estructura del documento

El TFM se organiza en siete capítulos más anexos:

- **Capítulo 2 — Estado del arte y conceptos previos**. Revisión de
  los trabajos previos: (i) tipologías AML según FATF y la evolución
  reciente hacia cripto; (ii) detectores publicados sobre benchmarks
  (Elliptic, EthereumHeist, AMLWorld, OpenAML); (iii) simulación
  adversarial previa; (iv) uso emergente de LLMs en detección
  on-chain; (v) mezcladores basados en pruebas ZK. Incluye también
  los conceptos técnicos previos que el lector necesita (LLMs,
  laundering en criptomonedas, arquitectura de exchanges, Sepolia
  y Anvil).
- **Capítulo 3 — Limitaciones del estado del arte y motivación del
  TFM**. Articula por qué el trabajo previo —a pesar de sus
  contribuciones— deja abiertas cinco brechas concretas: limitaciones
  de los LLMs previos a 2024 (menos capaces, más caros, ventanas de
  contexto reducidas), problemas metodológicos de los datasets
  académicos (label leakage, escala insuficiente, estatismo),
  carencias de los simuladores adversariales, y por qué el contexto
  tecnológico y regulatorio de 2026 hace viable este TFM.
- **Capítulo 4 — Análisis del problema y tecnologías**. Justificación
  técnica de cada capa del stack (Solidity, Foundry/Anvil, PyTorch
  Geometric, snarkjs, Anthropic SDK, CoinGecko) y detalle de los
  modelos LLM empleados (Opus 4.7 atacante, Sonnet 4.6 defensor
  headline, Haiku 4.5 defensor bulk).
- **Capítulo 5 — Diseño, dataset y lenguajes**. Reúne el diseño
  arquitectónico del sistema (vista de cinco capas, arquitecturas
  atacante/defensor, federación bajo visibilidad parcial), los seis
  lenguajes de programación empleados con LOC por lenguaje, y la
  especificación de datasets (propios + Elliptic++/OpenAML/EthereumHeist),
  particionado federado, configuración de detectores y parámetros LLM.
- **Capítulo 6 — Experimentos y resultados (attack + defense)**.
  Presenta primero la parte offensive (campañas del atacante
  multi-agente, incluyendo validación real sobre Sepolia con
  contratos desplegados y verificados), luego la parte defensive
  (detección binaria, atribución cross-exchange, ablations
  metodológicas: hard-negative, cross-domain EthereumHeist,
  post-hoc cluster merge P1-71, silhouette auto-tune P1-73, Sonnet
  vs Haiku, held-out con seeds nunca vistos, multi-campaign LOCO,
  campaign-id vs role-attribution, feature ablation). Cierra con
  la auditoría metodológica de memorización y las limitaciones.
- **Capítulo 7 — Visibilidad parcial, conclusiones y findings**.
  Argumenta la novedad central del TFM (federación por exchange con
  visibilidad parcial, patrón que ningún trabajo previo ha
  explorado), presenta once decisiones clave de diseño, y cierra
  con las conclusiones, findings publishable específicos —Sonnet
  puede "olvidar" retirar del mixer, problemas Sepolia de
  disponibilidad de fondos, targets de eficiencia coste— y
  reflexión final.
- **Anexos**. Prompts íntegros del atacante y del coordinador
  defensor; catálogo íntegro de herramientas on-chain; contratos
  desplegados en Sepolia; comandos exactos de reproducción;
  estructura del repositorio; atribuciones; declaración UC3M sobre
  uso de IA generativa. Código y datos disponibles en
  [`github.com/0xAnonsal/aml-thesis`](https://github.com/0xAnonsal/aml-thesis).

**Nota sobre numeración de secciones**: los identificadores internos
`§X.Y.Z` de secciones y sub-secciones (por ejemplo §8.9.42) se
preservan como identificadores estables de cross-reference. El primer
dígito reflejaba el número de capítulo en la organización previa
del documento; se mantiene por trazabilidad con los commits de
GitHub y los artefactos publicados. Los capítulos 5-7 del actual
esquema recogen contenido que originalmente vivía en capítulos
numerados 4-10.
