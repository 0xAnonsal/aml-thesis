# Capítulo 5 — Diseño, dataset y lenguajes

Este capítulo reúne el diseño arquitectónico del sistema, los
lenguajes empleados en la implementación, y la especificación de
datasets y parámetros. Se divide en cuatro bloques principales:

- **§5.A Diseño y diagramas** — vista general del sistema, arquitecturas atacante y defensor, flujo end-to-end.
- **§5.B Arquitectura de software** — capas de contratos, blockchain, herramientas y coordinación multi-agente.
- **§5.C Lenguajes empleados** — Python, Solidity, Circom, JavaScript, Bash, Markdown con LOC por lenguaje.
- **§5.D Datasets y parámetros** — datasets propios y externos, particionado federado, configuración de detectores y LLMs.

---

## 5.0 Novedad central del TFM — visibilidad parcial federada por exchange

Antes de exponer el diseño técnico se articula, en una única sección
compacta, la novedad que este trabajo aporta al estado del arte
descrito en el Capítulo 2 y a las limitaciones documentadas en el
Capítulo 3.

### 5.0.1 El problema que nadie ha modelado

Toda la literatura académica AML sobre criptoactivos (Weber 2019,
Wu 2023, Elmougy 2023, Juvinski 2025) asume implícitamente **una
vista global del grafo** — un observador omnisciente que ve todas
las transacciones on-chain y todos sus emisores/receptores. En la
práctica regulatoria esto no ocurre: bajo FATF Rec. 16 y Reglamento
(UE) 2023/1113, **cada exchange sólo puede identificar KYC a sus
propios usuarios**. Las direcciones de otros exchanges y de wallets
no-KYC son observadas como *contrapartes anónimas*. En consecuencia,
ningún actor individual dispone de la visión global sobre la que
operan los detectores publicados.

**Esta brecha entre el modelo académico (grafo completo) y la
realidad regulatoria (vistas parciales federadas) es la novedad
central de este TFM.**

### 5.0.2 Propuesta: federación cross-exchange con fingerprints

La arquitectura propuesta modela explícitamente n exchanges
federados donde:

1. Cada exchange E_i observa **únicamente su subgrafo local**
   `G_i = (V_i, E_i)` obtenido por *hashing determinista* de las
   direcciones sobre {1..n} (§5.A.4).
2. Cada exchange entrena su propio clasificador binario Louvain
   sobre G_i, produciendo flags locales `S_i ⊂ V_i`.
3. Los exchanges comparten **NO datos crudos** sino **fingerprints
   agregados** de 19 dimensiones por dirección flageada. Estos
   fingerprints son propiedades topológicas (`in_degree`, `log_usdt_in`,
   etc.) que **no revelan** el patrón de contrapartes específico ni
   permiten re-identificar la actividad de un usuario particular fuera
   del subgrafo local. Cumple los requisitos de minimización de datos
   del art. 25 RGPD.
4. Un coordinador cross-exchange —basado en LLM— razona sobre los
   fingerprints agregados de todos los exchanges y propone
   agrupaciones actor-cluster: qué direcciones flageadas en distintos
   exchanges probablemente pertenecen al mismo actor adversarial
   subyacente.

### 5.0.3 Por qué esto es publishable

Ningún trabajo previo del estado del arte cumple simultáneamente los
tres siguientes criterios:

| Criterio | Weber 2019 | Wu 2023 | Elmougy 2023 | Juvinski 2025 | **Este TFM** |
|----------|:---------:|:-------:|:------------:|:-------------:|:------------:|
| Modelado explícito de visibilidad parcial multi-exchange | ❌ | ❌ | ❌ | ❌ | **✓** |
| Coordinador cross-exchange que razona sin acceso a datos crudos | ❌ | ❌ | ❌ | ❌ | **✓** |
| Validación empírica bajo particionado federado n=3 | ❌ | ❌ | ❌ | ❌ | **✓** |

Los resultados empíricos (§8.9.K multi-campaign LOCO F1 = 0.939 con
34 578 nodos y 7 campañas simultáneas + §8.9.49 role attribution
ARI = 0.43 sobre 18 clusters role×campaign) demuestran que la
arquitectura propuesta **funciona operativamente** bajo las
condiciones adversariales del despliegue real: multiples campañas
concurrentes, visibilidad parcial cero cross-exchange, sin re-training
por dataset.

### 5.0.4 Diagrama de la arquitectura

![Figura 1. Arquitectura del sistema dual multi-agente: atacante LLM (Opus 4.7) ejecuta transacciones on-chain; los 3 exchanges federados observan sólo sus vistas locales KYC-verificadas; el coordinador LLM defensor (Haiku 4.5) razona sobre fingerprints agregados sin acceso a datos crudos.](tfm/figures/architecture.png)

### 5.0.5 Implicaciones regulatorias

La arquitectura propuesta es directamente aplicable al despliegue
comercial post-MiCA (Reglamento (UE) 2023/1114, vigencia plena 2027):

- **Rec. 16 FATF** (travel rule): cada exchange comparte fingerprints
  agregados, no PII, cumpliendo la restricción de compartición.
- **Rec. 20 FATF** (transparencia SAR): el LLM coordinator produce
  razonamiento textual auditable por rol AML (§8.7).
- **MiCA art. 63** (transparencia algorítmica): los outputs LLM son
  interpretables por un compliance officer sin conocimiento de ML.

## 5.A Diseño y diagramas del sistema

Este capítulo describe la arquitectura del sistema completo mediante siete
diagramas: (1) visión general de las cinco capas del pipeline; (2) desglose
interno del atacante multi-agente; (3) desglose interno del defensor
multi-agente; (4) flujo end-to-end de una campaña; (5) modelo de datos
federado; (6) stack de la prueba zero-knowledge del mezclador; y (7) vista
de despliegue en producción (trabajo futuro).

Cada diagrama se presenta como un esquema estructurado en texto (legible
directamente en Word) más una descripción explicativa. Las versiones
renderizadas como imagen (Mermaid) están disponibles en el repositorio
GitHub del proyecto para consulta online.

---

## 4.1 Vista general: las cinco capas del sistema

### Esquema

```
┌─────────────────────────────────────────────────────────────────┐
│  CAPA 5 — DEFENSOR MULTI-AGENTE                                 │
│    • GCN local por exchange (×3)                                │
│    • LLM Coordinator (Haiku / Sonnet / Opus) o cosine baseline  │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ (analiza tráfico generado)
                              │
┌─────────────────────────────────────────────────────────────────┐
│  CAPA 4 — ATACANTE MULTI-AGENTE                                 │
│    • Coordinator Opus 4.7 (estratega)                           │
│    • Placement / Layering / Integration Sonnet 4.6              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (invoca tools)
┌─────────────────────────────────────────────────────────────────┐
│  CAPA 3 — CATÁLOGO DE 19 HERRAMIENTAS ON-CHAIN                  │
│    • ToolDispatcher con 5 invariants estructurales              │
│    • Funder pool + gestión de notes                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (envía tx firmadas)
┌─────────────────────────────────────────────────────────────────┐
│  CAPA 2 — BLOCKCHAIN BACKEND                                    │
│    • Anvil ephemeral local (desarrollo + eval Anvil)            │
│    • Sepolia testnet pública (validación externa)               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (opera sobre)
┌─────────────────────────────────────────────────────────────────┐
│  CAPA 1 — CONTRATOS SOLIDITY (EVM)                              │
│    • MockUSDT (ERC-20 6 decimales)                              │
│    • MockUniswapV2Pool (AMM constant-product)                   │
│    • MockTornado + MerkleTreeWithHistory + Verifier (mixer ZK)  │
│    • MockBridge (bridge cross-chain simplificado)               │
└─────────────────────────────────────────────────────────────────┘
```

### Descripción

La Capa 1 (contratos Solidity) es el sustrato sobre el que operan las
capas superiores. La Capa 2 (backend blockchain) elige entre Anvil
ephemeral para desarrollo y Sepolia real para validación externa. La
Capa 3 (herramientas on-chain) expone al atacante 19 tools con 5
invariants aplicados transversalmente por el ToolDispatcher. Las Capas 4
y 5 son las contribuciones novel: atacante y defensor multi-agente
simétricos, ambos basados en LLMs pero con roles opuestos.

---

## 4.2 Atacante multi-agente FATF

### Esquema

```
                            ┌────────────────────────────┐
   USER prompt ────────▶    │  NIVEL 1: COORDINATOR      │
   (scenario 10 ETH)        │  Claude Opus 4.7           │
                            │  Planning + Delegation      │
                            └────────────┬───────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              │                          │                          │
      delegate_to_                delegate_to_             delegate_to_
        placement                   layering                integration
              │                          │                          │
              ▼                          ▼                          ▼
    ┌───────────────────┐   ┌───────────────────┐    ┌───────────────────┐
    │ NIVEL 2:          │   │ NIVEL 2:          │    │ NIVEL 2:          │
    │ PLACEMENT         │   │ LAYERING          │    │ INTEGRATION       │
    │ Sonnet 4.6        │   │ Sonnet 4.6        │    │ Sonnet 4.6        │
    │                   │   │                   │    │                   │
    │ generate_burner + │   │ mixer_deposit +   │    │ register_clean_   │
    │ transfer_eth      │   │ mixer_withdraw +  │    │ exit + smurf_     │
    │                   │   │ swaps + peel      │    │ split             │
    └─────────┬─────────┘   └─────────┬─────────┘    └─────────┬─────────┘
              │                       │                        │
              └───────────────────────┼────────────────────────┘
                                      │
                                      ▼
                     ┌──────────────────────────────────┐
                     │ NIVEL 3: EJECUCIÓN               │
                     │                                  │
                     │ ToolDispatcher                    │
                     │   • 19 tools                     │
                     │   • 5 invariants estructurales    │
                     │   • notes_file persistence       │
                     │   • funder pool rotation         │
                     └──────────────┬───────────────────┘
                                    │
                                    ▼
                     ┌──────────────────────────────────┐
                     │ Anvil / Sepolia                  │
                     │ (transacciones on-chain reales)   │
                     └──────────────────────────────────┘

  Retorno de status (success / partial / incomplete) desde cada sub-agente
  al Coordinador tras completar su fase.
```

### Descripción

El Coordinador Opus no ejecuta transacciones; sólo delega en sub-agentes
especializados por fase FATF. Cada sub-agente recibe un `objective`
textual y un `context`, y devuelve al Coordinador un `status`. El
ToolDispatcher (Nivel 3) es la única capa que firma y envía
transacciones on-chain, aplicando los cinco invariants estructurales
(guardarraíl del deployer, cap dinámico de burners, rotación del funder
pool, gas reserve por defecto, sweep final) de forma transversal a
todas las herramientas.

---

## 4.3 Defensor multi-agente cross-exchange

### Esquema

```
                  ┌──────────────────────────────────────────┐
    INPUT ───▶    │ Grafo global (MultiDiGraph etiquetado)   │
                  │ chain_trace.jsonl + addresses.json       │
                  └──────────────────┬───────────────────────┘
                                     │
                                     ▼
                  ┌──────────────────────────────────────────┐
                  │ partial_visibility_split (hash-based)     │
                  │ 3 exchanges deterministas, seed=42        │
                  └──────┬────────┬────────┬─────────────────┘
                         │        │        │
                         ▼        ▼        ▼
                    ┌──────┐ ┌──────┐ ┌──────┐
                    │ G_A  │ │ G_B  │ │ G_C  │  Subgrafos por exchange
                    │ ~55% │ │ ~55% │ │ ~55% │  (overlap de contrapartes)
                    │ edges│ │ edges│ │ edges│
                    └──┬───┘ └──┬───┘ └──┬───┘
                       │        │        │
                       ▼        ▼        ▼
                    ┌──────┐ ┌──────┐ ┌──────┐
   CAPA 1           │ GCN  │ │ GCN  │ │ GCN  │  Clasificadores GCN
   locales          │  A   │ │  B   │ │  C   │  entrenados independientemente
                    │      │ │      │ │      │  (2 capas 128h + 19 features)
                    └──┬───┘ └──┬───┘ └──┬───┘
                       │        │        │
                       │  top-K=60 flagged + features per address
                       │        │        │
                       └────────┼────────┘
                                │
                                ▼
                  ┌──────────────────────────────────────────┐
   CAPA 2         │ Coordinador cross-exchange (elegir uno): │
                  │                                          │
                  │  • MultiAgentDetector (cosine baseline)  │
                  │  • LLMDefenderCoordinator (Haiku /       │
                  │      Sonnet / Opus)                      │
                  └──────────────────┬───────────────────────┘
                                     │
                                     ▼
                  ┌──────────────────────────────────────────┐
   OUTPUT         │ • Actor clusters (arquetipos AML         │
                  │   nombrados)                             │
                  │ • Binary flags per address               │
                  │ • (LLM) Reasoning textual justificatorio │
                  └──────────────────────────────────────────┘
```

### Descripción

La simetría con el atacante es intencional: dos capas jerárquicas donde
la primera es local por exchange y la segunda agrega cross-exchange. La
Capa 1 (GCN locales) entrena un modelo por exchange sin compartir
grafos crudos entre exchanges — cada exchange sólo ve las direcciones
KYC-propias más sus contrapartes anónimas. La Capa 2 recibe las top-60
direcciones flageadas por cada exchange más las 19 features de cada una,
y produce el clustering final más las alertas. Ambos coordinadores
(cosine baseline y LLM) son sustituibles mediante la misma interfaz.

---

## 4.4 Flujo end-to-end de una campaña (secuencia temporal)

### Esquema

```
  Usuario/CLI      Runner        Coordinator     Sub-agente     ToolDispatcher      Chain
      │              │                │              │                │              │
      │ python run   │                │              │                │              │
      ├─────────────▶│                │              │                │              │
      │              │                │              │                │              │
      │              │ arranca Anvil / conecta Sepolia RPC             │              │
      │              │─────────────────────────────────────────────────┼─────────────▶│
      │              │                │              │                │              │
      │              │ despliega 6 contratos + bootstrap pool          │              │
      │              │─────────────────────────────────────────────────┼─────────────▶│
      │              │                │              │                │              │
      │              │ funds alice = amount ETH      │                │              │
      │              │─────────────────────────────────────────────────┼─────────────▶│
      │              │                │              │                │              │
      │              │ instancia + user_prompt        │                │              │
      │              ├───────────────▶│              │                │              │
      │              │                │              │                │              │
      │              │           ┌────┤ LOOP hasta end_turn:            │              │
      │              │           │    │              │                │              │
      │              │           │    │ plan next FATF phase           │              │
      │              │           │    │              │                │              │
      │              │           │    │ delegate_to_layering(...)     │              │
      │              │           │    ├─────────────▶│                │              │
      │              │           │    │              │                │              │
      │              │           │    │        ┌─────┤ LOOP hasta phase done:         │
      │              │           │    │        │     │                │              │
      │              │           │    │        │     │ tool_call (ej. mixer_deposit) │
      │              │           │    │        │     ├───────────────▶│              │
      │              │           │    │        │     │                │ sign + send  │
      │              │           │    │        │     │                ├─────────────▶│
      │              │           │    │        │     │                │ receipt+event│
      │              │           │    │        │     │                │◀─────────────┤
      │              │           │    │        │     │ tool_result    │              │
      │              │           │    │        │     │◀───────────────┤              │
      │              │           │    │        └─────│                │              │
      │              │           │    │              │                │              │
      │              │           │    │ status success/partial/failed │              │
      │              │           │    │◀─────────────┤                │              │
      │              │           │    │              │                │              │
      │              │           └────┤ evaluate + decide next        │              │
      │              │                │              │                │              │
      │              │ sweep_funder_pool(destination)                 │              │
      │              ├────────────────────────────────────────────────▶│              │
      │              │                │              │                │              │
      │              │ extract_chain_trace (start_block, end_block)    │              │
      │              │─────────────────────────────────────────────────┼─────────────▶│
      │              │                │              │                │              │
      │              │ persistir campaign.json / chain_trace.jsonl / addresses.json  │
      │              │                │              │                │              │
      │◀─────────────┤ recovery %, cost, wall clock                    │              │
```

### Descripción

El bucle multi-turn LLM ↔ tools ocurre a nivel del sub-agente. El
Coordinador sólo interviene entre fases FATF (Placement → Layering →
Integration). Cada tool call viaja del sub-agente al dispatcher y de
allí a la blockchain; el resultado (receipt + eventos) vuelve por el
mismo camino. Al terminar la fase, el sub-agente reporta un status
resumido al Coordinador. Al final de la campaña, el runner extrae el
trace completo del window de bloques (incluyendo tráfico coetáneo real
si es Sepolia) y persiste todos los artefactos en disco.

---

## 4.5 Modelo de datos: federación bajo visibilidad parcial

### Esquema

```
   Grafo global G (NO observable en producción real):
   ┌─────────────────────────────────────────────────┐
   │  10 311 nodos                                   │
   │  101 880 aristas                                │
   │  Etiquetas KYC completas (sólo en simulación)   │
   └─────────────────────────┬───────────────────────┘
                             │
                             ▼
              hash(dirección) mod 3, seed=42
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
   ┌─────────┐         ┌─────────┐         ┌─────────┐
   │  X_A    │         │  X_B    │         │  X_C    │
   │Exchange │         │Exchange │         │Exchange │
   │    A    │         │    B    │         │    C    │
   └────┬────┘         └────┬────┘         └────┬────┘
        │                   │                   │
        │  Cada exchange ve:                    │
        │    • Direcciones propias (~33%)       │
        │    • Sus etiquetas KYC (label conocido)
        │    • Contrapartes de B y C (visibles pero label=unknown)
        │
        └────────┬──────────┴────────┬──────────┘
                 │                   │
                 ▼                   ▼
   ┌───────────────────────────────────────────────┐
   │ Coordinador cross-exchange (Capa 2)           │
   │                                               │
   │ Recibe únicamente:                            │
   │   • Fingerprints per-address (features)       │
   │   • Scores de probabilidad                    │
   │                                               │
   │ NO recibe grafos crudos de A, B, C            │
   └───────────────────────────────────────────────┘
```

### Descripción

El particionado hash-based garantiza que cada dirección pertenece a
exactamente un exchange (determinismo entre corridas por la semilla
42). Las contrapartes que interactúan con nuestro exchange aparecen en
el subgrafo pero sin etiqueta accesible — así se replica la asimetría
regulatoria post-MiCA. La agregación cross-exchange respeta la
restricción de no compartir grafos crudos: el coordinador sólo recibe
fingerprints (vectores de 19 features + scores) de las top-60
direcciones flageadas por cada exchange.

---

## 4.6 Stack de la prueba ZK del mezclador

### Esquema

```
  OFF-CHAIN (Python + Node.js)                    ON-CHAIN (Solidity EVM)
  ═══════════════════════════════════════         ══════════════════════════

  1. mixer_deposit(from_wallet)
     ├─ nullifier = secrets.token_bytes(31)
     ├─ secret    = secrets.token_bytes(31)
     ├─ commitment = MiMC(nullifier, secret)
     │
     ├─▶ persistir deposit_note                   ┌────────────────────┐
     │   en mixer_notes.jsonl (safety net)        │ MockTornado.deposit│
     │                                            │ (commitment) →     │
     └─▶ tx.deposit(commitment)  ─────────────────│ emit Deposit event │
                                                  │ commitment insertado
                                                  │ como leaf en tree  │
                                                  └────────────────────┘

  2. mixer_withdraw(deposit_note, recipient)
     │
     ├─ escanear eventos Deposit del contrato
     │  (paginación get_logs con retry backoff)
     │
     ├─ reconstruir Merkle tree local (200 leaves)
     │
     ├─ calcular root local
     │
     ├─ verificar isKnownRoot(root) on-chain
     │  (fallback: retry hasta 3 veces si desync)
     │
     ├─ snarkjs.groth16.prove:
     │    inputs = nullifier + secret + path Merkle
     │    output = proof.json (3 puntos BN128) + signals
     │  (~5 segundos por prueba)
     │                                            ┌────────────────────┐
     └─▶ tx.withdraw(proof, root, nullifierHash, │ MockTornado.       │
                     recipient, fee=0, refund=0) │   withdraw(...)    │
                     ──────────────────────────▶ ├────────────────────┤
                                                 │ ┌──────────────────┤
                                                 │ │ Verifier.sol     │
                                                 │ │ verifyProof(     │
                                                 │ │   proof, signals)│
                                                 │ │ ↓                │
                                                 │ │ Pairing BN128    │
                                                 │ │ e(A,B)·e(-α,β)   │
                                                 │ │  ·e(-vk_x,γ)     │
                                                 │ │  ·e(-C,δ) = 1 ?  │
                                                 │ └──────────────────┤
                                                 │ isKnownRoot(root)?  │
                                                 │ isSpent(hash)? no   │
                                                 │ transfer 1 ETH →    │
                                                 │   recipient         │
                                                 │ mark nullifier spent│
                                                 └─────────────────────┘
```

### Descripción

El circuito genera pruebas fuera de la cadena porque el cómputo es
intensivo (~5 segundos por prueba). Sólo la prueba compacta (3 puntos
elípticos + 5 signals públicos, ~200 bytes) viaja on-chain. La note
(secret + nullifier) nunca se revela a la red — esa es la propiedad
zero-knowledge. La persistencia opcional en disco (mixer_notes.jsonl)
es el safety net introducido en 2026-08-18 para recuperación manual si
el sub-agente pierde la note.

---

## 4.7 Vista de despliegue en producción (trabajo futuro)

### Esquema

```
   Cliente exchange
   ═══════════════
                                       ┌────────────────────┐
                                       │ Compliance         │
                                       │ dashboard          │
                                       │ (React / Vue)      │
                                       └─────────┬──────────┘
                                                 │ HTTPS
                                                 ▼
   API Gateway
   ═══════════        ┌─────────────────────────────────────┐
                      │ FastAPI                             │
                      │  POST /detect  → alerta on-demand    │
                      │  GET /alerts   → consulta histórica  │
                      └─────┬───────────────────────────────┘
                            │
   Servicios de detección
   ══════════════════════
             ┌──────────────┼───────────────┐
             ▼              ▼               ▼
       ┌──────────┐  ┌───────────────┐  ┌──────────┐
       │ GCN      │  │ LLM Coordinator│  │ Redis    │
       │persistido│──│ (async worker) │──│ cache    │
       │torch.save│  │                │  │ prompts  │
       │hot-swap  │  └────────┬───────┘  └──────────┘
       └──────────┘           │
                              ▼
   Persistencia
   ════════════
                     ┌───────────────────┐
                     │ PostgreSQL        │
                     │  alertas + prompts│
                     │  + decisiones     │
                     │  (audit trail     │
                     │   FATF R.11)      │
                     └───────────────────┘

   Worker asíncrono
   ════════════════
                     ┌───────────────────┐
                     │ Celery + Redis    │
                     │  batch nocturno   │
                     └───────────────────┘

   Observabilidad
   ══════════════
             ┌────────────────┐    ┌────────────────┐
             │ Prometheus     │───▶│ Grafana        │
             │ metrics        │    │ dashboards     │
             └────────────────┘    └────────────────┘
```

### Descripción

Esta vista NO forma parte del sistema implementado — es la propuesta de
trabajo futuro descrita en el Capítulo 10 §10.4.6. La arquitectura
introduce persistencia de modelos GCN (torch.save), caché de respuestas
LLM (Redis), base de datos de alertas auditable (PostgreSQL, requerida
por FATF R.11), procesamiento asíncrono (Celery) y observabilidad
(Prometheus + Grafana). El esfuerzo estimado es de 6-9 meses full-stack
+ presupuesto de infraestructura $100-500/mes según carga.

---

## 4.8 Renderizado alternativo

Los diagramas en ASCII de este capítulo están diseñados para ser
directamente legibles en Word y PDF. Para versiones renderizadas como
imágenes (con formato flowchart profesional), el repositorio GitHub del
proyecto contiene los mismos diagramas en formato Mermaid, exportables
a PNG/SVG mediante la CLI `@mermaid-js/mermaid-cli`. Ver `tfm/diagrams/`
en el repositorio para las imágenes generadas.

---

## 5.B Arquitectura del software

Este capítulo describe el pipeline extremo-a-extremo del sistema. La
Figura 3.1 (a incluir) resume las cinco capas: (1) capa de contratos
inteligentes; (2) capa de blockchain local (Foundry/Anvil) y despliegue
sobre Sepolia; (3) catálogo de herramientas on-chain que expone el
sistema al agente atacante; (4) orquestación multi-agente del atacante
mediante agentes LLM; y (5) arquitectura simétrica del detector con
clasificadores locales por exchange más coordinador LLM cross-exchange.
El código completo del sistema está disponible en el repositorio del
proyecto (~9 000 líneas Python + ~800 líneas Solidity + circuitos Circom
para el mezclador ZK).

## 6.1 Capa de contratos inteligentes

El sistema despliega seis contratos Solidity que replican
funcionalmente los primitivos DeFi utilizados en tipologías reales de
blanqueo:

**`MockUSDT.sol`** (63 líneas). Implementación mínima de un token ERC-20
con seis decimales, siguiendo la especificación externa del USDT real
pero sin las particularidades no relevantes (blacklist, fee-on-transfer).
La función `mint` es permissionless por diseño para facilitar la
siembra de wallets en la simulación; este trade-off convierte al
contrato en catastrófico en una red pública real y por eso está
etiquetado explícitamente como *research artifact*.

**`MockUniswapV2Pool.sol`** (96 líneas). Réplica funcional del contrato
Pair de Uniswap V2 para el par ETH/USDT, con la fórmula de producto
constante x · y = k. El agente atacante puede consultar el precio
spot vía `getReserves()` antes de decidir el tamaño de un swap. La
implementación es intencionalmente sin fee-on-swap para simplificar el
razonamiento del atacante sobre el *slippage*; la generalización con
fee del 0,3 % es trivial y no altera las conclusiones.

**`MockTornado.sol`** (140 líneas) + **`MerkleTreeWithHistory.sol`** (154
líneas) + **`Verifier.sol`** (196 líneas, auto-generado por snarkjs).
Réplica funcional de un mezclador estilo Tornado Cash con circuito
Groth16 real, árbol de Merkle de profundidad 10 (capacidad
2¹⁰ = 1 024 depósitos), y verificador on-chain. Cada depósito
requiere `1 ether` fijo (constante hard-coded); cada retirada exige una
prueba Groth16 válida que demuestre pertenencia al árbol sin revelar
qué depósito específico se está retirando. La preimagen se comprime
mediante MiMC-Sponge (contrato `MiMCSponge` auto-generado por
circomlibjs). Con este stack, los depósitos y retiradas del atacante son
criptográficamente indistinguibles de un Tornado Cash real desde el
punto de vista de los signals públicos del smart contract.

**`MockBridge.sol`** (94 líneas). Réplica funcional simplificada de un
puente cross-chain USDT: el operador bloquea tokens en el contrato
origen (evento `USDTLocked` con el destino en la cadena externa) y los
libera en el contrato destino tras verificación (evento `USDTReleased`).
El bridge simula transacciones cross-chain sin ejecutarlas realmente;
para el detector, la salida del bridge es indistinguible de un cash-out
hacia una jurisdicción no cooperativa. Los bridges reales
(Wormhole, Polygon PoS, Ronin) implementan multisig de validators,
verificación on-chain de firmas y proof de finalidad de la cadena
origen; el mock omite todo ese aparato y sustituye el consenso por un
único `operator` role, suficiente para modelar la SEÑAL on-chain que
un detector observaría. **Estado en las campañas de la evaluación**:
el bridge está desplegado y su tool `bridge_lock_usdt` disponible en
el catálogo del atacante, pero ninguna de las tres campañas Sonnet
(seed 400, 401, 403) ha ejercitado esta ruta —el atacante ha
preferido consistentemente structuring directo hacia clean exits
sobre bridging cross-chain—. El bridge queda como scaffold ready
para la extensión cross-chain descrita en §10.4.4.

**`Verifier.sol`** (196 líneas, auto-generado por
`snarkjs zkey export solidityverifier` aplicado al *zkey* del circuito
`withdraw`). Contiene la única función pública `verifyProof(a, b, c,
public_signals)` que evalúa la ecuación de emparejamiento Groth16
sobre los puntos de la curva BN128 aportados en la prueba. Cada
retirada del mezclador invoca este verifier con: (i) los tres puntos
del proof generados off-chain por snarkjs; (ii) las cinco *public
signals* (root Merkle, nullifier hash, recipient, fee, refund). Si el
verifier retorna `true`, la prueba demuestra —sin revelar cuál
depósito— que el prover conoce un secret que corresponde a un leaf
válido del árbol. Se distribuye sin modificar bajo GPL-3.0 heredada
de snarkjs.

**Justificación de la elección hand-written vs adaptación externa**.
Los contratos MockUSDT, MockUniswapV2Pool y MockBridge se han escrito
a mano (etiquetados `SPDX-License-Identifier: UNLICENSED`) por dos
razones: (i) los originales (Tether real, Uniswap V2 core, bridges
mainnet) traen decenas de MB de dependencias transitivas y features
no relevantes al *research* (blacklists, LP tokens, multisig,
upgradability por proxy) que aumentarían la superficie de código sin
beneficio metodológico; (ii) el marcado `UNLICENSED` + los comentarios
prominentes `NEVER deploy on a real chain` reducen el riesgo de que
un tercero re-utilice los mocks fuera del contexto research. En
contraste, los tres contratos derivados de Tornado Cash (MockTornado,
MerkleTreeWithHistory, Verifier) preservan literalmente el *core*
criptográfico —es lo que garantiza que la simulación tenga las
mismas propiedades de *soundness* y *zero-knowledge* que el sistema
real que las campañas de blanqueo explotan en mainnet, requisito para
que los *findings* del Capítulo 8 sean transferibles fuera del
entorno mock (§Anexo G para el detalle de atribuciones y licencias).

## 6.2 Capa de blockchain

El sistema opera sobre dos backends intercambiables mediante una
abstracción única: la clase `AnvilNode` en `src/aml/chains/anvil.py`
(context manager que arranca y destruye un nodo Anvil local, con diez
cuentas pre-fondeadas de 10 000 ETH cada una) para experimentación
rápida; y el módulo `deploy_eth_mocks_sepolia.py` para despliegue
persistente sobre la testnet pública Sepolia, con EIP-1559 gas,
validación estricta de `chain_id`, verificación de balance del
deployer, y persistencia de las direcciones desplegadas en
`deployments/sepolia.json` para que el runner de campañas pueda
reconstruir los handles de contrato.

La abstracción `AnvilNode` permite que exactamente el mismo código de
las herramientas del atacante y del detector se ejecute contra Anvil
(coste 0, velocidad de bloque instantánea) o contra Sepolia (coste
0,02–0,05 ETH testnet, bloque cada 12 s). Esta portabilidad es la que
habilita la validación externa on-chain descrita en el Capítulo 8.

## 6.3 Catálogo de herramientas on-chain

El atacante interactúa con la blockchain exclusivamente a través de un
catálogo de diecinueve herramientas expuestas al LLM como funciones
JSON schema (`_TOOL_SCHEMAS` en `src/aml/attackers/tools.py`). Cada
herramienta tiene un nombre, descripción, esquema de parámetros y una
implementación Python que traduce la llamada en una transacción
firmada sobre la blockchain. El dispatcher (`ToolDispatcher`) gestiona
el ciclo de: (i) recepción de la llamada del LLM, (ii) validación de
los parámetros contra el schema, (iii) firma y envío de la transacción,
(iv) espera del receipt, y (v) devolución del resultado estructurado
(`ToolResult`) al LLM en el siguiente turno de la conversación.

Las herramientas se agrupan funcionalmente en cinco familias:

- **Introspección** (`get_balance`, `get_gas_budget`, `get_swap_quote`,
  `inspect_chain`): permiten al agente consultar estado sin gastar gas.
- **Transferencia básica** (`transfer_usdt`, `transfer_eth`,
  `mint_usdt`): envío directo entre EOAs, minteo permissionless.
- **Wallet management** (`generate_burner_wallet`, `register_clean_exit`):
  creación de direcciones nuevas y registro de destinos finales
  etiquetados con plataforma.
- **Layering primitives** (`smurf_split`, `smurf_eth_split`,
  `peel_chain`, `swap_eth_for_usdt`, `swap_usdt_for_eth`,
  `advance_blocks`): las primitivas para la fase de *layering* de la
  taxonomía FATF.
- **Mixing** (`mixer_deposit`, `mixer_withdraw`,
  `mixer_batch_deposit`, `mixer_batch_withdraw`): interacción con el
  mezclador ZK, incluyendo variantes *batched* que consolidan
  múltiples ciclos en una sola llamada LLM (PR #48). La generación
  off-chain de la prueba Groth16 se ejecuta con snarkjs.

El *dispatcher* dispone además de un helper interno
`sweep_funder_pool` no expuesto al LLM: el runner lo invoca al final
de la campaña para consolidar el residual del *pool* de funders
sobre una dirección de destino previamente registrada, cerrando
formalmente el ciclo operativo.

**Restricciones estructurales del `ToolDispatcher`.** Cinco *invariants*
se aplican de forma centralizada en el *dispatcher* (no en cada
herramienta) para reflejar disciplina operativa realista:

1. **Guardarraíl del deployer**. La dirección
   `0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4` (deployer de contratos
   en Sepolia) no puede aparecer como `from`, `to`, `gas_payer` ni
   `recipient` de ninguna llamada. Un atacante realista no expone la
   *hot wallet* de despliegue en la campaña de *laundering*, y esta
   restricción evita que las métricas de recuperación se inflen por
   fondos que nunca salieron del *stack* de infraestructura.
2. **Cap dinámico de burners**. El agente no puede solicitar más de
   max(30, min(250, 3 · ⌈ USD/999 ⌉))
   direcciones burner por campaña, calibrado sobre el volumen laundered.
   La cota inferior de 30 preserva flexibilidad táctica en campañas
   pequeñas; la cota superior de 250 evita bucles patológicos de
   generación observados en versiones tempranas (§8.9.5).
3. **`_pick_funder`** rota entre wallets del *pool* de funders y sólo
   solicita *refill* cuando el balance cae por debajo de 0,01 ETH
   (umbral) hasta un techo de 0,025 ETH (refill target).
4. **Gas reserve** por defecto de 0,005 ETH sobre cada burner (frente a
   los 0,05 ETH iniciales), alineado con el gas dust real de un burner
   Ethereum bajo condiciones EIP-1559 típicas.
5. **`sweep_funder_pool`** consolida el residual del *pool* al final de
   la campaña en una única dirección de destino, produciendo una
   última transferencia auditable que un detector real observaría como
   la señal de cierre de operaciones.

La granularidad del catálogo es una decisión de diseño relevante. Las
primeras iteraciones exponían herramientas atómicas (una transacción
ERC-20 = una llamada), pero el consumo de contexto por parte del LLM
crecía linealmente con el número de wallets involucradas. La versión
final incluye herramientas *batched* (`smurf_split` procesa hasta N
receptores en una sola llamada) que reducen el ratio tokens/transacción
en un orden de magnitud sin sacrificar granularidad forense.

## 6.4 Orquestación multi-agente del atacante

El atacante está estructurado como un sistema multi-agente jerárquico
con dos niveles.

**Nivel 1 — Coordinador** (`src/aml/attackers/coordinator.py`, ~350
líneas). Instanciado sobre Claude Opus 4.7 por su capacidad superior
de planificación de largo alcance. El coordinador recibe como input un
escenario de blanqueo de alto nivel (por ejemplo, "lava 500 000 USDT
desde el hackeo de Upbit hacia dos direcciones limpias de destino") y
descompone la campaña en subtareas asignables a los sub-agentes.
Mantiene estado global (wallets creadas, balances, direcciones de
destino comprometidas) y decide cuándo cambiar de fase FATF
(Placement → Layering → Integration). El coordinador no ejecuta
transacciones directamente; sólo emite invocaciones a los sub-agentes.

**Nivel 2 — Sub-agentes especializados** (`src/aml/attackers/sub_agent.py`,
~300 líneas). Tres roles paralelos instanciados sobre Claude Sonnet 4.6
(mejor coste/rendimiento para tareas tácticas con horizonte corto):

- *Placement Agent*: introduce los fondos en el sistema (minteo de USDT,
  fondeo de la primera wallet, split inicial en burners).
- *Layering Agent*: ejecuta las técnicas de ofuscación (smurfing, peel
  chains, swaps, depósitos/retiradas en el mezclador, uso del bridge).
  Es el sub-agente que consume el 80 % de las herramientas del catálogo.
- *Integration Agent*: consolida los fondos en las direcciones de
  destino limpias, minimizando el número de saltos finales que un
  detector podría correlacionar.

Los prompts de cada rol se encuentran en `src/aml/attackers/prompts.py`
(~420 líneas de texto estructurado). Cada prompt incluye: (i) contexto
de la fase FATF que el sub-agente debe ejecutar; (ii) el catálogo de
herramientas disponibles para su rol; (iii) restricciones tácticas
(umbral CTR de 10 000 USD, tamaño máximo de swap para evitar
*slippage* superior al 3 %); y (iv) un guardrail que impide al agente
transferir a direcciones fuera de su wallet pool sin justificación
explícita.

**Ejemplo concreto del flujo Coordinator → sub-agentes**. Para hacer
tangible el patrón multi-agente, se resume el trazado real de la
primera fase de la campaña *defi-exploit* con 10 ETH robados. El
Coordinador, tras recibir la consigna del scenario, decide en su
primer turn: (i) meta cuantitativa 10 ETH × $1 880 =
$18 809 USD a *laundering*; (ii) ⌈18 809/999⌉ × 2 = 38
*clean exits*, reparto no uniforme Binance/Coinbase/Kraken
(15/12/11); (iii) trifurcación de rutas ROUTE A (nuevos burners) +
ROUTE B (recycle burners para crear ciclos) + ROUTE C (mixer con
timing desordenado). El Coordinador NO ejecuta transacciones; delega
vía `delegate_to_placement`, `delegate_to_layering` e
`delegate_to_integration`. El Placement specialist recibe *"break
10 ETH from alice into 10 working wallets of ≈ 1 ETH each"*
y ejecuta `generate_burner_wallet` + `transfer_eth` diez veces,
reportando `success` con las diez wallets creadas. El Layering
specialist recibe *"corre las 10 working wallets por el ZK Tornado
+ trifurcación A/B/C"* y ejecuta ciclos como `mixer_deposit` →
`advance_blocks(30)` → `generate_burner_wallet` (recipient) +
`generate_burner_wallet` (gas_payer independiente) → `mixer_withdraw`
con la prueba Groth16 generada off-chain por snarkjs
—el resultado es que la wallet destino recibe 1 ETH sin *link*
on-chain con *alice*, el Merkle root público sólo prueba que "algún
depositante" retiró—. El Integration specialist recibe *"registra
38 clean exits distribuidos 15/12/11 Binance/Coinbase/Kraken,
fondea 25 con montos \200-\970 (sub-CTR), deja 3 vacíos como
distractores"* y ejecuta 38 llamadas a `register_clean_exit`
seguidas de `smurf_split` (*batched*: una única llamada LLM ejecuta
8-15 transferencias USDT sub-$999 a las direcciones
registradas). Al terminar las tres fases, el runner invoca
`sweep_funder_pool` sobre un *clean exit* residual y persiste todos
los artefactos.

**Cliente LLM abstracto** (`src/aml/attackers/llm_client.py`, ~260
líneas). Encapsula el SDK oficial de Anthropic tras una interfaz
`complete(prompt, system, model, max_tokens)` que devuelve un
`LLMResult` con `text`, `input_tokens`, `output_tokens` y `cost_usd`.
Un *Software Development Kit* (SDK) es el conjunto de librerías que
un proveedor distribuye para que el consumidor evite construir
peticiones HTTP manualmente; en este caso el paquete `anthropic`
(PyPI, licencia MIT) cubre autenticación por API key, versionado de
la API, retry automático ante errores transitorios, *streaming*
opcional y serialización de las respuestas. El *wrapper* propio
añade: (i) uniformar el cálculo de costes entre Opus, Sonnet y Haiku
(los precios por millón de tokens de entrada/salida difieren en dos
órdenes de magnitud entre tiers y el SDK crudo sólo devuelve counts);
(ii) sustituir el proveedor sin tocar el resto del código (si
mañana se quisiera evaluar GPT-4 o Gemini bastaría con
reimplementar `complete` sobre el SDK correspondiente); y (iii)
inyectar `MockLLMClient` en los tests unitarios para que el CI no
consuma créditos API en cada corrida.

**Runner de campaña** (`src/aml/attackers/run_campaign.py`, ~420
líneas). Punto de entrada CLI del atacante, invocable como
`python -m aml.attackers.run_campaign --scenario defi-exploit --seed
42 --amount 10 --model sonnet`. Ejecuta trece pasos secuenciales:
(1) parseo de los *flags* CLI; (2) carga de `ANTHROPIC_API_KEY` y
demás secretos desde `.env`; (3) instanciación del `AnvilNode`
context manager que arranca un proceso `anvil` local (o alternativa
Sepolia mediante `run_sepolia_campaign.py`); (4) despliegue de los
seis contratos mock + bootstrap del pool Uniswap; (5) instanciación
del `PriceOracle` con `campaign_ts = resolve_campaign_ts(...)`
(§3.4, market context); (6) generación determinista de la wallet
*alice* a partir del seed; (7) fondeo exacto de alice al `amount`
declarado (Alice normalization, ver más abajo); (8) construcción del
*user prompt* mediante `scenario.format_prompt(alice, amount)` con
el bloque *MARKET CONTEXT* pre-formateado inyectado; (9)
instanciación del Coordinador con el *client* LLM + prompts +
catálogo de tools + dispatcher; (10) ejecución del bucle multi-turn
LLM ↔ tools hasta que el Coordinador emite el token de terminación;
(11) invocación de `sweep_funder_pool` sobre una dirección de
destino registrada para consolidar el residual operativo; (12)
persistencia del *bundle* de artefactos (`meta.json`,
`campaign.json`, `chain_trace.jsonl`, `addresses.json`,
`summary.txt`) en un directorio con *timestamp*; (13) *reporting*
final de recovery %, coste USD, wall clock, número de iteraciones
del Coordinador y sub-agentes. Duraciones típicas: ≈ 3 min y
$0,30para1ETH con Haiku;~ 20min y~ \3 para
10 ETH con Sonnet.

**Escenarios de campaña** (`src/aml/attackers/scenarios.py`, ~230
líneas). Un `Scenario` empaqueta una tipología de blanqueo con la
configuración *chain* que el runner necesita más la plantilla del
*user prompt* que se pasa al Coordinador. La estructura permite
ejecutar múltiples tipologías cambiando únicamente el *flag*
`--scenario` sin tocar código. Tres escenarios están *locked* en la
versión de evaluación:

- **`defi-exploit`**: robo estilo hack DeFi. Activo ETH, cantidad
  por defecto 3 ETH, requiere Pool y Tornado. La táctica firma es
  el ciclo `transfer_eth` (Placement, no smurfing) →
  `mixer_deposit` × N (Layering, el ZK mixer es el corazón de la
  ofuscación) → `swap_eth_for_usdt` + `smurf_split` (Integration).
- **`stablecoin-scam`**: fraude tipo phishing/romance/ponzi. Activo
  USDT, cantidad por defecto 8 000 USDT, requiere Pool (opcional
  para asset cycling), NO requiere Tornado (el mezclador es
  ETH-only y la mayor parte del laundering de *stablecoin* real
  opera enteramente en USDT). La táctica firma es
  `smurf_split(alice, sub-999 chunks)` → cadenas multi-hop con
  ciclos ROUTE A + B → opcional `swap_usdt_for_eth` /
  `swap_eth_for_usdt` para romper *token-level tracing* → fan-out a
  clean exits.
- **`ransomware-cashout`**: cash-out de rescate. Activo ETH,
  cantidad por defecto 5 ETH, requiere Pool y Tornado con uso
  intensivo. La táctica firma es que TODAS las working wallets
  pasan por el mezclador, algunas dos veces (deposit → withdraw →
  deposit → withdraw a otra wallet nueva), y se aplica la
  trifurcación completa A+B+C.

Añadir un escenario nuevo requiere únicamente escribir una instancia
`Scenario` en `scenarios.py` y registrarla en el diccionario
`SCENARIOS`; el CLI la detecta automáticamente. Los *prompts* de los
tres escenarios se reproducen en el §Anexo A junto con los *system
prompts* de los cuatro roles.

**Oracle de precios y *market context*** (`src/aml/env/market_context.py`
+ `src/aml/env/oracle.py`). Un oráculo determinista con caché en
`data/prices/{eth,trx,usdt}.csv` (histórico diario descargado desde
CoinGecko) inyecta en el *system prompt* del coordinador el precio
spot ETH/USDT/TRX al momento de la campaña. Con esta señal el agente
razona en unidades USD (el umbral CTR de 999 USD es unidades
naturales para el atacante, no unidades ETH), calibra el tamaño de los
depósitos al mezclador contra la denominación fija de 1 ETH, y
ajusta la cadencia de swaps al *slippage* observado. El bloque
inyectado tiene la forma:

```
MARKET CONTEXT (spot @ 2026-08-16 UTC):
  1 ETH  = 1,880.96 1 USDT =0.9998   1 TRX = $0.2400
FATF thresholds in current spot terms:
  $10,000 CTR   ~ 5.317 ETH   ~ 10,002 USDT
  $999 sub-CTR  ~ 0.5311 ETH  ~ 999 USDT
```

Sin esta pre-computación, el LLM tendría que convertir USD→ETH
mentalmente en cada decisión de tamaño, un error recurrente
observado en corridas tempranas sin market context. El helper
`build_market_context(oracle, campaign_ts)` produce el bloque, y el
helper `resolve_campaign_ts(oracle, override_iso)` decide QUÉ fecha
usar para el precio spot: (i) si se pasa `--campaign-ts 2026-08-16`
al runner, esa fecha exacta (reproducibilidad byte-idéntica entre
corridas del mismo seed); (ii) si no se pasa nada, `now(UTC)`
clampeado al último día cacheado en el CSV (evita crashear si el
cache no llegó al día actual); (iii) en Sepolia, el runner refresca
el cache primero y pasa `now(UTC)` directo (validación externa
real-time). **La misma pareja de helpers se usa en el defensor**
(`aml.detectors.multi_agent.LLMDefenderCoordinator`), garantizando
que atacante y defensor jueguen bajo idéntica realidad de precios y
la comparativa entre ambos sea metodológicamente válida.

**Diseño del *funder pool*** (`src/aml/attackers/funder_sizing.py`).
Antes de arrancar la campaña, el runner asigna el capital operativo
(hasta un 5 % del *amount* laundered, techo \le 1 ETH por funder,
piso \ge 0,02 ETH) sobre 2-10 wallets funder mediante
`allocate_funder_amounts`, con distribución tier-based no uniforme
para replicar patrones observados en operaciones reales (Chainalysis
2023). Cada burner es re-fondeado por el *dispatcher* con esos funders
en rotación. El *pool* completo se vuelca al final vía
`sweep_funder_pool` sobre una dirección de destino registrada por el
sub-agente Integration, cerrando el ciclo operativo del atacante.

**Alice normalization**. El runner drena la wallet fuente (*alice*)
exactamente al `amount` declarado del escenario, sin añadir buffer
adicional. Un atacante real dispone únicamente de los fondos robados;
el pre-fondeo generoso de amount + 0,05 ETH utilizado en
versiones tempranas contaminaba el denominador de las métricas de
recuperación y fue eliminado antes de la corrida canónica seed 400.

## 6.5 Arquitectura simétrica del defensor

El defensor replica la estructura multi-agente del atacante pero
adaptada al rol defensivo. Está compuesto por dos capas.

**Capa 1 — Clasificador GCN local por exchange** (`src/aml/detectors/gnn.py`,
~360 líneas + `src/aml/detectors/graph.py` para conversión NetworkX ↔
PyTorch Geometric). Cada exchange X_i observa el subgrafo inducido
por las aristas incidentes en sus direcciones KYC y entrena un
clasificador GCN de dos capas (128 hidden units, dropout 0,5, Adam
lr 10⁻³, 50 épocas). Las features de nodo son **19-dimensionales**
(constante `FEATURE_NAMES` en `src/aml/detectors/gnn.py:80`)
organizadas en cuatro grupos:

*Grupo 1 — degree features (3)*: `in_degree`, `out_degree`,
`total_degree`. Capturan cuántas aristas inciden en el nodo
independientemente del tipo, aproximando la actividad neta del wallet.

*Grupo 2 — flujos de valor (4)*, todas transformadas con \log(1+x)
para comprimir la distribución que puede abarcar varios órdenes de
magnitud: `log_eth_in`, `log_eth_out`, `log_usdt_in`, `log_usdt_out`.
Suma total del ETH y USDT recibido y enviado por el nodo.

*Grupo 3 — diversidad de contrapartes (2)*, análogamente
log-transformadas: `log_unique_in` (contrapartes únicas que envían
al nodo), `log_unique_out` (contrapartes únicas que reciben del
nodo). Un burner de laundering típicamente tiene pocas contrapartes
altamente conectadas; un usuario benigno de DeFi tiene muchas
contrapartes con menor recurrencia.

*Grupo 4 — conteo por tipo de arista (10 = 5 tipos × 2 direcciones)*:
`transfer_eth_in/out`, `transfer_usdt_in/out`, `swap_in/out`,
`mixer_deposit_in/out`, `mixer_withdraw_in/out`. Estos diez features
son los que mejor discriminan patrones AML: los cuatro features
`mixer_*` distinguen inmediatamente al usuario del mezclador ZK
(señal más fuerte para las campañas *defi-exploit* y
*ransomware-cashout*), mientras los `transfer_*` capturan el fan-out
de structuring (señal principal para *stablecoin-scam*).

Total: 3 + 4 + 2 + 10 = 19 features float32 por dirección. La
salida del GCN es una probabilidad p_i(v) ∈ [0, 1] por dirección
visible. Un umbral de 0,5 produce el flag binario que se propaga a
la capa 2.

**Baselines para comparativa**. El módulo `src/aml/detectors/baselines.py`
implementa un detector Louvain sobre comunidades (Blondel et al. 2008)
como baseline no supervisado, un detector GAT alternativo, y el
detector `PerExchangeDetector` que aplica cualquier detector base a
cada view por separado. Estos baselines permiten separar la
contribución de la arquitectura simétrica LLM-vs-LLM del efecto
absoluto del clasificador GCN.

**Capa 2 — Coordinador LLM cross-exchange** (`LLMDefenderCoordinator`
en `src/aml/detectors/multi_agent.py`, ~250 líneas dedicadas). Este
componente implementa la simetría con el coordinador atacante. Recibe
como input el top-K de direcciones flageadas por cada uno de los tres
exchanges (K=60 por defecto, seleccionando por probabilidad descendente),
junto con las features de cada dirección. Construye un prompt
estructurado que presenta al LLM las tres vistas como bloques
independientes y le pide una asignación explícita de cada dirección a
un cluster con justificación textual.

El *system prompt* del coordinador está calibrado para producir entre
10 y 25 clusters (rango realista para 20 campañas atacantes + benign
outliers) y para nombrar los clusters con arquetipos AML reconocibles:
*cross-exchange mixer hub*, *pass-through mixer relay*, *pure mixer
depositor*, *fan-out distributor*, *consolidation sink*. El output es
JSON compacto en formato `{cluster_id: [direcciones]}`; un parser
tolerante (`_parse_llm_clusters`) maneja tanto este formato como una
variante verbose con reasoning por-cluster, y aplica un *fallback* de
similaridad coseno si el parseo falla o el LLM asigna direcciones
inexistentes en el input.

**Detector cosine baseline**. La clase base `MultiAgentDetector`
implementa la misma interfaz que `LLMDefenderCoordinator` pero
sustituye el LLM por un algoritmo simple de clustering por similaridad
coseno sobre los vectores de features (umbral 0,95). Esta clase
existe explícitamente como *strawman* comparativo para la evaluación
del Capítulo 8: cualquier ganancia del LLM debe justificarse contra
esta baseline no paramétrica.

**Métricas** (`src/aml/detectors/eval.py`, ~185 líneas). Se computan
cuatro métricas clave:

- *F1 binario*: clasificación intra-exchange (¿esta dirección es
  ilícita?).
- *Adjusted Rand Index (ARI)*: acuerdo entre el clustering predicho
  y el ground-truth de actor cluster, corregido por azar.
- *Homogeneidad*: fracción de clusters puros (todas las direcciones
  del mismo cluster pertenecen al mismo actor real).
- *Completitud*: fracción de actores reales cuyas direcciones caen en
  un único cluster predicho.

## 6.6 Federación y visibilidad parcial

La conversión del grafo global G en las tres vistas {G_1, G_2, G_3}
se realiza mediante `partial_visibility_split` en
`src/aml/detectors/dataset.py` (~430 líneas). El algoritmo:

1. Asigna cada dirección v ∈ V a un único exchange
   X_i ∈ {X_A, X_B, X_C} mediante hashing del hash de v con
   una semilla determinista.
2. Define el subgrafo visible de X_i como el conjunto de aristas
   (u, v) ∈ E tales que u ∈ X_i o v ∈ X_i.
3. Etiqueta como *KYC-known* (etiqueta accesible para el entrenamiento
   del clasificador local) sólo aquellas direcciones v ∈ X_i.
   Las contrapartes de otros exchanges aparecen en el subgrafo pero
   son etiquetadas como *unknown counterparty*.

Este esquema replica fielmente la asimetría regulatoria descrita en
el Capítulo 2: un exchange puede observar transacciones incidentes
en sus usuarios KYC pero no dispone de las etiquetas de las
contrapartes externas. Es la restricción que hace que el problema
cross-exchange requiera un coordinador de segundo nivel.

**Inputs de los detectores bajo cada régimen de visibilidad**. La
diferencia operativa entre *full visibility* (baseline académica
histórica, no realista bajo MiCA) y *partial visibility federada*
(régimen de este trabajo) se resume en cuatro dimensiones:

| Dimensión                  | Full visibility (baselines académicos)          | Partial visibility federada (este TFM)                                    |
|----------------------------|-------------------------------------------------|---------------------------------------------------------------------------|
| Grafo de entrenamiento     | G global (10 311 nodos, 101 880 aristas)      | Tres subgrafos {G_A, G_B, G_C} inducidos por las aristas incidentes en las direcciones asignadas al exchange respectivo (~55 % de aristas cada uno por overlap de contrapartes) |
| Features                   | 19 dimensiones computadas sobre G global      | 19 dimensiones computadas sobre el subgrafo G_i: los valores difieren respecto al régimen global (ej. `in_degree` de una contraparte compartida es menor porque cada vista sólo ve una fracción de sus aristas) |
| Etiquetas de entrenamiento | Todas las 649 direcciones adversariales visibles | Sólo las ~216 direcciones adversariales asignadas a X_i (el resto aparecen como *unknown counterparty*) |
| Agregación cross-exchange  | Directa sobre el grafo global                   | Vía la capa 2 (coseno o LLM coordinator) sobre *fingerprints* per-address SIN acceso a los grafos crudos de los otros exchanges |

Los detectores del Capítulo 8 se entrenan y reportan **exclusivamente
bajo partial visibility**. Los benchmarks históricos que operan bajo
full visibility (Weber et al. 2019 sobre Elliptic, la mayoría de
GNN AML publicadas) sirven como referencia contextual del techo
teórico pero no como comparativa directa —replicar sus F1 bajo
visibilidad parcial requiere un mecanismo cross-exchange que la
literatura previa no aporta. La contribución arquitectónica del
presente trabajo (§1.3, contribución 2) es precisamente cerrar ese
gap con el coordinador de capa 2.

## 6.7 Repositorio y reproducibilidad

El código íntegro está publicado en
[`github.com/0xAnonsal/aml-thesis`](https://github.com/0xAnonsal/aml-thesis)
bajo licencia MIT (con la advertencia expresa de que los contratos son
*research artifacts* con `mint` permissionless y no deben usarse en
mainnet). El repositorio incluye:

- Suite de tests con más de 300 tests unitarios sobre las cinco capas
  (`pytest tests/` — cobertura > 85 %).
- Scripts de reproducción end-to-end para los tres detectores sobre
  los cuatro datasets (Elliptic++, OpenAML v1, EthereumHeist,
  simulación propia).
- Runbook de despliegue Sepolia (`docs/SEPOLIA_DEPLOY.md`) con
  comandos exactos, verificación en Etherscan, y troubleshooting.

Cada resultado numérico reportado en el Capítulo 8 se acompaña del
comando `python scripts/<nombre>.py <argumentos>` que lo regenera; los
resultados en formato JSON residen en `results/` bajo control de
versiones para trazabilidad.

---

## 5.C Lenguajes de programación empleados

El sistema combina cuatro lenguajes distintos, cada uno en su ámbito
natural: Python para orquestación multi-agente + análisis de datos;
Solidity para contratos inteligentes on-chain; Circom para el circuito
zero-knowledge; y JavaScript (via Node.js) para la interfaz off-chain
con snarkjs. Esta pluralidad es consecuencia directa del dominio del
problema —cada capa técnica tiene un lenguaje incumbente con
madurez de librerías y ecosistema— y no una decisión arbitraria.

## 5.1 Python 3.11 — lenguaje principal (≈ 9 000 LOC)

**Rol**: orquestación del sistema completo. Todo el pipeline
atacante multi-agente, todo el pipeline defensor, la abstracción
de blockchain, los scripts de reproducción experimental, los tests
unitarios y de integración. En términos absolutos, ≈ 90 %
del código propio del proyecto es Python.

**Justificación de la elección**:

1. **Madurez del ecosistema ML**: PyTorch + PyTorch Geometric +
   scikit-learn + NetworkX no tienen equivalente comparable en
   ningún otro lenguaje. La detección AML mediante GNN es
   intrínsecamente una tarea de PyTorch.
2. **SDK oficial de Anthropic**: `anthropic` (PyPI) es el cliente
   de referencia para la API de Claude. Alternativas en TypeScript
   o Go existen pero con menor completitud y actualización más
   lenta.
3. **Interfaz web3.py**: cliente Ethereum de referencia. La
   alternativa `ethers.js` es TypeScript y forzaría un mixed
   codebase con Python-para-ML + TypeScript-para-blockchain con
   IPC entre ambos.
4. **Legibilidad de investigación**: el TFM debe ser inspeccionable
   por otro investigador AML sin experiencia en tipado estático;
   Python permite prototipar rápidamente sin *boilerplate* de
   tipos genéricos.

**Convenciones y estilo**:

- Formato PEP-8 con línea máxima 100 caracteres (verificado por
  `ruff check` con reglas E/F/I/B/UP).
- Type hints (typing + `from __future__ import annotations`)
  en interfaces públicas y clases *dataclass*.
- Docstrings estilo Google (`Args:`/`Returns:`/`Raises:`) en cada
  función pública.
- Ninguna función atacante o detector tiene más de ≈ 150 líneas;
  los helpers *complejos* (por ejemplo `_mixer_collect_leaves` con su
  retry loop) están extraídos a métodos privados.

**Estructura del paquete `src/aml/`**:

```
src/aml/
├── attackers/     ~2500 LOC — multi-agente ofensivo
├── detectors/     ~3500 LOC — pipeline defensivo + baselines
├── chains/        ~1200 LOC — abstraccion blockchain
├── env/           ~500 LOC — oracle precios + market context
└── utils/         ~300 LOC — helpers cross-cutting
```

## 5.2 Solidity 0.8.20 — contratos inteligentes (≈ 800 LOC)

**Rol**: seis contratos on-chain que replican el *stack* DeFi
sobre el que operan las tipologías reales de blanqueo. Escrito en
Solidity porque es el lenguaje objetivo canónico de EVM y el único
soportado por Foundry para tests + compilación directa.

**Justificación de la elección**:

1. **Solidity es EVM-native**: alternativas (Vyper, Yul) son
   marginales y perderían compatibilidad con el ecosistema de
   auditoría, herramientas y comunidad AML.
2. **Foundry ↔ Solidity ↔ Anvil**: cadena de compilación +
   ejecución + tests en un único lenguaje, sin cross-language
   FFI ni JS.
3. **Referencia de Tornado Cash + Uniswap V2**: los contratos
   adaptados están originalmente en Solidity; reescribirlos en
   otro lenguaje introduciría riesgo de divergencia semántica del
   *core* criptográfico.

**Convenciones**:

- Cada contrato marcado con `SPDX-License-Identifier` (MIT para
  adaptaciones de Tornado, UNLICENSED para *hand-written* research
  artifacts, GPL-3.0 heredada para el `Verifier.sol` auto-generado).
- Cabecera de comentario `ATTRIBUTION` en contratos derivados que
  documenta la fuente + naturaleza de la adaptación.
- Marcado prominente `NEVER deploy on a real chain` en los mocks
  con funciones `mint` permissionless.
- Uso exclusivo de características del lenguaje 0,8+
  (overflow checks nativos, `receive()`, `struct` con typed
  members).

**Contratos**:

```
contracts/
├── MockUSDT.sol              63 LOC — ERC-20 6 decimales
├── MockUniswapV2Pool.sol     96 LOC — AMM constant-product
├── MockTornado.sol          140 LOC — mixer ZK
├── MerkleTreeWithHistory.sol 154 LOC — arbol append-only
├── MockBridge.sol            94 LOC — bridge lock-and-release
├── Verifier.sol             196 LOC — auto-gen snarkjs
├── IHasher.sol               15 LOC — interfaz MiMC
└── IVerifier.sol             15 LOC — interfaz Groth16
```

## 5.3 Circom 2.0 — circuito zero-knowledge (≈ 60 LOC)

**Rol**: el circuito aritmético `withdraw.circom` que define
matemáticamente la afirmación *"conozco un `(nullifier, secret)`
cuyo commitment MiMC está en el árbol Merkle representado por
`root`, y el `nullifierHash` de la retirada es
`MiMC(nullifier)`"*. La prueba Groth16 sobre este circuito es lo
que la retirada del mezclador aporta on-chain.

**Justificación de la elección**:

1. **Estándar de facto en el ecosistema Ethereum ZK**: Tornado
   Cash, Semaphore, la mayoría de zkApps consumidas por proyectos
   AML de referencia (TRM Labs, Chainalysis Reactor) usan
   circom + snarkjs + Groth16.
2. **Toolchain completa**: `circom` (compilador) + `snarkjs`
   (prover) + `circomlib` (primitivas) + Powers of Tau Hermez
   (trusted setup público reutilizable) forman un stack cohesivo
   sin fragmentación entre lenguajes.
3. **Reproducibilidad**: los `.circom` son texto legible en Git,
   auditables por terceros. Alternativas como Halo2 (Rust) o
   Noir (Rust-like) tendrían la barrera de entrada de Rust para
   los revisores del TFM.

**Alternativas descartadas**:

- **Halo2**: sin trusted setup pero API mucho más baja y madurez
  posterior; el ecosistema AML aún no lo adopta.
- **Cairo (Starknet)**: no compatible con EVM sin puente adicional.
- **Noir**: prometedor pero aún alpha en 2026.

## 5.4 JavaScript / Node.js — puente off-chain (≈ 200 LOC)

**Rol**: script `scripts/zk_helpers.js` que expone tres subcomandos
consumidos por Python via `subprocess`:

- `node zk_helpers.js mimc <nullifier>` → `MiMC(nullifier)` como int.
- `node zk_helpers.js mimc2 <nullifier> <secret>` →
  `MiMC(nullifier, secret)` = commitment como int.
- `node zk_helpers.js merkle-path <depth> <leaf_idx> <leaves_file>` →
  JSON con `root`, `pathElements[10]`, `pathIndices[10]`.

Además el script `scripts/install_zk_tools.sh` gestiona la
instalación de `node`, `circom` (via `cargo install`), `snarkjs`
(npm global) y `circomlib` (npm global).

**Justificación**:

1. **`circomlibjs` solo existe en JavaScript**: la biblioteca que
   evalúa MiMC off-chain igual que el contrato Solidity está
   escrita en JS y no tiene port oficial a Python.
2. **`snarkjs` es CLI de Node**: la generación de pruebas se
   invoca inevitablemente como proceso Node.
3. **Alternativa Rust** (`arkworks-rs`): funciona pero requiere
   compilación específica del circuito con toolchain paralela
   circom-rust; añade complejidad sin ganancia clara.

Se aisla el JS al mínimo indispensable —Python delega a Node solo
para operaciones criptográficas específicas y consume el resultado
como stdout parseable—.

## 5.5 Bash / shell — scripts de setup (~100 LOC)

**Rol**: scripts idempotentes de setup del entorno ZK:

- `scripts/install_zk_tools.sh` — instala Node + circom + snarkjs.
- `scripts/setup_zk.sh withdraw` — trusted setup fase 2 del
  circuito `withdraw` (compila circom → R1CS → wasm → zkey →
  verifier.sol).
- `scripts/run_sepolia_full_campaign.sh` — orquesta múltiples
  campañas Sepolia en secuencia (histórico, no ejercitado en la
  evaluación final).

**Justificación**: los pasos de setup son secuencias imperativas
con dependencias entre herramientas de sistema, no lógica de
dominio. Bash es el estándar para orchestration ligera en Linux
y todos los usuarios objetivo (investigadores AML sobre WSL o
Linux nativo) lo conocen.

## 5.6 Markdown — documentación y draft del TFM (≈ 4 000 líneas)

**Rol**: todo el TFM (chapters + anexos) + los `README.md` +
`ROADMAP.md` + `docs/*.md` están en Markdown.

**Justificación**:

1. **Portabilidad**: convertible a `.docx` (via python-docx o
   pandoc), PDF (via pandoc + XeLaTeX) o HTML (via cualquier
   markdown renderer) sin locked-in en un formato propietario.
2. **Versionado en Git**: los diffs son legibles y permiten
   revisión línea a línea de cada cambio sin abrir Word.
3. **Diagramas Mermaid embebidos** (Capítulo 4): syntax highlight
   + preview integrado en GitHub y VS Code, sin dependencia
   externa.

Alternativas consideradas y descartadas:

- **LaTeX**: superior en tipografía matemática pero introduce una
  fricción de compilación innecesaria para el volumen matemático
  de este trabajo.
- **Word directo**: pierde versionado + reproducibilidad + integración
  con el resto del código.

## 5.7 Resumen cuantitativo

| Lenguaje    | LOC aprox.   | % del código propio | Rol                        |
|-------------|--------------|---------------------|----------------------------|
| Python      | 9 000        | ~86 %               | orquestación + ML + tests   |
| Solidity    | 800          | ~8 %                | contratos on-chain          |
| JavaScript  | 200          | ~2 %                | puente ZK snarkjs           |
| Bash        | 100          | ~1 %                | setup toolchain             |
| Circom      | 60           | ~0.6 %              | circuito ZK withdraw        |
| Markdown    | ~4 000       | —                   | documentación (no ejecutable) |

La proporción refleja el diseño: Python domina porque la
contribución novel (multi-agente LLM sobre grafo federado) vive
en Python; Solidity + Circom son necesarios pero minimalistas.
El mixing de lenguajes se justifica por incumbencia técnica —cada
lenguaje aporta su ecosistema— y se controla mediante interfaces
finas (subprocess a Node, RPC JSON-RPC a Anvil/Sepolia,
`solc`/`forge` a Solidity).

---

## 5.D Datasets y parámetros


Este capítulo describe el diseño experimental que produce los resultados
del Capítulo 8. Se detallan: (i) los cuatro *datasets* utilizados y su
provenance; (ii) el particionado federado en n=3 exchanges; (iii) la
configuración exacta de cada detector; (iv) la estrategia de
*cross-validation* diseñada para detectar y prevenir memorización;
(v) las métricas de evaluación; (vi) la parametrización de los
componentes LLM; (vii) las semillas de reproducibilidad;
(viii) las constantes finales *locked* del atacante refinado; y (ix)
el protocolo de validación externa sobre la testnet Sepolia.
El objetivo es que un lector independiente pueda regenerar cualquier
tabla o figura del Capítulo 8 con un solo comando desde el repositorio
del proyecto.

## 9.1 Datasets

Se emplean cuatro *datasets* complementarios: uno propio (simulación
adversarial multi-agente) y tres externos (uno real, dos benchmarks
académicos).

### 9.1.1 Simulación propia — `dataset.pkl`

El *dataset* principal se genera mediante el simulador ofensivo
descrito en el Capítulo 6, ejecutado sobre una copia local de la red
Ethereum (Foundry/Anvil). Cada corrida instancia el pipeline extremo-a-
extremo: despliegue de los seis contratos mock, arranque del
coordinador Opus 4.7, ejecución de una campaña completa por los
sub-agentes Sonnet 4.6, y captura del grafo de transacciones
resultante.

El *dataset* combinado (`~/aml-results/batch_2026-06-26/dataset.pkl`,
254 MB) integra 420 corridas: 20 campañas atacantes distribuidas
uniformemente entre tres escenarios (*defi-exploit*, *stablecoin-scam*
y *ransomware-cashout*, PRs 48-49 del repositorio) y 400 campañas
benignas (semillas 200-599), estas últimas ejecutadas por el módulo
`src/aml/detectors/run_benign.py` que simula tráfico de exchanges y
usuarios legítimos con las mismas herramientas del catálogo pero sin
el objetivo de blanqueo. La distribución de clases es realista: 649
direcciones adversariales (6,3 %) sobre un total de 10 311 nodos y
101 880 aristas, alineada con el rango de prevalencia AML reportado
por FATF para el ecosistema cripto (< 10 %).

El *ground truth* se estructura en dos niveles: (i) etiqueta binaria
y_v ∈ {0, 1} por dirección; (ii) partición latente de las 649
direcciones adversariales en 20 *actor clusters* (uno por campaña,
con tamaño heterogéneo entre 9 y 83 direcciones), más 9 584 *actor
singletons* correspondientes a wallets benignas. Esta doble
estructura permite evaluar separadamente la detección binaria local
(F1) y la atribución de actor cluster cross-exchange (ARI).

### 9.1.2 EthereumHeist (Wu et al. 2023)

**EthereumHeist** es el *dataset* real de referencia utilizado para
validación externa. Contiene 23 casos reales de hackeos y robos
sobre Ethereum mainnet, propagados forensicamente desde direcciones
semilla identificadas: 633 057 nodos, 2 452 786 aristas. El adaptador
`scripts/load_ethereum_heist.py` fusiona las 23 carpetas de hack en
un único `MultiDiGraph` y produce el *pickle* combinado
`data/ethereum_heist_combined.pkl` (285 MB).

Cuatro de los 23 hackeos son marcadamente asimétricos en escala
—UpbitHack (263 k nodos), PlusTokenPonzi (155 k), AscendEXHacker
(85 k), BitpointHacker (44 k)— y dominarían cualquier evaluación
combinada. Los experimentos del Capítulo 8 reportan tanto la
configuración completa como la configuración `--exclude-big-hacks`
(19 hackeos, 4 796 nodos, 48 408 aristas) que produce un problema
más equilibrado y tractable para CPU. Los dos regímenes se reportan
por separado.

### 9.1.3 Elliptic++ (Elmougy y Liu 2023)

**Elliptic++** es la extensión a nivel de wallet del *dataset* Elliptic
original (Weber et al. 2019) sobre Bitcoin. Contiene 822 942 wallets
etiquetadas como *lícito* / *ilícito* / *desconocido*, con features
propagadas del grafo de transacciones original. Este trabajo utiliza
un subconjunto de 50 000 wallets etiquetadas (excluyendo *desconocido*)
para la validación auxiliar del clasificador GCN local. Aunque el
*ledger* subyacente es Bitcoin (no Ethereum), la evaluación funciona
como sanity check del clasificador aislado del pipeline específico
Ethereum.

**Qué entra al modelo GCN a partir del *dataset* propio**. La
pipeline `combine_runs` → `partial_visibility_split` →
`extract_features` produce, para cada corrida ejecutada, tres
vistas {G_A, G_B, G_C} del *dataset* combinado. Para cada
vista, el clasificador GCN local recibe: (i) la matriz de features
X ∈ ℝ^|V_i| × 19 (los 19 features
descritos en §6.3 computados sobre el subgrafo G_i); (ii) las
aristas del subgrafo como *edge_index* de PyTorch Geometric;
(iii) el vector de etiquetas binarias y_v ∈ {0, 1} sólo para
las direcciones asignadas al exchange X_i (las contrapartes
compartidas aparecen en X pero con etiqueta *unknown*, enmascarada
en la loss). El GCN produce como salida un vector de probabilidades
p(v) ∈ [0, 1]^|V_i| por dirección visible; el top-K por
probabilidad descendente (K = 60 por defecto) se propaga al
coordinador cross-exchange de la capa 2 con las 19 features en
crudo como *fingerprint* per-address. **La capa 2 nunca recibe
grafos crudos**: el input al coordinador LLM es exclusivamente la
lista de direcciones flageadas + sus fingerprints en representación
textual.

### 9.1.4 OpenAML v1 (proyecto FINOS/DTCC 2025)

**OpenAML v1** es el marco open-source más reciente sobre Ethereum,
mantenido por FINOS (Fintech Open Source Foundation, Linux Foundation)
tras nacer como proyecto del *DTCC AI Hackathon* en la Duke University.
Se usa el archivo `training_data.csv` distribuido con la versión 1 del
repositorio, con 34 000 wallets etiquetadas y 16 features por wallet
(in-degree, out-degree, ETH recibido, USDT recibido, número de
contrapartes únicas, entre otras). Este *dataset* es el más cercano en
formato al output del pipeline propio y se usa para la triple-validación
cruzada.

**Nota sobre StableAML v2**. La configuración inicial contemplaba el
uso del dataset ampliado "StableAML v2" mencionado en la documentación
del proyecto OpenAML como *dataset* principal externo, pero el CSV
correspondiente no está distribuido en el repositorio público (*issues*
abiertas hace 11 meses sin respuesta al momento de la redacción). La
estrategia final locked el 2026-07-18 sustituye StableAML v2 por la
combinación Elliptic++ + OpenAML v1 + simulación propia, que provee
triple validación con provenance verificable.

## 9.2 Particionado federado en exchanges

Los cuatro *datasets* se someten al mismo particionado
`partial_visibility_split` (definido en `src/aml/detectors/dataset.py`,
línea 200-380) con parámetros fijos:

- **Número de exchanges**: n=3.
- **Nombres**: `exchange_A`, `exchange_B`, `exchange_C`.
- **Semilla**: 42 (determinista y reportada explícitamente en todas las
  ejecuciones).
- **Distribución**: hash-based, aproximadamente uniforme
  (± 3 % entre exchanges).

El algoritmo asigna cada dirección v ∈ V a exactamente un
exchange mediante `hash(str(v)) % 3` con la semilla determinada, y
construye para cada X_i el subgrafo inducido por las aristas
incidentes en direcciones de X_i. Las etiquetas KYC (y_v) sólo son
accesibles al entrenamiento del clasificador local f_i si
v ∈ X_i; para el resto de direcciones que aparecen en el subgrafo
de X_i como contrapartes, la etiqueta es *unknown*.

La elección de n=3 balancea dos consideraciones: (i) n=1 colapsa
al escenario centralizado no federado; (ii) n ≥ 10 produce
subgrafos demasiado ralos para entrenar el GCN local. Con n=3 cada
exchange observa aproximadamente el 33 % de las direcciones y ~55 %
de las aristas (las aristas con ambos endpoints en X_i más las que
cruzan hacia otras plataformas).

## 9.3 Configuración de detectores

Se comparan cuatro detectores.

### 9.3.1 Louvain (baseline no supervisado)

Implementado en `src/aml/detectors/baselines.py` mediante
`networkx.algorithms.community.louvain_communities`. Sin entrenamiento
supervisado: predice como *ilícita* toda dirección que caiga en una
comunidad cuyo tamaño exceda el percentil 90 de la distribución de
comunidades por exchange. Se incluye como *baseline* de referencia
histórica —Louvain era el algoritmo dominante en detección AML
pre-2020— y como estrawman para justificar el coste computacional
del GCN.

### 9.3.2 GCN (Kipf y Welling 2017)

Clasificador de convolución sobre grafos implementado en
`src/aml/detectors/gnn.py` (359 líneas) con PyTorch Geometric.
Configuración por defecto (dataclass `GCNConfig`, líneas 213-220):

- **Arquitectura**: dos capas `GCNConv` con 32 unidades ocultas y
  activación ReLU intermedia, capa lineal final a un logit.
- **Features de nodo** (d=19, definidas en
  `src/aml/detectors/gnn.py`, `FEATURE_NAMES`):
  (i) *degree features* (3): `in_degree`, `out_degree`, `total_degree`;
  (ii) *flujos de valor* log-transformados (4): `log_eth_in`,
  `log_eth_out`, `log_usdt_in`, `log_usdt_out`;
  (iii) *diversidad de contrapartes* log-transformadas (2):
  `log_unique_in`, `log_unique_out`;
  (iv) *conteo por tipo de arista* (10 = 5 tipos × 2 direcciones):
  `transfer_eth_{in,out}`, `transfer_usdt_{in,out}`, `swap_{in,out}`,
  `mixer_deposit_{in,out}`, `mixer_withdraw_{in,out}`. La transformación
  logarítmica en (ii) y (iii) comprime distribuciones que pueden
  abarcar varios órdenes de magnitud entre nodos benignos aislados y
  nodos hub de laundering. La justificación semántica del diseño se detalla en §6.3.
- **Dropout**: 0,1 tras cada capa oculta.
- **Optimizador**: Adam con *learning rate* 10⁻² (0,01), *weight
  decay* 5 · 10⁻⁴.
- **Épocas**: 100 por defecto en la *dataclass*; los *scripts* de
  evaluación pasan 50-100 según el *dataset* (validado empíricamente
  como suficiente para convergencia sin sobreajuste).
- **Loss**: `binary_cross_entropy_with_logits` con
  `pos_weight = n_negatives / n_positives` para compensar el
  desbalance de clase.
- **Semilla**: 42 (afecta inicialización de pesos y orden de mini-
  batches).

Se instancia una copia independiente del clasificador por exchange:
f_A, f_B, f_C no comparten pesos y se entrenan por separado
sobre sus vistas parciales.

### 9.3.3 MultiAgent baseline (similaridad coseno)

Implementado en `src/aml/detectors/multi_agent.py` como
`MultiAgentDetector` (línea 124). La clase encapsula la capa 2 del
defensor con una implementación no-LLM: recibe las direcciones
flageadas por cada *exchange*, extrae el vector de *features* de
19 dimensiones por dirección y agrupa dos direcciones v, w en
el mismo *cluster* si cos(x_v, x_w) ≥ 0,95
(constante `similarity_threshold`, línea 144). Es el *strawman*
comparativo obligatorio para el detector LLM.

### 9.3.4 MultiAgent LLM (coordinador Claude)

`LLMDefenderCoordinator` (`src/aml/detectors/multi_agent.py`,
línea 553; 780 líneas totales del módulo). Sustituye la capa 2
por una llamada estructurada a un LLM. Parámetros *locked*:

- **`top_k_flagged_per_exchange = 60`**: número máximo de direcciones
  flageadas que se envían al LLM por cada exchange. Selección por
  probabilidad descendente del clasificador local.
- **`llm_max_tokens = 8192`**: cap de output para permitir
  clusterings verbosos.
- **`fallback_similarity_threshold = 0.95`**: umbral de similaridad
  coseno aplicado si el parser JSON falla (idéntico al baseline).

Los modelos evaluados son:

- **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`): opción bulk
  económica, ~0,25/1,25 USD por millón de tokens
  input/output.
- **Claude Sonnet 4.6**: opción headline balanced,
  ~3/15 USD por millón.
- **Claude Opus 4.7** (`claude-opus-4-7`): opción qualitative demo,
  ~15/75 USD por millón.

Los tres modelos se ejecutan sobre exactamente el mismo prompt
(`_LLM_COORDINATOR_SYSTEM_PROMPT` en `multi_agent.py`) para permitir
comparativa directa modelo-a-modelo.

## 9.4 Métricas

### 9.4.1 Detección binaria local

- **F1** = harmonic mean de precisión y recall sobre la etiqueta
  binaria ŷ_v ∈ {0, 1} agregada a través de los tres
  exchanges. Métrica primaria de la capa 1 del defensor.

### 9.4.2 Atribución de actor cluster cross-exchange

- **Adjusted Rand Index (ARI)** (Hubert y Arabie 1985): mide el
  acuerdo entre el clustering predicho Ĉ y el clustering
  verdadero C*, corregido por acuerdo esperado bajo azar.
  Rango [-1, 1]; 0 corresponde a agrupación aleatoria, 1 a
  identidad. Métrica primaria de la capa 2 del defensor.
- **Homogeneidad** (Rosenberg y Hirschberg 2007): fracción de
  clusters ĉ ∈ Ĉ tales que todas las direcciones de
  ĉ pertenecen al mismo cluster real. Alta homogeneidad indica
  pureza de los clusters predichos.
- **Completitud**: fracción de clusters reales c ∈ C* tales
  que todas las direcciones de c caen en un único cluster predicho.
  Alta completitud indica ausencia de fragmentación de actores reales.

### 9.4.3 Métricas de coste

Para los detectores LLM se reporta adicionalmente:

- **Coste en USD**: suma de `input_tokens × precio_input +
  output_tokens × precio_output` sobre todas las llamadas de la
  evaluación. Devuelto directamente por
  `aml.attackers.llm_client.LLMClient.complete()`.
- **Tokens totales**: separados en `input_tokens` (contexto + prompt)
  y `output_tokens` (respuesta del LLM).
- **Tiempo wall-clock**: latencia total de la evaluación.

## 9.5 Configuración LLM

### 9.5.1 Prompts

Los prompts del coordinador defensor están calibrados para producir
entre 10 y 25 clusters (rango realista para las 20 campañas
atacantes esperadas más *outliers* benignos). El *system prompt*
enfatiza:

1. Nombrado de clusters con arquetipos AML reconocibles
   (*cross-exchange mixer hub*, *pass-through mixer relay*,
   *pure mixer depositor*, *fan-out distributor*,
   *consolidation sink*, *peeling chain*).
2. Formato de output JSON compacto
   `{"cluster_id": ["addr1", "addr2", ...]}`.
3. Restricción explícita de usar únicamente las direcciones
   proporcionadas en el input (evita hallucinations).
4. Sección de reasoning textual que explique cada cluster.

El *user prompt* concatena las tres vistas locales como bloques
independientes, cada uno con la lista completa de direcciones
flageadas del exchange y sus features (una línea por dirección con
formato `0x<full_42_char_address>: {feature_summary}`). Las
direcciones se envían **sin truncar** —una versión temprana que
enviaba direcciones abreviadas causó que el LLM devolviese versiones
truncadas incompatibles con el parser.

### 9.5.2 Parser tolerante

El parser `_parse_llm_clusters` (línea 490 de `multi_agent.py`) acepta
dos formatos alternativos:

1. **Compacto** (formato preferido): `{cluster_id: [addresses]}`.
2. **Verbose**: `[{cluster_id, addresses, reasoning}]` (formato
   histórico de las primeras iteraciones, soportado por
   backward-compat).

Si el LLM devuelve un JSON malformado o asigna direcciones no
presentes en el input, el parser aplica el *fallback* de similaridad
coseno con umbral 0,95 (idéntico al baseline). El campo
`used_fallback` del *result* se reporta explícitamente en cada
ejecución.

## 9.6 Configuración final del atacante (constantes locked)

Los resultados canónicos del Capítulo 8 (§8.9.5, seeds 400 y 403) se
producen con los siguientes valores fijos, congelados tras la
iteración de refinamiento documentada en §8.9.5:

| Constante                             | Valor                                     | Localización                                             |
|---------------------------------------|-------------------------------------------|----------------------------------------------------------|
| `_DEFAULT_GAS_RESERVE_ETH`            | 0,005 ETH                             | `attackers/tools.py`                                     |
| `_pick_funder threshold`              | 0,01 ETH                              | `attackers/tools.py`                                     |
| `_pick_funder refill target`          | 0,025 ETH                             | `attackers/tools.py`                                     |
| `MAX_PER_FUNDER_ETH`                  | 1,0 ETH                               | `attackers/funder_sizing.py`                             |
| `MIN_PER_FUNDER_ETH`                  | 0,02 ETH                              | `attackers/funder_sizing.py`                             |
| `POOL_PCT_OF_AMOUNT`                  | 0,05 (5 % del stolen)             | `attackers/funder_sizing.py`                             |
| `POOL_BOOTSTRAP_ETH_WEI`              | 5 000 ETH (mock pool AMM)              | `chains/eth_stack.py`                                    |
| `POOL_BOOTSTRAP_USDT_BASE`            | 10⁷ USDT (mock pool AMM)               | `chains/eth_stack.py`                                    |
| Cap dinámico *burners*                | max(30, min(250, 3·⌈USD/999⌉))               | `attackers/tools.py::_burner_cap`                    |
| Deployer address (guardarraíl)        | `0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4` | `attackers/tools.py::_deployer_addr`               |
| Umbral sub-Travel-Rule *structuring*  | < 999 USD por *clean exit*                   | *prompt* `INTEGRATION_SYSTEM`                        |
| Precio ETH/USDT fecha campaña         | Congelado por `resolve_campaign_ts`       | `env/market_context.py`                                  |

La derivación empírica de estos valores se detalla en §8.9.5. La
motivación de tres de las constantes merece resaltarse:

- **Cap dinámico de burners** (max(30, min(250, 3·⌈USD/999⌉))). La fórmula garantiza al menos 30
  burners (flexibilidad táctica en campañas pequeñas), no más de 250
  (evita bucles patológicos observados en seed 306 con 236 burners
  para 1 ETH), y crece proporcional al volumen esperado bajo el umbral *structuring* de 999 USD por *clean exit*
  (sub-Travel-Rule crypto: FATF R.16 establece el umbral en
  1 000 USD para transferencias de criptoactivos, más restrictivo
  que el CTR de 10 000 USD del BSA estadounidense).
- **Pool operativo del 5 %** del *amount* laundered. Alinea con el
  perfil *moderate professional* de Chainalysis 2023 (250-750 USD
  por wallet en promedio); una fracción mayor produce campañas
  computacionalmente costosas y forense-obvias, una menor deja la
  campaña sin capital para consumir gas en múltiples hops.
- **Deployer guardrail**. Excluir la dirección deployer del *pool*
  operativo previene la contaminación del denominador de las métricas
  de recuperación (versión pre-fix: 198 % nominal por incluir el
  balance del deployer al final).

---

## 5.E Planificación del proyecto y metodología de trabajo

### 5.E.1 Metodología aplicada

El desarrollo del TFM siguió una **metodología iterativa incremental**
adaptada al perfil experimental del proyecto. Cada iteración cierra
con validación empírica en Anvil (sandbox local, cost cero) antes de
promoción a Sepolia (testnet pública, cost real ETH testnet). Este
patrón Anvil-dev → Sepolia-prod permite iteración rápida en la fase
de exploración y garantiza validez externa en la fase final.

Las cinco fases del proyecto se ejecutaron secuencialmente con
solapamiento parcial en las fases 2-3 (implementación atacante) y
4-5 (implementación defensor). Los hitos entregables (contratos
verificados, datasets consolidados, ablations documentadas)
delimitan cada fase.

### 5.E.2 Fases del proyecto y recursos

| Fase | Descripción | Duración estimada | Duración real | Recursos empleados |
|------|-------------|------------------:|--------------:|--------------------|
| **F1 — Análisis y diseño** | Revisión bibliográfica, taxonomía FATF, diseño arquitectura dual multi-agente | 3 semanas | 3 semanas | 45 h autor + 5 h tutor |
| **F2 — Simulador ofensivo** | Contratos Solidity, herramientas on-chain, agente coordinador atacante | 5 semanas | 6 semanas | 90 h autor + $8 USD LLM Opus |
| **F3 — Validación Anvil-dev** | 15 campañas Anvil (seeds 500-606) con iteraciones P1-XX bug fixes y refinamientos | 3 semanas | 4 semanas | 60 h autor + $6 USD LLM |
| **F4 — Despliegue Sepolia** | 6 contratos verificados en Etherscan + 8 campañas oficiales (seeds 800-830) | 3 semanas | 3 semanas | 45 h autor + $9 USD LLM + 8 ETH testnet |
| **F5 — Defensor + ablations** | Pipeline defensivo Louvain + LLM + ablations (P1-55/56/70/71/72/73/74) + cross-domain + held-out + LOCO | 4 semanas | 4 semanas | 60 h autor + $2 USD LLM Haiku |
| **F6 — Redacción memoria + preparación defensa** | Draft TFM + docx + defensa | 3 semanas | 2 semanas | 45 h autor |
| **TOTAL** | | **21 semanas** | **22 semanas** | **~345 h autor + $25 USD LLM** |

### 5.E.3 Diagrama Gantt textual

```
Semana         1   2   3   4   5   6   7   8   9   10  11  12  13  14  15  16  17  18  19  20  21  22
F1 Análisis    ████████████                                                                        
F2 Atacante             ████████████████████████                                                   
F3 Anvil dev                            ████████████████████                                       
F4 Sepolia                                          ████████████                                   
F5 Defensor                                                      ████████████████                   
F6 Redacción                                                                     ████████████     
```

### 5.E.4 Recursos y hardware empleados

**Recursos humanos**: 1 estudiante autor (Saleh Sinawi), dedicación
estimada 180 h presenciales + no-presenciales conforme al plan de
estudios UC3M (6 ECTS × 30 h/ECTS = 180 h). Real: ~345 h por el
alcance experimental extendido — el excedente sobre lo previsto es
parte del aprendizaje personal del autor y no se contabiliza en el
presupuesto oficial.

**Recursos técnicos**:

- Portátil de desarrollo: Intel Core i7-11800H, 32 GB RAM DDR4,
  NVIDIA RTX 3060 6 GB, WSL2 Ubuntu 22.04.
- Foundry (Anvil + Forge) para sandbox on-chain local.
- Sepolia testnet (Ethereum). Wallet deployer:
  `0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4`.
- API Anthropic Claude (Opus 4.7 atacante, Sonnet 4.6 defensor
  headline, Haiku 4.5 defensor bulk).
- CoinGecko API (precios ETH/USDT diarios en cache).
- Alchemy + Infura RPC providers (Sepolia).
- GitHub (control de versiones + repo público MIT).

### 5.E.5 Plan de gestión de riesgos

| Riesgo | Probabilidad | Impacto | Mitigación aplicada |
|--------|:------------:|:-------:|---------------------|
| LLM API rate-limit o timeout durante campaña larga | Media | Alto | P1-67 timeout+retry en llm_client.py; máx 300s read + 5 retries |
| Sepolia faucet drain / no fondos ETH testnet | Media | Medio | Migración a Anvil para escenarios de scale; Sepolia sólo para validación externa |
| Bug crítico en contratos con ETH bloqueado | Baja | Alto | 47 tests unitarios de contratos + mixer_recover.py como red de rescate |
| LLM produce campañas no realistas | Media | Medio | P1-42/43/44 iteraciones de refinamiento con métricas F1 vs. baseline |
| Overfitting del defensor a seeds de dev | Media | Alto | LOCO-CV (§8.10) + held-out validation seeds 900/901 (§8.9.J) |
| Budget LLM excedido | Baja | Medio | Pivote a Haiku 4.5 en fase F5; cost tracking en cada eval |

## 5.F Presupuesto del proyecto

### 5.F.1 Coste de personal

Considerando la dedicación real del autor (~345 h) y usando la tarifa
horaria estándar para un ingeniero informático junior en España
(sueldo bruto anual medio ~28 000 EUR + 30 % costes sociales ~= 21 EUR/h
sobre 1 750 h anuales efectivas):

| Concepto | Horas | Tarifa | Coste |
|----------|------:|-------:|------:|
| Autor (estudiante ingeniero jr.) | 345 h | 21 EUR/h | **7 245 EUR** |
| Tutor UC3M (co-supervisión) | 5 h | 65 EUR/h | 325 EUR |
| **Subtotal personal** |  |  | **7 570 EUR** |

### 5.F.2 Coste de hardware (amortización)

Amortización lineal a 5 años sobre el periodo de 5 meses del TFM:

| Recurso | Precio | Uso | Amortización |
|---------|-------:|-----|-------------:|
| Portátil Intel i7-11800H 32 GB RTX 3060 | 1 500 EUR | 5 meses / 60 | **125 EUR** |
| Monitor externo 27" | 250 EUR | 5 meses / 60 | 21 EUR |
| **Subtotal hardware** |  |  | **146 EUR** |

### 5.F.3 Coste de servicios cloud y APIs

| Servicio | Uso | Coste real |
|----------|-----|-----------:|
| Anthropic API (Opus 4.7 atacante, ~13 campañas oficiales) | 26 campañas × ~$1 USD | 25 USD |
| Anthropic API (defensor Haiku + Sonnet ablations) | 15 evals × ~$0.15 USD | ~2 USD |
| CoinGecko API (free tier) | 5 meses | 0 USD |
| Alchemy / Infura RPC (free tier Sepolia) | 5 meses | 0 USD |
| GitHub (repo público) | 5 meses | 0 USD |
| **Subtotal servicios** (~$27 USD ≈ 25 EUR al cambio) |  | **25 EUR** |

### 5.F.4 Coste de electricidad y overhead

| Concepto | Cálculo | Coste |
|----------|---------|------:|
| Electricidad (300W × 345h × 0.20 EUR/kWh) | 20.7 kWh × 0.20 | **21 EUR** |
| Conectividad internet (5 meses × 40 EUR) | 200 EUR × 30% imputable | 60 EUR |
| **Subtotal overhead** |  | **81 EUR** |

### 5.F.5 Presupuesto total

| Concepto | Coste (EUR) |
|----------|------------:|
| Personal | 7 570 |
| Hardware (amortización) | 146 |
| Servicios cloud + APIs | 25 |
| Electricidad + overhead | 81 |
| **Subtotal directo** | **7 822** |
| IVA (21 %) | 1 643 |
| **TOTAL con IVA** | **9 465 EUR** |

**Nota sobre coste real vs. presupuesto**: el coste directo de los
recursos técnicos (hardware amortizado + APIs + electricidad) fue de
**252 EUR** — una fracción muy pequeña del total. El grueso del coste
(7 570 EUR, 97 %) corresponde a las horas de trabajo del autor. En
un contexto académico donde este coste no se factura, el **presupuesto
efectivo desembolsado** para completar el TFM fue de aproximadamente
**252 EUR**, lo cual constituye uno de los findings publishable del
proyecto: un pipeline AML multi-agente reproducible con budget
< 300 EUR es viable en 2026 gracias a la disponibilidad de LLMs
frontera a coste marginal.


