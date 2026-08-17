# 🎯 Cheatsheet para reunión con Chema
**Fecha reunión**: 2026-07-15
**Última actualización**: 2026-07-14 madrugada

---

## 📋 Cómo usar este documento

Cada sección tiene:
- **PREGUNTA de Chema** que anticipa
- **COMANDO exacto** para copy-paste
- **QUÉ VERÁ** cuando lo ejecutes

Abre 2 ventanas: **este .md** + **PowerShell**. Cuando pregunte algo, buscas la sección y pegas.

---

## 🚀 SETUP INICIAL (antes de la reunión)

Abre PowerShell y ejecuta:

```powershell
wsl
cd ~/aml-thesis
conda activate aml
```

Ahora ya estás en el proyecto listo para todo lo demás.

---

## 1️⃣ "¿En qué estado está el proyecto?"

### Numeros globales
```bash
echo "=== Benignos generados ===" && \
ls ~/aml-results/batch_2026-06-26/benign/ | wc -l && \
echo "=== Adversariales generados ===" && \
find ~/aml-results -maxdepth 3 -type d -name "*campaign*" -o -name "*attack*" 2>/dev/null | wc -l && \
echo "=== Contratos Solidity ===" && \
ls ~/aml-thesis/contracts/*.sol | wc -l && \
echo "=== Módulos código ===" && \
ls ~/aml-thesis/src/aml/
```

**Chema verá**: 400 benignos, N adversariales, 8 contratos, módulos organizados (attackers, defenders, detectors, env, chains, utils).

---

## 2️⃣ "Muéstrame un ejemplo de traza benigna"

```bash
SAMPLE=$(ls ~/aml-results/batch_2026-06-26/benign/ | head -1)
BASE=~/aml-results/batch_2026-06-26/benign/$SAMPLE

echo "=== METADATA de la campaña ===" 
cat "$BASE/meta.json" | python -m json.tool

echo ""
echo "=== SUMMARY humano-legible ===" 
cat "$BASE/summary.txt"

echo ""
echo "=== Primeras 5 líneas del chain_trace ===" 
head -5 "$BASE/chain_trace.jsonl" | python -m json.tool
```

**Chema verá**: JSON con eventos on-chain (transfers, swaps, mixing), metadata (seed, timestamp, tipo de campaña), summary humano.

---

## 3️⃣ "¿Cuáles son tus tipos de campañas benignas?"

```bash
ls ~/aml-results/batch_2026-06-26/benign/ | sed 's/.*benign_seed//' | sed 's/^/seed_/' | sort | uniq -c | tail -5

echo ""
echo "=== Tipos de campañas detectadas en tu batch más reciente ==="
ls ~/aml-results/batch_2026-06-26-v2/v5b/ | awk -F'_' '{print $2}' | sort | uniq -c
```

**Chema verá**: distribuciones por tipo. Los tuyos incluyen `stablecoin-scam`, `ransomware-cashout`, `benign` estándar. 

---

## 4️⃣ "Muéstrame los contratos Solidity"

```bash
echo "=== Contratos disponibles ===" 
ls ~/aml-thesis/contracts/

echo ""
echo "=== Mock Tornado (mixer principal, primeras 50 líneas) ===" 
head -50 ~/aml-thesis/contracts/MockTornado.sol

echo ""
echo "=== Mock DEX (Uniswap V2, primeras 50 líneas) ===" 
head -50 ~/aml-thesis/contracts/MockUniswapV2Pool.sol

echo ""
echo "=== USDT Mock (primeras 30 líneas) ===" 
head -30 ~/aml-thesis/contracts/MockUSDT.sol
```

**Chema verá**: contratos ERC20, Tornado-style mixer, Uniswap V2 pool, bridge. Todo local en Anvil (Foundry).

### Si pregunta por Sepolia
```bash
echo "=== ROADMAP Sepolia deployment ===" 
grep -A 30 -i "sepolia" ~/aml-thesis/ROADMAP.md | head -40
```

Y le enseñas el **Word de decisiones** donde está el plan Sepolia y los costes.

---

## 5️⃣ "¿Cómo funciona tu detector?"

```bash
echo "=== Estructura de detectores ===" 
ls ~/aml-thesis/src/aml/detectors/

echo ""
echo "=== Ficheros de entrenamiento ===" 
ls ~/aml-thesis/scripts/ | grep -i train

echo ""
echo "=== Baseline detector - inicio del código ===" 
head -60 ~/aml-thesis/scripts/train_baseline.py
```

**Chema verá**: GCN baseline, código de entrenamiento. Si pregunta por Elliptic2:

```bash
echo "=== Estado dataset Elliptic2 ==="
ls -la ~/aml-data/Elliptic2/ 2>/dev/null
ls -la ~/aml-data/elliptic2_raw/ 2>/dev/null
echo ""
echo "=== Script de descarga ===" 
head -30 ~/aml-thesis/scripts/download_elliptic.py
```

---

## 6️⃣ "¿Y los atacantes / adversarios?"

```bash
echo "=== Attackers implementados ===" 
ls ~/aml-thesis/src/aml/attackers/

echo ""
echo "=== Env (entorno de simulación) ===" 
ls ~/aml-thesis/src/aml/env/

echo ""
echo "=== Defenders ===" 
ls ~/aml-thesis/src/aml/defenders/
```

**Chema verá**: separación limpia attackers/defenders/env. La arquitectura multi-agent LLM se explica en el **Word de decisiones**.

---

## 7️⃣ "¿Qué resultados tienes ya?"

```bash
echo "=== HEADLINE final ===" 
cat ~/aml-results/RESULTS_HEADLINE.txt

echo ""
echo "=== Snapshot ===" 
cat ~/aml-results/SNAPSHOT_2026-06-26.md 2>/dev/null | head -80

echo ""
echo "=== Resultados 20 campañas ===" 
head -40 ~/aml-results/results_20_campaigns.txt 2>/dev/null
```

**Chema verá**: métricas AUC/precision/recall del pipeline actual.

---

## 8️⃣ "¿Y el plan a futuro?"

```bash
cat ~/aml-thesis/ROADMAP.md | head -100
```

Y sobre todo — **abre el Word de decisiones** en Windows:

```powershell
# Desde PowerShell (fuera de WSL):
start "C:\Users\Asus\Downloads\files\TFM_decisiones_v3.docx"
```

**Chema verá**: decisiones sobre Sepolia + sophistication metric + costes API/gas.

---

## 9️⃣ "¿Cuánto va a costar todo esto?"

Le enseñas la sección de **Costes** del Word. Resumen mental:

| Item | Coste estimado |
|---|---|
| **Sepolia gas** (~50 deploys + 500 txs) | **$0** (testnet gratis) |
| **Claude Sonnet API** (agent runs, 100 campañas × ~30k tokens) | **~$15-30** |
| **GPU cloud** (si se necesita para GCN) | **~$20-50** puntual (Colab Pro suficiente) |
| **Datasets** | **$0** (Elliptic2 público, OFAC público) |
| **TOTAL estimado** | **< $100** para toda la tesis |

---

## 🔟 "¿Qué datasets vas a usar?"

Sacas el Word (sección datasets) o dices:

- **Elliptic2** (KDD 2024) — 122M nodos, 200M edges, ground-truth de wallets ilícitos vía subgraphs
- **EthereumHeist** — 133 casos reales de hacks documentados
- **OFAC SDN List** — wallets sancionadas oficiales US Treasury
- **Etherscan tags** — heurísticos de exchanges/mixers/scams

```bash
echo "=== Estado descargas ==="
du -sh ~/aml-data/* 2>/dev/null
```

---

## 🎁 EXTRAS que Chema puede preguntar

### "¿Puedo ver el flujo end-to-end de una campaña adversarial?"

```bash
# Si tienes campañas adversariales listas:
find ~/aml-results -name "*adversarial*" -type d 2>/dev/null | head -3
find ~/aml-results -name "*campaign*" -type d 2>/dev/null | head -3

# Si no las tienes aún, le dices que están en la lista pendiente:
echo "Tasks pendientes:"
echo "  #2: Descarga Elliptic2"
echo "  #4: Deploy Sepolia"
echo "  #5: mixer_batch_deposit + mixer_batch_withdraw tools"
echo "  #6: Pre-train GCN sobre Elliptic2"
```

### "Muéstrame git status del proyecto"

```bash
cd ~/aml-thesis
git log --oneline | head -20
git status
```

### "¿Cómo ejecutas una simulación completa?"

```bash
ls ~/aml-thesis/scripts/ | head -20
# Los más importantes:
# - deploy_eth_mocks.py: despliega contratos en Anvil local
# - train_baseline.py: entrena el detector GCN base
# - inspect_benign_campaign.py: inspecciona una traza benigna
# - view_benign_corpus.py: vista general del corpus
```

---

## 🎬 GUION SUGERIDO para la reunión

**Duración total ~30-40 min**:

1. **0-5 min**: Estado global (comando #1)
2. **5-15 min**: Enseñar traza benigna (#2), tipos campañas (#3)
3. **15-25 min**: Enseñar contratos Solidity (#4) + Word decisiones (Sepolia)
4. **25-30 min**: Detector actual (#5) + resultados (#7)
5. **30-40 min**: Plan a futuro (#8) + costes (#9) + datasets (#10)

---

## 🚨 SI ALGO NO FUNCIONA

### WSL no arranca
```powershell
wsl --shutdown
Start-Sleep 5
wsl
```

### Python env no activa
```bash
conda activate aml
# Si no funciona:
source ~/miniconda3/bin/activate aml
```

### "Command not found: conda"
```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate aml
```

---

## 💾 Backup de todo esto

Este archivo está en:
- **Windows**: `C:\Users\Asus\Downloads\files\DEMO_CHEMA_cheatsheet.md`

Ábrelo en VS Code, Notepad++, o cualquier editor markdown para verlo bonito.
