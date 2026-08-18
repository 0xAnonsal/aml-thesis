# Limitaciones del simulador AML — inventario

Documento vivo. Recoge las **restricciones prácticas y de modelado** del
simulador multi-agente respecto al blanqueo real on-chain. Ordenado por
severidad para el thesis: primero las que ya observamos empíricamente
(chapter 5 findings), luego las conocidas por diseño.

---

## 1. Limitaciones observadas empíricamente

### 1.1 Mixer withdraw falla por Merkle tree incompleto — **[FIXED 2026-08-12]**

**Síntoma:** en seed200 (defi-exploit), Sonnet completó `mixer_deposit` pero
`mixer_withdraw` reverted con "unknown root". Adaptación: Sonnet cambió a
multi-hop layering (fan-out linear + convergent).

**Causa raíz:** el contract `MockTornado` mantiene un Merkle tree global
compartido entre TODAS las campañas. Cada deposit ocupa un `leafIndex`
incremental. El código cliente en `_mixer_collect_leaves` (tools.py:2101)
escaneaba `Deposit` events sólo desde `start_block - 100` — capturaba SU
deposit pero no los históricos de seeds previos. El tree reconstruido local
tenía 1 leaf en lugar de N → root local ≠ `isKnownRoot()` on-chain →
verifier rechaza la Groth16 proof.

**Impacto pre-fix:** cualquier campaign después del PRIMER deposit ever
fallaba silenciosamente la parte ZK. Los 3 tests defi-exploit se apoyaron
en fallbacks multi-hop.

**Fix aplicado:** paginación de `eth_getLogs` en chunks de 500 bloques
desde `tornado_deploy_block` (grabado ahora en `deployments/sepolia.json`).
Ver `docs/fix_mixer_merkle.md` para detalle técnico.

### 1.2 Compresión temporal irrealista

**Observado:** un campaign completo (placement → layering → integration)
en 58 min wall-clock, con delays sintéticos de 30-100 bloques (~6-20 min
Sepolia) entre fases.

**Realidad:** operaciones de Lazarus (Bybit, Ronin) se distribuyen en
semanas o meses. Ejemplo: Ronin bridge (marzo 2022) — depósitos a Tornado
Cash siguieron durante los 6 meses siguientes al hack.

**Por qué es una limitación:** detectores temporales (features de burst
frequency, dormancy periods) no pueden entrenarse contra patrones
realistas de "silencio-actividad-silencio" de semanas.

### 1.3 Alcance monetario pequeño

**Observado:** tests con 1-3 ETH (defi-exploit) y hasta 5 ETH (ransomware
default). Total dispersado en seed200: $18,445 USDT.

**Realidad:** ataques reales manejan $100M-1.4B. Ronin $625M, Bybit $1.4B,
Wormhole $325M.

**Trade-off asumido:** validación externa en Sepolia limitada por gas fees
reales y tiempo de simulación. Detección heurística de "small-account
peculiarities" (ej. structuring bajo $999) sigue siendo válida porque
opera sobre la GRANULARIDAD, no el monto absoluto.

---

## 2. Limitaciones por diseño

### 2.1 Sin bridging cross-chain

**Estado:** existe `bridge` tool (MockBridge deployed) pero ninguna de las
3 campañas de Sonnet lo usó. Bridge deposita ETH en un contract y emite
evento `BridgeInitiated(destChain, destAddr, amount)`. NO hay chain de
destino real — es un simulacro one-shot para modelar la SEÑAL on-chain.

**Realidad:** casi todos los hacks recientes usan bridges (Ronin, Nomad,
Wormhole, Poly). THORChain, Wormhole, cBridge son los top targets/tools.

**Extensión futura:** modelar el "delay" y "asset conversion rate" del
bridging para reproducir el patrón "burst deposits antes de bridge lock".

### 2.2 Sin oráculos manipulables

**Estado:** `MockUniswapV2Pool` tiene precio hardcodeado (constant product
con reserves iniciales fijas). Los swaps ejecutan al precio spot invariante.

**Realidad:** ataques como Mango Markets ($117M), Cream Finance ($130M) y
laundering con "self-trading" en DEX pequeños dependen de manipular el
oracle temporalmente. Sonnet no puede reproducir esa técnica en tu sim.

**Extensión futura:** slippage AMM real + un oracle Chainlink-style con
delay que se pueda desincronizar del pool spot price.

### 2.3 Sin plataformas P2P

**Estado:** no modelado.

**Realidad:** Lazarus + otros usan masivamente LocalBitcoins, Paxful,
Binance P2P para el off-ramp final. El launderer vende crypto a
contrapartes reales inocentes, recibe fiat de múltiples cuentas
legítimas. Rompe la heurística "single payer" del clustering AML.

**Por qué no está:** requiere modelar orderbook + KYC seller + reputation.
Fuera de scope on-chain.

### 2.4 Sin OTC brokers

**Estado:** las `clean_exit` wallets simulan cuentas de exchange, pero no
hay OTC broker layer (grande, off-chain, informal).

**Realidad:** ~40% del off-ramp de crímenes crypto pasa por OTC brokers en
Rusia, Hong Kong, Dubai. Chainalysis report 2024 los llama "professional
money laundering services". Cobran 5-15% de fee.

**Extensión futura:** una tool `otc_broker_transfer(amount, fee_pct,
counterparty_kyc_level)` que emita un evento pero deje la wallet destino
"opaca" (no siga con transfers on-chain trackables).

### 2.5 Sweep — ETH intercambiado en pool: **RECUPERADO (2026-08-18)**

**Estado histórico**: `scripts/sweep_sepolia.py` reclamaba ETH y USDT
de wallets con private key conocida pero no podía recuperar el ETH
intercambiado por USDT en el pool ni el ETH depositado en Tornado
cuyo withdraw falló.

**Mitigación aplicada 2026-08-18**: el sweep script se extendió con
un **reverse-swap opcional post-loop** que, tras acumular el USDT en
la wallet del deployer, ejecuta `approve` + `swapUSDTForETH` sobre
`MockUniswapV2Pool` con slippage tolerance parametrizable (default
$2\,\%$). El impacto en la pérdida real por corrida cae desde $\sim
1$-$2$ ETH histórico a $\sim 0{,}07$ ETH (gas efectivo + $0{,}3\,\%$
fee del pool).

**Pendiente**: el ETH depositado en `MockTornado` sigue siendo
recuperable únicamente por el mecanismo normal (`mixer_withdraw` con
la note original preservada); si la note se perdió, el ETH queda
locked forever. Los runs actuales persisten las notes en
`chain_trace.jsonl` pero no hay script auxiliar de reclamación —
extensión de future work.

### 2.6 Sin adversario que reaccione al defender

**Estado:** el attacker (Coordinator) opera una campaign completa sin
información sobre si su tráfico fue detectado. El defender clasifica
post-hoc sobre el chain_trace.

**Realidad:** launderers monitorizan direcciones sospechosas en Etherscan,
detectan cuando exchanges bloquean depósitos, adaptan rutas en tiempo real.

**Extensión futura:** loop closed-feedback donde el defender emite señales
"exit wallet X flagged" que el attacker vea y adapte. Fuera de scope para
el TFM (Reto potencial de futuro trabajo).

### 2.7 Prompts del attacker "guían" la evasión

**Estado:** el prompt de `defi-exploit` en scenarios.py explicita las
técnicas de evasión ("non-round amounts", "trifurcation pattern",
"disordered timing", "empty distractors"). El LLM no las descubre desde
cero — las ejecuta.

**Impacto en interpretación:** los findings del thesis miden **la
capacidad de un LLM para EJECUTAR una campaña sofisticada**, no de
DESCUBRIR técnicas nuevas. Válido para el objetivo de la thesis (¿puede
un LLM off-the-shelf reproducir sofisticación observada en Lazarus?)
pero debe declararse explícitamente en la limitations section del
chapter 5.

### 2.8 Sin herramientas de reconnaissance

**Estado:** el Coordinator recibe una spec "aquí hay X ETH en wallet
alice, blanquéalos". No hace scan pre-attack de "qué exchanges tienen
sanctions screening más débil", "qué DEX tiene menos slippage", "qué
bridge es más rápido".

**Realidad:** APTs hacen semanas de reconnaissance antes del hack.
Chainalysis y TRM Labs reportan que Lazarus mapea infrastructure de
exchanges víctimas 2-6 meses antes.

**Extensión futura:** una fase 0 de recon con tools tipo
`query_exchange_kyc_level`, `estimate_pool_slippage`, `check_bridge_delay`.
Añade coste API y complejidad.

---

## Resumen para el chapter 5 (limitations section)

1. **Empíricas observadas** (contar como findings positivos): 1.1 fixed,
   1.2 y 1.3 declarar como scope.
2. **Por diseño, scope explícito** (declarar como "trabajo futuro"):
   2.1-2.8.
3. **Threat to validity crítico**: 2.7 (prompts guían evasión). Debe
   estar en la primera línea del "limitations".
