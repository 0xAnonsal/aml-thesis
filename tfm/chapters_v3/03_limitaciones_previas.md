# Capítulo 3 — Limitaciones del estado del arte y motivación del TFM

Este capítulo articula por qué el estado del arte descrito en el
capítulo 2 —a pesar de sus contribuciones sustanciales— deja abiertas
brechas que este TFM aborda. La argumentación se organiza en cinco
bloques: (1) las limitaciones históricas de los modelos LLM aplicados
a este dominio, (2) los problemas metodológicos de los datasets
académicos, (3) las carencias de los simuladores adversariales
previos, (4) los cambios de contexto tecnológico y regulatorio que
en 2026 hacen viable un trabajo como éste, y (5) la brecha específica
que este TFM cubre.

## 3.1 Limitaciones históricas de los modelos LLM en detección AML

Antes de 2024, las capacidades disponibles en modelos de lenguaje
grandes no permitían el tipo de razonamiento estructurado necesario
para clustering AML sobre grafos on-chain:

- **Modelos menos capaces**: GPT-3.5 (2022-2023) y modelos comparables
  no manejaban de forma consistente prompts con centenares de nodos
  y features numéricas. La tasa de errores de parsing y de
  alucinaciones en salidas estructuradas era prohibitiva para uso
  en pipelines de detección.
- **Coste operativo elevado**: los modelos frontera de 2022-2023
  (GPT-4) tenían un precio de ~30 USD/M tokens de entrada y
  ~60 USD/M de salida. Un pipeline como el aquí propuesto
  (~50k tokens de entrada por evaluación) habría costado ~$1.50 por
  eval, contra los $0.04 actuales con Haiku 4.5 — una reducción
  de **37×** en coste unitario.
- **Ventanas de contexto reducidas**: 8k-32k tokens en la generación
  anterior de modelos impedía procesar los fingerprints de 180+
  direcciones flagged simultáneamente. La ventana de 200k de Claude
  Sonnet/Haiku 4.5-4.6 (2026) es lo que permite el diseño de este
  TFM.
- **Ausencia de tool-use fiable**: los agentes que llamaban a
  herramientas externas (transferir ETH, ejecutar swaps) fallaban
  en cadenas largas por deriva del estado interno del agente. El
  soporte robusto de tool-use en Claude Opus 4.7 / Sonnet 4.6
  (2026) es lo que hace posible que el atacante multi-agente de
  este trabajo ejecute campañas Sepolia completas de 6-8h sin
  intervención humana.

**Consecuencia**: los trabajos que aplicaban LLMs a detección de
fraude on-chain antes de 2024 se limitaban a experimentos de "prompt
único" sobre datasets sintéticos pequeños. Un pipeline production-
grade con atacante y defensor ambos LLM-driven, operando sobre grafos
reales de miles de nodos, no era técnicamente viable hasta 2025-2026.

Este TFM aprovecha esa ventana de capacidad — modelos frontera con
precios ~10-40× más bajos, ventanas de contexto ~10× más grandes, y
tool-use robusto — para explorar arquitecturas que habrían sido
imposibles hace 24 meses.

## 3.2 Limitaciones de los datasets académicos existentes

Los benchmarks públicos revisados en §2.2 (Elliptic++, EthereumHeist,
OpenAML v1, AMLWorld) son valiosos como referencia comparativa pero
adolecen de tres problemas estructurales que sesgan los resultados
reportados en la literatura:

- **Label leakage**: los datasets se construyen etiquetando ex-post
  addresses conocidamente ilícitos (por hackeos publicados, sanciones,
  o clusters heurísticos). Esto genera un sesgo circular: los
  detectores aprenden las heurísticas usadas para etiquetar, no
  necesariamente comportamiento ilícito de novo. El auditor F1
  crítico (§8.10 en este TFM) documenta que EthereumHeist bajo
  LOCO-CV degrada F1 de 0.99 a 0.68 — evidencia de memorización.
- **Escala real vs. laboratorio**: AMLWorld reporta 10⁷ transacciones
  pero es una simulación puramente sintética sin comportamiento
  adversarial. Los datasets con ilícitos reales (Elliptic++,
  EthereumHeist) tienen <10⁵ ilícitos etiquetados — insuficientes
  para técnicas modernas de deep learning sin sobreajuste.
- **Estatismo**: los datasets son snapshots congelados. Un atacante
  moderno adaptaría sus tácticas a los detectores conocidos — este
  bucle adversarial no aparece en la literatura de benchmarks.

**Consecuencia**: los F1 > 0.90 reportados sobre estos datasets no
predicen el rendimiento operativo real. Este TFM aborda el problema
generando datasets propios con un atacante genuinamente adversarial
(sección §3.3) y validando con LOCO-CV (§8.10) para exponer la
memorización.

## 3.3 Limitaciones de los simuladores adversariales previos

Los trabajos que simulan comportamiento adversarial AML sobre
blockchain (Chen et al. 2020, Ferretti et al. 2022, Yang et al. 2024)
comparten tres restricciones:

- **Reglas fijas, no razonamiento**: los "atacantes" son scripts
  determinísticos que ejecutan un pattern preprogramado (structuring
  con umbrales fijos, layering con n saltos fijos). No adaptan
  estrategia según el defensor, no reaccionan a fallos, no eligen
  entre herramientas.
- **Sin ejecución on-chain real**: la mayoría opera sobre grafos
  sintéticos sin desplegar en un testnet. Los costes reales de gas,
  latencias de bloques, fallos de RPC, decisiones de qué exchange
  usar, cuándo cash-out, etc., no aparecen. El resultado es un
  benchmark artificial.
- **Ausencia de mezcladores ZK**: la mayoría no incluye herramientas
  de privacidad como Tornado Cash o pruebas ZK. Sin embargo, en el
  ecosistema real (post-sanción OFAC de Tornado Cash 2022), estos
  son la herramienta primaria de laundering. Un detector entrenado
  en simuladores sin ZK mixer no generaliza.

**Consecuencia**: el atacante multi-agente LLM de este TFM (Cap. 5)
—que ejecuta campañas reales en Sepolia con un mezclador ZK completo,
elige dinámicamente entre 32 herramientas on-chain, y produce
transacciones cuya complejidad emula a las de hackers reales— es
cualitativamente distinto de los simuladores previos y produce
datasets más representativos del régimen operativo real.

## 3.4 Cambios de contexto 2024-2026 que hacen viable este TFM

Tres factores convergentes justifican por qué un trabajo con este
alcance no habría sido posible hace dos años:

- **Modelos frontera accesibles**: Claude Sonnet 4.6 y Haiku 4.5
  (2026) tienen coste de ~$3/$15 (Sonnet) y $0.80/$4 (Haiku) por
  millón de tokens I/O respectivamente. Compara con GPT-4 en 2023:
  ~$30/$60 por millón. Un trabajo con presupuesto estudiantil de
  $50 puede ejecutar 20-40 campañas atacantes completas + validación
  defensor en 2026, mientras que en 2023 el mismo presupuesto sólo
  cubriría 3-5 campañas.
- **Infraestructura testnet madura**: Sepolia (Ethereum testnet
  activa desde 2022) es hoy suficientemente estable y fondeada
  para simulación adversarial realista. Anvil (Foundry, 2023) da
  además un sandbox local instantáneo para iteración rápida. La
  combinación Anvil-dev / Sepolia-prod usada en este TFM (§8.9) es
  posible gracias a esta madurez.
- **Marco regulatorio activo**: MiCA (EU) entró en vigor en 2024 y
  aplica plenamente en 2027; FATF Travel Rule (Recomendación 16)
  está en implementación activa. La necesidad operativa de
  detectores AML sobre criptomonedas con capacidad explicativa
  (justificable ante regulador) — no sólo predictiva — es exactamente
  el nicho que este TFM ocupa.

## 3.5 Brecha específica y aportación del TFM

Uniendo las cuatro brechas anteriores:

| Estado del arte previo (2022-2024)      | Este TFM (2026)                                             |
|-----------------------------------------|-------------------------------------------------------------|
| Atacantes basados en reglas fijas       | Atacante multi-agente LLM con razonamiento dinámico         |
| Defensores clasificación binaria pura   | Defensor con Phase 1 (F1) + Phase 2 (clustering LLM)        |
| Datasets con label leakage sin auditar  | Auditoría LOCO explícita (§8.10) + held-out validation      |
| Sin visibilidad parcial federada        | Federación 3-exchange con partial views (novedad, Cap. 7)   |
| Coste LLM prohibitivo                   | $1.60 total con Haiku 4.5 (§8.9 acumulado)                  |
| Sin outputs interpretables              | Razonamiento textual del defensor (Anexo B, §8.7)           |

Este TFM aporta:

1. **Simulador adversarial LLM-driven** con ejecución on-chain real.
2. **Defensor híbrido** (Louvain + LLM) con outputs interpretables.
3. **Auditoría metodológica honesta** (LOCO + held-out + multi-campaign LOCO).
4. **Novedad de visibilidad parcial federada** (Cap. 7).
5. **Pipeline reproducible** con presupuesto <$2 (código, contratos y
   evaluaciones publicadas bajo MIT en GitHub).

Los capítulos 4-6 describen técnicamente estas aportaciones; el
capítulo 7 cierra con las conclusiones y findings específicos.
