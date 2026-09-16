# Anexos

Los anexos documentan los artefactos técnicos referenciados en los
capítulos anteriores. Su función es hacer el trabajo verificable e
independientemente reproducible por un tercero. El código íntegro
está publicado bajo licencia MIT en
[`github.com/0xAnonsal/aml-thesis`](https://github.com/0xAnonsal/aml-thesis).

Los prompts del atacante y del defensor se reproducen aquí en su
forma estructural (bloques y guardarraíles) más que como texto
íntegro; el texto completo reside en los archivos referenciados y
está sujeto a versionado en Git. Cualquier cambio material a los
prompts se acompaña de una entrada en el `CHANGELOG.md` del
repositorio.

## Anexo A — Prompts del atacante multi-agente

Archivo: `src/aml/attackers/prompts.py` (422 líneas). Contiene cuatro
*system prompts* (Coordinador y tres especialistas FATF) más tres
bloques composicionales que se interpolan en los cuatro roles para
mantener consistencia semántica.

### A.1 Bloques composicionales

**`_RESEARCH_FRAMING`**. Encabezado ético común a los cuatro roles:
declara el contexto de simulación controlada, la ausencia de fondos
reales, y el objetivo AML defensivo. Este bloque es lo que legítima
la generación de comportamiento adversarial —sin él, los modelos
Claude aplicarían sus políticas de rechazo estándar a peticiones de
laundering.

**`_GAS_DISCIPLINE`**. Restricciones operativas sobre gestión de gas:
piso mínimo de 0,005 ETH reservado por burner (refinado de los
0,05 ETH iniciales tras las corridas seed 304-307), uso de
`get_gas_budget` para verificar capacidad transaccional restante,
semántica *drain-only-at-end* que sólo permite vaciar el gas dust al
final de la campaña.

**`_MARKET_CONTEXT`**. Bloque inyectado dinámicamente por el runner
con el precio ETH/USDT/TRX del oráculo determinista
(`env/market_context.py`) fijado a la fecha de campaña. Permite al
agente razonar en unidades USD (natural para el umbral CTR de 999
USD) sin re-calibrar prompt entre corridas con precios diferentes.

**`_BURNER_MODERATION`** (post-fix A+B-lite, §8.9.5). Instrucción
explícita al Layering specialist para que solicite *"MODERATE aids"*
en vez de la formulación anterior *"OVER-PROVISIONING WELL BEYOND
REQUIRED"*, que en seed 306 produjo el bucle patológico de 236
burners para 1 ETH. Complementa el *hard cap* del *dispatcher*
descrito en §3.3.

**`_DETECTOR_EVASION`**. Instrucciones tácticas de evasión adversarial.
Nombra explícitamente los tres detectores contra los que corre la
campaña (Louvain, GCN estilo Weber, MultiAgent colaborativo) y
enumera las señales que cada detector explota. Instrucción central:
"*be deliberately heterogeneous*" —varía topologías, monta amounts,
mezcla tipos de transacción— porque un pipeline laundering
internamente uniforme es trivialmente aprendible.

### A.2 Coordinator (`COORDINATOR_SYSTEM`)

Instanciado sobre Claude Opus 4.7. Rol de estratega puro: no ejecuta
transacciones directamente. Dispone de cuatro herramientas:

- `delegate_to_placement`: FATF stage 1.
- `delegate_to_layering`: FATF stage 2.
- `delegate_to_integration`: FATF stage 3.
- `inspect_chain`: audit read-only.

Cada delegación pasa a un sub-agente un `objective` (qué lograr) y un
`context` (todo el estado necesario: direcciones, montos, notas del
mezclador, hechos cargados desde fases previas). El coordinador
mantiene el hilo entre fases y decide cuándo re-delegar si un
sub-agente reporta *partial* o *failed*.

Responsabilidad crítica del coordinador: **planificación de clean
exits**. Debe calcular el número de wallets finales de destino
(*clean exits*) que la fase Integration debe crear, respetando la
restricción CTR (< 999 USD por exit) y una dispersión realista de
2-5 plataformas.

### A.3 Placement (`PLACEMENT_SYSTEM`)

Instanciado sobre Claude Sonnet 4.6. Función: introducir los fondos
en el sistema. Operaciones típicas: `mint_usdt`, `transfer_usdt` a
las primeras direcciones intermedias, generación de burners iniciales.
El sub-agente Placement no ve la conversación del Coordinador ni la
de sus pares —opera aislado sobre su `objective` y `context`.

### A.4 Layering (`LAYERING_SYSTEM`)

Instanciado sobre Claude Sonnet 4.6. Es el sub-agente que consume el
80 % de las herramientas del catálogo. Ejecuta las técnicas de
ofuscación: smurfing (`smurf_split`, `smurf_eth_split`), peel chains
(`peel_chain`), swaps (`swap_eth_for_usdt`, `swap_usdt_for_eth`),
depósitos y retiradas en el mezclador (`mixer_deposit`,
`mixer_withdraw`). El contrato `MockBridge` está desplegado
(Sepolia D.1) pero fuera del alcance del catálogo de herramientas
del atacante: no se expone como *tool* al LLM porque el *bridge*
real requeriría integración *cross-chain* que queda como trabajo
futuro (§10.4.4).

El prompt Layering incluye un catálogo priorizado de tácticas por
tamaño de flow: para movimientos ≥ 1 ETH prefiere ciclos
mezclador; para movimientos < 1 ETH prefiere structuring
multi-hop.

### A.5 Integration (`INTEGRATION_SYSTEM`)

Instanciado sobre Claude Sonnet 4.6. Función: consolidar los fondos
en las direcciones limpias de destino que el propio sub-agente crea
mediante `register_clean_exit`. Restricción principal: cada *exit*
debe recibir < 999 USD para permanecer por debajo del umbral
sub-Travel-Rule crypto (Recomendación FATF 16 fija 1 000 USD para
transferencias de criptoactivos; el CTR estadounidense clásico de
10 000 USD del BSA es el orden de magnitud superior y no aplica
aquí). El nombrado de exits sigue el patrón
`<platform>_<n>` (por ejemplo `binance_3`, `coinbase_1`) para
facilitar la trazabilidad forense posterior.

## Anexo B — Prompt del defensor LLM Coordinator

Archivo: `src/aml/detectors/multi_agent.py` líneas 396-432. Un único
*system prompt* de ~600 palabras, instanciado sobre Claude Sonnet 4.6
para las evaluaciones headline y sobre Haiku 4.5 para las bulk. El
prompt establece cuatro elementos:

1. **Rol y task**. "You are an anti-money-laundering (AML) coordinator
   for a federation of cryptocurrency exchanges. Your task: identify
   distinct campaigns / real-world actors".
2. **Estructura del input**. Por dirección: qué exchange la flageó, un
   fingerprint de 19 dimensiones, el score de confianza local.
3. **Guías estructurales**. Rango objetivo 10-25 clusters. Cada
   campaña típica produce 5-30 direcciones. Preferir muchos clusters
   medianos (5-15 direcciones cada uno) sobre pocos arquetipos
   conductuales enormes. Explicitamente NO agrupar todos los
   *mixer users* en un cluster ni todos los *swap-heavy* en otro —
   son patrones conductuales, no identidades de actor.
4. **Formato de output**. JSON compacto
   `{"cluster_id": [addresses]}` más una sección
   `overall_reasoning` con la justificación textual.

El formato JSON compacto (en vez de la versión verbose por-cluster
que era el default temprano) reduce el consumo de output tokens en un
factor de ~3× y permite procesar batches de 180+ direcciones con
`max_tokens = 8192`.

**Parser tolerante** (`_parse_llm_clusters`, línea 490): acepta
tanto el formato compacto como la versión verbose por retro-
compatibilidad, y aplica *fallback* a similaridad coseno con umbral
0,95 si el JSON está malformado o si el LLM asigna direcciones no
presentes en el input.

## Anexo C — Catálogo íntegro de herramientas on-chain

Archivo: `src/aml/attackers/tools.py` (3 441 líneas). Contiene la
constante `_TOOL_SCHEMAS` (línea 135) con las **diecinueve herramientas**
disponibles al atacante. Cada herramienta se declara al LLM como una
función con nombre, descripción y esquema JSON de parámetros. El
*dispatcher* aplica los cinco *invariants* estructurales descritos en §7.4 (guardarraíl deployer, cap dinámico de burners, rotación de
funders, gas reserve, *sweep* final) transversalmente a todas las
herramientas.

### C.1 Introspección (gasto 0 gas)

- `get_balance(address, asset)`: consulta el balance de una dirección
  en ETH o USDT.
- `get_gas_budget(address)`: estima cuántas transacciones más puede
  pagar la *wallet* antes de agotar el piso de gas (0,005 ETH,
  constante `_DEFAULT_GAS_RESERVE_ETH`).
- `get_swap_quote(asset_in, amount_in, asset_out)`: cotización sin
  ejecución para calcular *slippage* antes de comprometerse.
- `inspect_chain(since_block)`: audit read-only. Devuelve balances de
  wallets registradas, contadores de eventos por tipo (`transfer`,
  `swap`, `mixer_deposit`, `mixer_withdraw`, `bridge_lock`) desde el
  bloque referenciado, y señales relevantes para el detector
  (uniformidad de balances entre burners, ratio flow ETH/USDT).

### C.2 Transferencia básica

- `transfer_usdt(from_wallet, to_address, amount_usdt)`: transferencia
  ERC-20 estándar.
- `transfer_eth(from_wallet, to_address, amount_eth, reserve_eth)`:
  transferencia nativa con parámetro opcional `reserve_eth` para
  respetar el piso de gas.
- `mint_usdt(to_address, amount_usdt)`: creación permissionless de
  USDT mock. Usado en la fase Placement para siembra inicial.

### C.3 Wallet management

- `generate_burner_wallet(label)`: crea una nueva EOA con clave
  aleatoria, la fondea con *gas dust* desde el *funder pool* (piso
  0,005 ETH reservado en `_DEFAULT_GAS_RESERVE_ETH`) y la registra
  con la etiqueta forense proporcionada.
- `register_clean_exit(platform, index)`: genera y registra una
  dirección de destino final etiquetada. Usada exclusivamente por
  Integration en la fase 3 FATF.

### C.4 Primitivas de layering

- `smurf_split(from_wallet, recipients_and_amounts_usdt)`: batched.
  Ejecuta hasta N transferencias USDT desde un solo emisor en una
  única llamada LLM, reduciendo el consumo de contexto por
  transacción en un orden de magnitud.
- `smurf_eth_split(from_wallet, recipients_and_amounts_eth,
  reserve_eth)`: análogo para ETH nativo.
- `peel_chain(source_wallet, hop_amounts_usdt, final_recipient)`:
  ejecuta una cadena de peeling clásica —cada hop conserva una
  fracción y forwarda el resto— en una única llamada, generando las
  direcciones intermedias sobre la marcha.
- `swap_eth_for_usdt(from_wallet, amount_eth, min_usdt_out)`:
  ejecuta un swap en `MockUniswapV2Pool` con protección contra
  *slippage* excesivo.
- `swap_usdt_for_eth(from_wallet, amount_usdt, min_eth_out)`:
  dirección inversa.
- `advance_blocks(n)`: instrumento de tiempo para simulación. En
  Anvil salta N bloques; en Sepolia no-op.

### C.5 Mixing

- `mixer_deposit(from_wallet)`: ejecuta un depósito de 1 ETH en
  `MockTornado`. Devuelve una `note` (secret + nullifier) que el
  agente debe conservar para la retirada.
- `mixer_withdraw(note, recipient_wallet, fee_wei)`: genera la
  prueba Groth16 off-chain con snarkjs y ejecuta la retirada. Puede
  operar con `note` generada en cualquier turno previo de la
  conversación.
- `mixer_batch_deposit(from_wallet, count, notes_out)`: variante
  *batched* (PR #48) que ejecuta hasta N depósitos secuenciales de
  1 ETH desde un mismo emisor en una sola llamada del LLM.
  Internamente el *dispatcher* itera N veces: (i) genera un
  `secret` y un `nullifier` aleatorios via `secrets.token_bytes(31)`;
  (ii) calcula el commitment MiMC en Python; (iii) firma la
  transacción `deposit(commitment)` con la key de `from_wallet`;
  (iv) espera el *receipt*; (v) acumula el par (`secret`, `nullifier`)
  en la lista `notes_out`. La única llamada LLM produce N tx
  on-chain más N notes conservadas. **Motivación de la variante
  batched**: cada llamada LLM cuesta *input tokens* proporcionales
  al contexto acumulado; encapsular N deposits en un solo *round
  trip* reduce el consumo de tokens en un factor 3-6×
  frente al bucle `mixer_deposit` iterado N veces, sin pérdida
  de granularidad forense.

- `mixer_batch_withdraw(notes, recipients_and_fees)`: análogo para
  retiradas. Recibe una lista de N notes previamente generadas
  (posiblemente en turnos anteriores de la conversación) más una
  lista paralela de (recipient, fee\_wei). Por cada
  entrada el *dispatcher*: (i) reconstruye el árbol Merkle local
  paginando eventos `Deposit` desde `tornado_deploy_block` en
  *chunks* de 500 bloques; (ii) invoca `snarkjs.groth16.prove`
  off-chain (~5 s por prueba) con la path Merkle del leaf
  correspondiente; (iii) firma la transacción `withdraw(proof,
  public_signals, recipient, fee)`; (iv) espera el *receipt* y
  verifica que el nullifier haya sido registrado. La ganancia en
  consumo de tokens es aún mayor que en el deposit batched porque
  el prompt no repite N veces la descripción del formato de la
  prueba Groth16.

### C.6 Helper interno (no expuesto al LLM)

- `sweep_funder_pool(destination)`: método privado del `ToolDispatcher`.
  Invocado por el runner de campaña al final del ciclo Integration
  para consolidar el residual del *pool* de funders (dust ETH sin
  usar) sobre una dirección registrada por el sub-agente Integration.
  No forma parte del catálogo LLM porque su semántica es de cierre
  operativo, no de decisión táctica del agente.

### C.7 Módulo auxiliar `funder_sizing.py`

Archivo: `src/aml/attackers/funder_sizing.py`. Contiene
`allocate_funder_amounts(amount_eth, rng)` que particiona el capital
operativo (5 % del *amount* laundered) sobre 2-10 wallets funder
mediante una distribución tier-based no uniforme calibrada al perfil
*moderate professional* de Chainalysis 2023 (250-750 USD por
wallet). Constantes locked en el módulo:

- `MAX_PER_FUNDER_ETH = 1.0`
- `MIN_PER_FUNDER_ETH = 0.02`
- `POOL_PCT_OF_AMOUNT = 0.05`

## Anexo D — Contratos desplegados en Sepolia

### D.1 Direcciones y enlaces Etherscan

Los seis contratos del sistema fueron desplegados sobre Ethereum Sepolia
el 2026-08-11 desde el deployer
`0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4`. Cada dirección es
inspeccionable en Sepolia Etherscan por cualquier tercero.

| Contrato            | Dirección                                       | Source verificado                                                                                     |
|---------------------|-------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| MockUSDT            | `0x665A2176d7beF3bccE52F37F1e64c99D993Aa7Ba`   | [✅ ver #code](https://sepolia.etherscan.io/address/0x665A2176d7beF3bccE52F37F1e64c99D993Aa7Ba#code) |
| MockUniswapV2Pool   | `0x229cC888FE17c81CD474Ea8eb6F5387f1739a4c4`   | [✅ ver #code](https://sepolia.etherscan.io/address/0x229cC888FE17c81CD474Ea8eb6F5387f1739a4c4#code) |
| MiMCSponge          | `0x615FD17d605325eb05d784Aa8189518FeE5f0Ce6`   | [bytecode only](https://sepolia.etherscan.io/address/0x615FD17d605325eb05d784Aa8189518FeE5f0Ce6) — auto-generado por circomlibjs |
| Verifier (Groth16)  | `0x2FDceD2D3C9c3A9322d04afc1e6A243a98a68f46`   | [✅ ver #code](https://sepolia.etherscan.io/address/0x2FDceD2D3C9c3A9322d04afc1e6A243a98a68f46#code) |
| MockTornado         | `0x199181F8a480A61520Ca3Eb2Ae6BF5dbBc6550C5`   | [✅ ver #code](https://sepolia.etherscan.io/address/0x199181F8a480A61520Ca3Eb2Ae6BF5dbBc6550C5#code) |
| MockBridge          | `0x99DFe4eEAbbA728CE1271d0Ba6a0959883986600`   | [✅ ver #code](https://sepolia.etherscan.io/address/0x99DFe4eEAbbA728CE1271d0Ba6a0959883986600#code) |

### D.2 Hashes de transacción de despliegue

Cada contrato produce una transacción `contractCreation` cuyo `to` es
`null` y cuyo *receipt* incluye la dirección resultante en el campo
`contractAddress`. Los hashes verificables son:

| Contrato          | Nonce | Hash de transacción                                                    |
|-------------------|-------|------------------------------------------------------------------------|
| MockUSDT          | 5     | (visible en el log `results/sepolia_deploy.log` del repositorio)       |
| MockUniswapV2Pool | 6     | (visible en el log `results/sepolia_deploy.log` del repositorio)       |
| MiMCSponge        | 8     | `0x34a5f44d6d855ce30bc59ed563c1730b5a344860cb272cd72382eb0d8be8a2a9` |
| Verifier          | 9     | `0x0eb7342c792bbeb51cd497972b4ae7e91083eac2f6007e313dde798ea5282859` |
| MockTornado       | 10    | `0x094c6f0bc13c75c70bc88462e7e35ed1d99bca84e9cb4750b5a722f8b277f3dd` |
| MockBridge        | 11    | `0x45da12de8b668eefb5bad4dea4c3cb0048f72edf244ab422f3c3be204d7e980f` |

Adicionalmente, la transacción de cancelación (nonce 7,
`0xe86e429ff345e697684ae64359fa80b5d6dbf1f04665260a2c04007186529686`)
documenta el incidente operativo descrito en §8.9.1 (helper MiMC con
gas legacy insuficiente en el primer intento).

### D.3 Fichero `deployments/sepolia.json`

El fichero mantenido bajo control de versiones en
`deployments/sepolia.json` del repositorio contiene la totalidad de la
información canónica del despliegue: las seis direcciones, la marca de
tiempo UTC, las constantes de deployment (profundidad del árbol Merkle,
denominación del mezclador, cantidades del bootstrap del pool), y una
nota explicativa sobre las dos fases del despliegue.

```json
{
  "chain_id": 11155111,
  "chain_name": "sepolia",
  "deployed_at_utc": "2026-08-11T16:24:24Z",
  "contracts": {
    "MockUSDT": "0x665A2176d7beF3bccE52F37F1e64c99D993Aa7Ba",
    "MockUniswapV2Pool": "0x229cC888FE17c81CD474Ea8eb6F5387f1739a4c4",
    "MiMCSponge": "0x615FD17d605325eb05d784Aa8189518FeE5f0Ce6",
    "Verifier": "0x2FDceD2D3C9c3A9322d04afc1e6A243a98a68f46",
    "MockTornado": "0x199181F8a480A61520Ca3Eb2Ae6BF5dbBc6550C5",
    "MockBridge": "0x99DFe4eEAbbA728CE1271d0Ba6a0959883986600"
  },
  "constants": {
    "merkle_depth": 10,
    "tornado_denomination_wei": 1000000000000000000,
    "bootstrap_usdt_micro": 100000000000,
    "bootstrap_eth_wei": 500000000000000000
  }
}
```

### D.4 Coste del despliegue

- **Balance inicial del deployer**: 11,8521 ETH Sepolia (fondeado
  mediante *faucet* público).
- **Balance final tras despliegue**: 11,3148 ETH Sepolia.
- **Coste total**: 0,5373 ETH descompuesto en 0,5000 ETH aportados como
  liquidez permanente al *pool* Uniswap y 0,0373 ETH de gas efectivo
  incluyendo la transacción de cancelación del incidente.
- **Presupuesto restante para las campañas Sepolia**: ~11,3 ETH,
  suficientes para ~11 depósitos completos en el mezclador (denominación
  fija de 1 ETH) más gas de cientos de transacciones ERC-20 y swaps.

### D.5 Hashes de la campaña adversarial

El 2026-08-11 se ejecutó una campaña `defi-exploit` sobre Sepolia
mediante `scripts/run_sepolia_campaign.py --scenario defi-exploit
--amount 1.0 --model sonnet --seed 100 --alice-funding-eth 1.5`. La
campaña generó 35 transacciones on-chain en 90 bloques consecutivos
(11 467 806 → 11 467 896) durante 19 min de *wall-clock*, con coste
LLM de 1,7256 USD. Los *artifacts* completos residen en
`results/sepolia_campaign/2026-08-11T18-02-38_defi-exploit_seed100_sepolia/`
del repositorio (`meta.json`, `campaign.json`, `addresses.json`,
`chain_trace.jsonl` con las 35 tx traceadas + 11 865 tx del tráfico
Sepolia coetáneo).

**Wallets de la campaña**:

| Rol                           | Dirección                                       |
|-------------------------------|-------------------------------------------------|
| Source (alice)                | `0x2628e757b4A3e13E2aC7F0648C0c50c66D0DC649`   |
| Deployer / operator           | `0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4`   |
| Alice funding tx              | `0x5a84b5d420ab0e574e7f171accd381021274f13bb7f7e9d409f72c4e44641536` |
| Burners generados (8)         | (ver `addresses.json` — 8 direcciones)          |

**Clean exits registrados (7 wallets, 4 plataformas)**:

| Dirección                                     | Plataforma | USDT rec. | Nota                     |
|-----------------------------------------------|------------|-----------|--------------------------|
| `0xBC16539C03408D5D77A2F4B3A6Ae5656e1ED7e98`  | Binance    | 565       | primary off-ramp         |
| `0xA804A18c80DA11A19660D506721520b35fC965e8`  | Binance    | 391       | secondary off-ramp       |
| `0x366A254Fa7e9123891b29ac48733A1eD0026cA23`  | Binance    | 278       | tertiary off-ramp        |
| `0x60269c5B856a96ABEE93a36D11E17CC36daeAa11`  | Kraken     | 199       | single-exit platform     |
| `0x69a7cDFfB55B957610F4A77c689cF50418a99cb9`  | OKX        | 132       | single-exit platform     |
| `0xce6Ec24f668Fef4f88dA1F18531C2287f3f505A9`  | Coinbase   | 0         | distractor intencionado  |
| `0x2169CC256354EDaB1e1CFa0dcCA30134823a0cB6`  | Coinbase   | 0         | distractor intencionado  |

**Total USDT distribuido**: 1 565 USDT, todos los importes efectivos
estrictamente inferiores al umbral sub-Travel-Rule de 999 USD
(*structuring* deliberado del *Integration specialist*).

**Verificación pública**. Todas las transacciones son inspeccionables
individualmente en Sepolia Etherscan visitando la página de cada
dirección; la página de `alice`
[`sepolia.etherscan.io/address/0x2628e757b4A3e13E2aC7F0648C0c50c66D0DC649`](https://sepolia.etherscan.io/address/0x2628e757b4A3e13E2aC7F0648C0c50c66D0DC649)
lista las transferencias iniciales que originan el resto del grafo.
Los eventos on-chain relevantes (`Transfer` de USDT, `Deposit` y
`Withdrawal` de MockTornado, `Swap` de MockUniswapV2Pool) están
decodificados en el fichero `chain_trace.jsonl` de los *artifacts*.

**Nota sobre la selección de escenario**. La ejecución sobre Sepolia
se limitó a `defi-exploit` (dos corridas: *seed* 100 de 19 min con
1 ETH robado y *seed* 500 de 61 min con 10 ETH robados). La ejecución
adicional de `stablecoin-scam` y `ransomware-cashout` sobre Sepolia
queda como extensión inmediata sin cambios de código: basta reejecutar
el *runner* con `--scenario stablecoin-scam` y
`--scenario ransomware-cashout` una vez el *deployer* disponga de
suficiente ETH Sepolia adicional. La validación de estos dos
escenarios sí se realizó extensivamente sobre Anvil local
(§8.9.3), donde el coste por corrida es cero.

### D.6 Instrucciones de reproducción del despliegue

El *runbook* completo (prerequisitos, gestión de secretos, comandos)
está mantenido en `docs/SEPOLIA_DEPLOY.md` del repositorio.
Resumidamente, el despliegue completo se reproduce con:

```bash
cp .env.sepolia.example .env.sepolia
# Poblar SEPOLIA_RPC_URL, SEPOLIA_DEPLOYER_PRIVATE_KEY, ETHERSCAN_API_KEY
python scripts/deploy_eth_mocks_sepolia.py --dry-run   # Verifica entorno
python scripts/deploy_eth_mocks_sepolia.py             # Despliegue real
python scripts/verify_sepolia_deployment.py            # Smoke test
```

Si el despliegue original de MiMCSponge se atasca por infra-estimación
de *priority fee* (§8.9.1), el *script* de recuperación
`scripts/deploy_sepolia_resume.py` reanuda desde la fase MiMC con
EIP-1559 y prioridad de 5 gwei explícita.

## Anexo E — Comandos exactos de reproducción

Todos los resultados numéricos y figuras del Capítulo 8 son
reproducibles con los siguientes comandos, ejecutados desde la raíz
del repositorio con el conda environment `aml-thesis` activo. El
tiempo indicado es *wall-clock* en la máquina de desarrollo
(hardware detallado en el Cap. 4 §4.7.2).

```bash
# --- Detección binaria local ---

# Split estándar 80/20 sobre simulación (Tabla §8.5.1)
python scripts/eval_gcn_simulation.py --split standard    # ~2 min

# LOCO-CV sobre simulación (Tabla §8.5.2)
python scripts/loco_simulation_3detectors.py              # ~15 min

# LOCO sobre EthereumHeist real (Tabla §8.5.3)
python scripts/loco_ethereum_heist.py                     # ~30 min

# Auditoría metodológica (§8.10)
python scripts/audit_f1_memorization.py                   # ~5 min

# Sanity checks (§8.5.4)
python scripts/eval_gcn_elliptic_plus_plus.py             # ~10 min
python scripts/eval_gcn_openaml.py                        # ~3 min

# --- Atribución cross-exchange ---

# Simulación con LLM Haiku 4.5 (Tabla §8.6.1)
python scripts/eval_llm_defender.py --model haiku         # ~30 s, ~$0.04

# Simulación con LLM Sonnet 4.6 (Tabla §8.6.1 headline)
python scripts/eval_llm_defender.py --model sonnet        # ~2 min, ~$0.17

# EthereumHeist con LLM Haiku (Tabla §8.6.2)
python scripts/eval_llm_defender_heist.py --model haiku --exclude-big-hacks
                                                          # ~5 min, ~$0.05

# EthereumHeist con LLM Sonnet (Tabla §8.6.2 headline)
python scripts/eval_llm_defender_heist.py --model sonnet --exclude-big-hacks
                                                          # ~8 min, ~$0.14

# --- Preparación de datasets ---

# Construir el pickle combinado EthereumHeist desde las 23 carpetas
python scripts/load_ethereum_heist.py                     # ~10 min

# Combinar 420 corridas del simulador propio
python -m aml.detectors.dataset combine \
    ~/aml-results/batch_2026-06-26/v5b/*                  # ~5 min

# --- Sepolia ---

# Deploy sobre Sepolia (requiere .env.sepolia populated)
python scripts/deploy_eth_mocks_sepolia.py --dry-run      # ~5 s (dry-run)
python scripts/deploy_eth_mocks_sepolia.py                # ~8 min, ~0.05 ETH
python scripts/verify_sepolia_deployment.py               # ~10 s (read-only)
```

## Anexo F — Estructura del repositorio

```
aml-thesis/
├── contracts/                Contratos Solidity (~800 LOC)
│   ├── MockUSDT.sol          ERC-20 mock (6 decimales, permissionless mint)
│   ├── MockUniswapV2Pool.sol AMM ETH/USDT constant-product
│   ├── MockTornado.sol       Mezclador ZK Groth16
│   ├── MerkleTreeWithHistory.sol
│   ├── MockBridge.sol        Puente cross-chain lock-and-release
│   ├── IHasher.sol / IVerifier.sol
│   └── Verifier.sol          Auto-generado por snarkjs
├── circuits/                 Circuitos Circom para el mezclador
│   └── withdraw.circom       Prueba de pertenencia al árbol Merkle
├── src/aml/                  Paquete Python principal (~9 000 LOC)
│   ├── attackers/            Multi-agente ofensivo
│   │   ├── coordinator.py    Opus 4.7 estratega
│   │   ├── sub_agent.py      Sonnet 4.6 x 3 (Placement/Layering/Integration)
│   │   ├── prompts.py        4 system prompts + 3 bloques composicionales
│   │   ├── tools.py          19 herramientas on-chain
│   │   ├── llm_client.py     Wrapper Anthropic SDK
│   │   ├── run_campaign.py   Runner end-to-end
│   │   └── scenarios.py      Escenarios pre-definidos (defi/scam/ransomware)
│   ├── detectors/            Pipeline defensivo
│   │   ├── gnn.py            GCN 2-layer 32-hidden
│   │   ├── gat.py            GAT alternativo
│   │   ├── baselines.py      Louvain + PerExchangeDetector
│   │   ├── multi_agent.py    MultiAgentDetector + LLMDefenderCoordinator
│   │   ├── dataset.py        partial_visibility_split federación
│   │   ├── graph.py          NetworkX ↔ PyTorch Geometric
│   │   ├── eval.py           F1, ARI, homogeneidad, completitud
│   │   ├── run_benign.py     Generador de campañas benignas
│   │   └── viz.py            Utilidades de visualización
│   ├── chains/               Abstracción blockchain
│   │   ├── anvil.py          Context manager Anvil local
│   │   ├── mimc.py           Deploy MiMCSponge auto-generado
│   │   ├── eth_stack.py      Stack completo mock ETH
│   │   └── trace.py          Extracción grafo desde RPC
│   └── utils/                Helpers cross-cutting
├── scripts/                  Scripts CLI de reproducción
│   ├── deploy_eth_mocks.py         Deploy sobre Anvil ephemeral
│   ├── deploy_eth_mocks_sepolia.py Deploy sobre Sepolia persistent
│   ├── verify_sepolia_deployment.py
│   ├── eval_llm_defender.py        Eval sobre simulación
│   ├── eval_llm_defender_heist.py  Eval sobre EthereumHeist
│   ├── loco_simulation_3detectors.py LOCO-CV simulación
│   ├── loco_ethereum_heist.py      LOCO-CV EthereumHeist
│   ├── audit_f1_memorization.py    Auditoría §8.10
│   ├── load_ethereum_heist.py      Adaptador dataset Wu 2023
│   └── ...                          (más scripts de utilidad)
├── tests/                    Suite pytest (~300 tests, cobertura > 85%)
├── docs/                     Documentación
│   └── SEPOLIA_DEPLOY.md     Runbook Sepolia
├── deployments/              JSONs de deploys (gitignored)
├── data/                     Datasets (gitignored excepto price cache)
├── results/                  JSON outputs por experimento
├── tfm/                      Este documento
│   ├── chapters_v2/          Capítulos definitivos (estructura UC3M 10 caps)
│   │   ├── 01_introduccion.md
│   │   ├── 02_analisis_comparaciones.md
│   │   ├── 03_tecnologias.md
│   │   ├── 04_diseno_diagrama.md
│   │   ├── 05_lenguajes_usados.md
│   │   ├── 06_arquitectura_software.md
│   │   ├── 07_decisiones.md
│   │   ├── 08_implementacion_pruebas.md
│   │   ├── 09_datasets_parametros.md
│   │   ├── 10_conclusiones.md
│   │   └── A_anexos.md          (este archivo)
│   └── README.md             Instrucciones pandoc → PDF/DOCX
├── foundry.toml              Config compilador Solidity
├── pyproject.toml            Config paquete Python
├── environment.yml           Conda environment
├── .env.example              Template variables entorno
├── .env.sepolia.example      Template Sepolia
└── README.md                 Overview del repo
```

## Anexo G — Atribuciones de código, artefactos criptográficos y datasets de terceros

Este anexo enumera exhaustivamente todo el código, los artefactos
criptográficos y los datasets de terceros integrados en el proyecto,
con la licencia bajo la que se utilizan y una descripción de la
adaptación o el rol dentro del sistema. El repositorio del proyecto
también incluye ficheros `LICENSE` (código propio bajo licencia MIT)
y `ATTRIBUTIONS.md` (versión operacional de esta lista para
consumidores del código).

### G.1 Contratos Solidity adaptados de proyectos externos

**Tornado Cash** — [`github.com/tornadocash/tornado-core`](https://github.com/tornadocash/tornado-core) (MIT).
Base arquitectónica del mezclador ZK utilizado en el simulador:

| Fichero propio                                | Origen Tornado                       | Naturaleza de la adaptación                                                                                                                                             |
|-----------------------------------------------|--------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `contracts/MockTornado.sol`                   | `contracts/ETHTornado.sol`           | Denominación única fija (sin *routing* multi-denominación); sin *relayer* (fee y refund a cero en las pruebas); comentarios ampliados para revisores externos al ZK.    |
| `contracts/MerkleTreeWithHistory.sol`         | `contracts/MerkleTreeWithHistory.sol`| Preservación literal de la estructura *append-only* con historial acotado de raíces, hashLeftRight MiMC-Feistel y dígitos de subárbol precomputados; cambios sólo en comentarios e idioms Solidity 0.8+; sin cambios semánticos al árbol.  |
| `circuits/withdraw.circom`                    | `circuits/withdraw.circom`           | Preservación literal del witness (nullifier + secret, prueba de inclusión Merkle, derivación del nullifier hash, binding de recipient/fee/refund); constantes `LEVELS=10` y `fee=refund=0` reflejan la variante *no-relayer single-denomination*.  |

La preservación literal del *cryptographic core* es intencional: es
lo que asegura que la simulación tenga las mismas propiedades de
soundness y zero-knowledge que el sistema real que las campañas de
blanqueo explotan en mainnet, requisito para que los *findings* del
Capítulo 8 sean transferibles.

**Uniswap V2** — [`github.com/Uniswap/v2-core`](https://github.com/Uniswap/v2-core) (GPL-2.0).
Referencia algorítmica (no reutilización de código):

| Fichero propio                        | Naturaleza de la referencia                                                                                                                                              |
|---------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `contracts/MockUniswapV2Pool.sol`     | Reimplementación *hand-written* del AMM constant-product con *fee* del 0,3 %. No hay LP tokens; un único bootstrapper siembra la liquidez y a partir de entonces cualquier dirección puede hacer swap. Reservas trackeadas en storage sin sync desde balances. |

### G.2 Primitivas criptográficas y artefactos generados

**snarkjs** — [`github.com/iden3/snarkjs`](https://github.com/iden3/snarkjs), *Copyright 2021 0KIMS association* (GPL-3.0).
Genera automáticamente el verifier on-chain y las pruebas Groth16 off-chain:

- `contracts/Verifier.sol`: contrato `Groth16Verifier` auto-generado
  por `snarkjs zkey export solidityverifier` aplicado al *zkey* del
  circuito `withdraw`. Se distribuye sin modificar bajo la licencia
  GPL-3.0 heredada.
- Uso off-chain: `scripts/zk_helpers.js` y `src/aml/chains/mimc.py`
  invocan `snarkjs.groth16.prove` para producir cada prueba de retiro.

**circomlib / circomlibjs** — [`github.com/iden3/circomlib`](https://github.com/iden3/circomlib) y [`github.com/iden3/circomlibjs`](https://github.com/iden3/circomlibjs) (MIT).
Primitivas hash consumidas por circuito y contrato:

- `circuits/withdraw.circom` incluye `circomlib/circuits/mimcsponge.circom`
  para la permutación Feistel MiMC.
- El bytecode del contrato `MiMCSponge` se genera en tiempo de deploy
  vía `circomlibjs.mimcSpongecontract.createCode("mimcsponge", 220)`
  y se despliega por `src/aml/chains/mimc.py`.

**Hermez Powers of Tau** — ceremonia de trusted setup público
[`github.com/iden3/snarkjs#7-prepare-phase-2`](https://github.com/iden3/snarkjs#7-prepare-phase-2).
El fichero `powersOfTau28_hez_final_*.ptau` (fase 1 universal de
Hermez) se reutiliza para el trusted setup específico del circuito
`withdraw`; no lo generamos nosotros, sino que descargamos y
verificamos el hash oficial. El script `scripts/setup_zk.sh` documenta
el flujo.

**Foundry** — [`github.com/foundry-rs/foundry`](https://github.com/foundry-rs/foundry) (Apache-2.0 o MIT dual).
Toolchain de compilación, testing y despliegue Solidity utilizado para
todos los contratos. Los binarios `forge`, `anvil` y `cast` se
invocan desde los scripts pero no se redistribuyen con el proyecto.

**circom** — [`github.com/iden3/circom`](https://github.com/iden3/circom) (GPL-3.0).
Compilador del lenguaje circom utilizado para compilar
`circuits/withdraw.circom` a R1CS, WebAssembly witness generator y
símbolos.

### G.3 Datasets de terceros

Los cuatro *datasets* utilizados en el Capítulo 8 son externos y se
citan académicamente:

| Dataset                       | Cita académica                                                                                                                                          | Provisión                                                              | Rol en el TFM                              |
|-------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------------------------------|
| **Elliptic** (2019)           | Weber, M., Domeniconi, G., Chen, J. et al. *Anti-Money Laundering in Bitcoin: Experimenting with Graph Convolutional Networks for Financial Forensics*. arXiv 1908.02591. | Kaggle público                                                          | Baseline histórico (referenciado en §8.10)  |
| **Elliptic++** (2023)         | Elmougy, Y., Liu, L. *Demystifying Fraudulent Transactions and Illicit Nodes in the Bitcoin Network*. arXiv 2305.15214.                                | GitHub `git-disl/EllipticPlusPlus`                                     | Sanity check GCN (§8.5.4)                  |
| **EthereumHeist** (2023)      | Wu, J. et al. *Toward Understanding Asset Flows in Crypto Money Laundering through the Lenses of Ethereum Heists*. IEEE TIFS 18: 1994-2009.            | Dropbox + GitHub `HxQlaive/EthereumHeist`                              | Validación externa real (§8.5.3, §8.6.2)   |
| **OpenAML v1** (2025)         | *FINOS OpenAML v1 — DTCC AI Hackathon dataset*. FINOS (Linux Foundation).                                                                              | GitHub `finos/OpenAML` (`training_data.csv`)                           | Sanity check GCN (§8.5.4)                  |

Adicionalmente se referencian sin utilizar directamente:

- **AMLWorld / AMLSim**: Altman, E., Bhattacharyya, A., Chen, C. et al.
  *Realistic Synthetic Financial Transactions for Anti-Money Laundering
  Models*. NeurIPS 2023 Datasets and Benchmarks.
  [`github.com/IBM/AMLSim`](https://github.com/IBM/AMLSim). Citado en el
  Capítulo 2 como *state of the art* de generación sintética AML pero no
  ejecutado en el pipeline propio.

**Datos de precios**. Los ficheros CSV en `data/prices/{eth,trx,usdt}.csv`
son *snapshots* diarios descargados desde la API pública gratuita de
CoinGecko ([`coingecko.com/api`](https://www.coingecko.com/api)) y
cacheados localmente por reproducibilidad. No hay licencia formal
sobre estos datos; el TFM los usa exclusivamente como *market context*
del oráculo determinista descrito en §3.4.

### G.4 Referencias algorítmicas de detectores

Los cuatro detectores de referencia del Capítulo 8 son
implementaciones de algoritmos ampliamente conocidos:

- **GCN** (Kipf, T., Welling, M. *Semi-Supervised Classification with
  Graph Convolutional Networks*. ICLR 2017). Implementado mediante
  `torch_geometric.nn.GCNConv` en `src/aml/detectors/gnn.py`.
- **GAT** (Veličković, P. et al. *Graph Attention Networks*. ICLR 2018).
  Implementado mediante `torch_geometric.nn.GATConv` en
  `src/aml/detectors/gat.py`.
- **EvolveGCN** (Pareja, A. et al. *EvolveGCN: Evolving Graph
  Convolutional Networks for Dynamic Graphs*. AAAI 2020). Referenciado
  en el Capítulo 2 y el ROADMAP; no implementado dentro del scope v1
  del TFM.
- **Louvain** (Blondel, V. D. et al. *Fast unfolding of communities in
  large networks*. J. Stat. Mech. 2008). Consumido vía
  `networkx.algorithms.community.louvain_communities`.

Métricas de evaluación importadas de la literatura:

- **Adjusted Rand Index** — Hubert, L., Arabie, P. *Comparing
  partitions*. Journal of Classification 1985. Consumido vía
  `sklearn.metrics.adjusted_rand_score`.
- **Homogeneidad y Completitud** — Rosenberg, A., Hirschberg, J.
  *V-Measure: A Conditional Entropy-Based External Cluster Evaluation
  Measure*. EMNLP-CoNLL 2007. Consumido vía
  `sklearn.metrics.homogeneity_completeness_v_measure`.

### G.5 Bibliotecas software (dependencies)

Sin ánimo exhaustivo, las bibliotecas software principales sobre las
que se apoya el proyecto:

| Biblioteca                | Licencia         | Rol                                                                     |
|---------------------------|------------------|-------------------------------------------------------------------------|
| **PyTorch**               | BSD-3            | Backend numérico + autograd para los GCN/GAT                            |
| **PyTorch Geometric**     | MIT              | Capas de convolución sobre grafos (`GCNConv`, `GATConv`)                 |
| **NetworkX**              | BSD-3            | Manipulación del grafo etiquetado + Louvain baseline                    |
| **scikit-learn**          | BSD-3            | Métricas ARI / homogeneidad / completitud                                |
| **web3.py**               | MIT              | RPC hacia Anvil / Sepolia + firma de transacciones                       |
| **eth-account**           | MIT              | Generación determinista de wallets burner                                |
| **snarkjs** (npm)         | GPL-3.0          | Generación de pruebas Groth16 off-chain                                  |
| **circomlibjs** (npm)     | MIT              | Bytecode del contrato MiMCSponge on-chain                                |
| **Anthropic SDK**         | MIT              | Cliente HTTP hacia la API de Claude                                      |
| **pytest**                | MIT              | Framework de tests                                                       |

### G.6 Contratos escritos de cero (sin adaptación externa)

Para claridad, los siguientes contratos son *hand-written* sin código
externo importado:

- `contracts/MockUSDT.sol` (63 líneas). ERC-20 mínimo de 6 decimales,
  sin blacklist ni fee-on-transfer, mint permissionless. Sin relación
  con el Tether real.
- `contracts/MockBridge.sol` (94 líneas). Bridge cross-chain
  lock-and-release simplificado sin multisig ni verificación de
  firmas on-chain. Modela únicamente la señal on-chain que un detector
  observaría.
- `contracts/IHasher.sol` y `contracts/IVerifier.sol` (interfaces
  mínimas, ~15 líneas cada una).

### G.7 Licencia del código propio del proyecto

Todo el código propio del proyecto —código Python en `src/aml/`,
scripts CLI en `scripts/`, tests en `tests/`, contratos *hand-written*
listados en §G.6 y el propio TFM en `tfm/`— se libera bajo
**licencia MIT** (fichero `LICENSE` en la raíz del repositorio).
Los ficheros de terceros incluidos en el repositorio conservan su
licencia original (marcada en el *header* SPDX de cada fichero); en
particular, `contracts/Verifier.sol` está bajo GPL-3.0 por herencia
de snarkjs.

Consumidores del código deben respetar el conjunto de licencias
aplicable a los ficheros que utilicen. Un adoptante que redistribuya
sólo los contratos *hand-written* + el código Python está bajo MIT
únicamente; un adoptante que redistribuya el stack ZK completo debe
respetar además la GPL-3.0 del verifier.

## Anexo H — Declaración de uso de Inteligencia Artificial Generativa

> **Nota**: Este anexo cumple con el requisito establecido por la
> Universidad Carlos III de Madrid desde el curso 2024-2025 sobre
> declaración obligatoria del uso de IA Generativa en Trabajos Fin de
> Máster. La plantilla oficial descargable desde
> [`uc3m.libguides.com/TFM/IAGenerativa`](https://uc3m.libguides.com/TFM/IAGenerativa)
> se rellena separadamente; este anexo documenta las prácticas
> subyacentes con mayor detalle.

### H.1 Modelos y herramientas utilizadas

Durante el desarrollo del proyecto se han utilizado los siguientes
modelos de lenguaje grandes (LLM) accedidos vía la API de Anthropic:

- **Claude Opus 4.7** (identificador `claude-opus-4-7`) — uso puntual
  para tareas de razonamiento complejo (revisión metodológica,
  discusión de trade-offs de diseño).
- **Claude Sonnet 4.6** (identificador `claude-sonnet-4-6`) — uso
  principal como asistente de programación bajo la CLI *Claude Code*.
- **Claude Haiku 4.5** (identificador `claude-haiku-4-5-20251001`) —
  uso económico para iteración rápida durante desarrollo.

**Importante**: estos mismos modelos se utilizan también como *objeto
de estudio* dentro del propio sistema descrito en los Capítulos 6
y 7
(Opus como coordinador atacante, Sonnet como sub-agentes tácticos,
todos ellos como coordinadores defensores). Esta doble función
—herramienta de desarrollo y objeto de investigación— se declara
explícitamente para evitar ambigüedad.

### H.2 Tareas para las que se ha utilizado IA generativa

Alineadas con los usos que la política UC3M enumera como
**permitidos**:

**Procesamiento de datos con supervisión y postprocesamiento**:
- Ejecución de scripts de evaluación sobre los *datasets* del proyecto
  (`scripts/eval_llm_defender.py`, `scripts/loco_ethereum_heist.py`,
  etc.) bajo la revisión posterior de todos los resultados numéricos.
- Análisis exploratorio de artefactos generados por las campañas del
  atacante para identificar patrones de refinamiento (§8.9.5).

**Herramienta de desarrollo de código (co-piloto)**:
- Asistencia en la implementación de contratos Solidity adaptados de
  Tornado Cash y en la integración de la toolchain ZK (snarkjs +
  circomlib), siempre bajo revisión y validación manual.
- Asistencia en la implementación del *pipeline* Python (agentes
  atacantes, detectores, particionado federado), siguiendo las
  decisiones arquitectónicas descritas en el Capítulo 6.
- Depuración de errores puntuales durante desarrollo iterativo
  (fixes de reconciliación de árbol Merkle, snarkjs, gas budgeting).

**Refinamiento posterior de la redacción**:
- Revisión gramatical, sintáctica y de coherencia terminológica
  sobre borradores redactados originalmente por el autor. La
  estructura argumental, las decisiones metodológicas y los
  hallazgos empíricos reportados son enteramente atribuibles al
  autor.

**Tareas administrativas**:
- Conversión de los ficheros Markdown de los capítulos a formato
  `.docx` (script `md_to_docx.py`).
- Traducción parcial de documentación auxiliar del proyecto (README,
  ROADMAP, runbook Sepolia) del inglés al español.

### H.3 Tareas para las que NO se ha utilizado IA generativa

Alineadas con los usos que la política UC3M enumera como **no
aceptados**:

**Diseño de la arquitectura del sistema** (Capítulos 6 y 7). El
diseño multi-agente FATF del atacante, el particionado de visibilidad
parcial federada y la asimetría LLM-vs-LLM del defensor son
decisiones del autor.

**Decisiones metodológicas** (Capítulo 9). La selección de *datasets*
(Elliptic++, EthereumHeist, OpenAML v1, simulación propia), la
estrategia de *cross-validation* (LOCO-CV como métrica primaria), la
elección de *baselines* (Louvain, GCN, coseno) y la parametrización
de todos los detectores son decisiones documentadas y justificadas
por el autor.

**Generación de datos experimentales**. Todos los datos reportados en
el Capítulo 8 provienen de: (i) ejecución real del pipeline sobre la
blockchain Anvil local o la testnet Sepolia; (ii) *datasets*
académicos externos citados explícitamente en el Anexo G. **En ningún
caso se han fabricado resultados experimentales mediante IA
generativa**.

**Interpretación de los resultados y hallazgos del Capítulo 8**. Las
conclusiones sobre el trade-off ARI vs interpretabilidad (§8.8), la
auditoría de memorización (§8.10) y las lecciones metodológicas del
Capítulo 10 §10.3 reflejan el análisis crítico del autor sobre los
datos empíricos observados.

### H.4 Trazabilidad y verificabilidad

- El historial completo de commits del repositorio
  [`github.com/0xAnonsal/aml-thesis`](https://github.com/0xAnonsal/aml-thesis)
  preserva la traza de contribuciones asistidas por LLM cuando fueron
  aplicables (metadato `Co-Authored-By` en los mensajes de commit
  durante el periodo de desarrollo activo con asistencia LLM).
- Todos los prompts del sistema utilizados **dentro** del pipeline
  (para el atacante y el defensor, objeto de estudio) están
  publicados en los Anexos A y B de este documento y en los ficheros
  fuente `src/aml/attackers/prompts.py` y
  `src/aml/detectors/multi_agent.py`.
- Los prompts utilizados en la asistencia al desarrollo (Claude Code
  CLI) no se persisten sistemáticamente por el diseño de la
  herramienta, pero el resultado final de cada tarea es revisable en
  el diff del commit correspondiente.

### H.5 Responsabilidad y autoría

Conforme al reglamento UC3M sobre uso de IA en TFM, el autor asume
la **plena responsabilidad y autoría** del trabajo presentado. La IA
generativa se ha utilizado exclusivamente como herramienta
complementaria bajo supervisión humana continua; todas las
decisiones sustantivas (diseño, metodología, interpretación) son
atribuibles personalmente al autor, que responderá ante el tribunal
del TFM por la totalidad del contenido.


