# Capítulo 1 — Introducción, contexto, motivación y objetivos

## 1.1 Contexto y motivación

El blanqueo de capitales en criptoactivos ha dejado de ser un problema periférico
para convertirse en un vector sistémico del crimen financiero global. Según el
informe *Crypto Crime Trends 2025* de Chainalysis, el volumen de fondos
identificados como procedentes de actividad ilícita superó los 40 000 millones
de dólares en 2024, con una concentración creciente en la red Ethereum: los
*stablecoins* USDT y USDC —ambos contratos ERC-20 sobre Ethereum— acumularon
más del 84 % del volumen de fraude cripto verificado en el ejercicio 2025.
Este desplazamiento desde Bitcoin hacia Ethereum modifica de raíz el problema
de detección: la unidad de análisis deja de ser una UTXO y pasa a ser una
cuenta con estado; los flujos ilícitos se enmascaran mediante interacciones
con contratos inteligentes de propósito general (intercambios descentralizados,
puentes cross-chain, mezcladores basados en pruebas de conocimiento cero); y
el rastro on-chain se fragmenta entre múltiples plataformas de intercambio
regulado (*exchanges*) que, por diseño regulatorio, sólo observan una vista
parcial del grafo global.

Esta última restricción es la más limitante en la práctica y, sin embargo,
está prácticamente ausente de la literatura académica reciente. Un exchange
sometido a la normativa KYC/AML —FATF Recommendation 16 sobre la *travel rule*,
Reglamento (UE) 2023/1113, MiCA (Reglamento (UE) 2023/1114)— sólo puede
etiquetar como *entidad conocida* aquellas direcciones que ha verificado
directamente. El resto del grafo, incluyendo las direcciones de otros
exchanges, sólo es observable como *contrapartes anónimas*. En consecuencia,
ningún actor individual dispone de la visión global que asumen implícitamente
los detectores publicados en los principales *benchmarks* académicos
(Elliptic, EthereumHeist, OpenAML). Este trabajo aborda esa brecha
proponiendo una arquitectura donde cada exchange entrena su propio clasificador
sobre su vista local, y un coordinador cross-exchange —basado en un modelo de
lenguaje grande (LLM)— razona sobre los *fingerprints* agregados para inferir
qué direcciones flageadas en distintos exchanges pertenecen al mismo actor
adversarial subyacente.

Paralelamente, el estado del arte en simulación adversarial de blanqueo
tampoco recoge la sofisticación reciente de los actores reales. Los datasets
sintéticos disponibles —AMLSim (IBM 2020), AMLWorld (Altman, Blanuša,
Egressy et al. NeurIPS 2023), OpenAML (FINOS 2025)— generan campañas
de *smurfing* clásico o
patrones geométricos de *fan-out/fan-in*, pero no simulan el uso combinado
de mezcladores ZK (Tornado Cash), puentes cross-chain, intercambios
descentralizados con *slippage* real, y estructuración por debajo del umbral
de reporte CTR. Este trabajo propone un simulador ofensivo basado en agentes
LLM que ejecuta cinco de las ocho técnicas de blanqueo cripto documentadas
por FATF en el informe "Virtual Assets Red Flag Indicators" (2021) —
concretamente mezcladores, DEX, puentes cross-chain, structuring sub-CTR y
rapid pass-through wallets, dejando fuera privacy coins, jurisdicciones no
cooperativas y OTC brokers por no ser verificables on-chain sin datos
externos — mediante herramientas on-chain reales sobre una copia local de
la red Ethereum (Foundry/Anvil), y valida el sistema completo sobre la
*testnet* pública Sepolia con verificabilidad on-chain vía Etherscan.

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

Sobre este planteamiento el trabajo aborda dos problemas encadenados:

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

Este trabajo realiza cuatro aportaciones principales:

1. **Arquitectura simétrica LLM-vs-LLM**. Se propone un sistema donde tanto
   el atacante como el defensor están orquestados por agentes LLM. El
   atacante utiliza un coordinador Opus 4.7 con sub-agentes especializados
   (Placement, Layering, Integration) basados en Sonnet 4.6, cada uno con
   acceso a un catálogo de herramientas on-chain reales. El defensor replica
   la asimetría: clasificadores GCN locales por exchange (rol *ML filter*)
   más un coordinador Sonnet 4.6 cross-exchange (rol *LLM agent*) que
   razona sobre los fingerprints agregados para inferir clusters de actores.
   Hasta donde el autor conoce, esta simetría LLM-vs-LLM es novedosa en la
   literatura AML cripto.

2. **Evaluación estricta bajo visibilidad parcial federada**. A diferencia de
   los detectores publicados sobre Elliptic++ (Elmougy y Liu, KDD 2023)
   o AMLWorld (Altman, Blanuša, Egressy et al. NeurIPS 2023) —que asumen
   implícitamente una vista global del
   grafo— este trabajo evalúa cada componente bajo particiones aleatorias
   del grafo en n=3 exchanges, midiendo separadamente el rendimiento
   *intra-exchange* (F1 sobre etiquetas binarias locales) y el rendimiento
   *cross-exchange* (ARI sobre atribución de actor clusters).

3. **Auditoría metodológica de la memorización en detectores AML**. Se
   identifica y documenta un artefacto experimental común en la literatura:
   los detectores GCN sobre datasets pequeños (< 30 campañas) tienden a
   memorizar en lugar de generalizar, produciendo F1 > 0,95 en validación
   estándar pero cayendo a F1 ≈ 0,42 bajo *leave-one-campaign-out*
   cross-validation. Este trabajo aplica LOCO-CV a los tres detectores
   (Louvain, GCN, MultiAgent-LLM) y reporta los diferenciales.

4. **Validación externa on-chain**. El sistema completo se despliega sobre
   Sepolia con seis contratos verificables (MockUSDT, MockUniswapV2Pool,
   MockTornado con verificador Groth16, MockBridge, MiMCSponge, Verifier),
   permitiendo que un lector independiente reproduzca las campañas y verifique
   los hashes de transacción en Etherscan.

## 1.4 Alcance y limitaciones

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

## 1.5 Estructura del documento

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
