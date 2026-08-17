# Despliegue en Sepolia — runbook de validación externa del TFM

Guía end-to-end para desplegar el stack de contratos AML mock sobre la testnet
Ethereum Sepolia. Es la Task #4 del plan del TFM y desbloquea la Task #7 (campaña
atacante de 6-8 h, la validación externa principal del capítulo 5).

**Presupuesto total de tiempo**: ~45-60 min una vez tengas listos los 3 keys.

## 0. Prerequisitos — keys que necesitas

Tener los tres listos ANTES de empezar. Si falta alguno bloqueás el deploy a
mitad y desperdiciás ETH Sepolia en retries.

| Key                            | Dónde obtenerlo                                            | Tiempo |
|--------------------------------|------------------------------------------------------------|--------|
| Sepolia RPC URL                | https://dashboard.alchemy.com (Create App → Sepolia)       | 5 min  |
| Deployer private key           | MetaMask → Account details → Show private key              | 1 min  |
| Etherscan API key (opcional)   | https://etherscan.io/myapikey                              | 3 min  |

**SEGURIDAD**:
- Nunca pegues la private key en chat, screenshots, logs, o Git.
- El fichero `.env.sepolia` está gitignored. Mantenlo así.
- Aunque el ETH Sepolia no tiene valor en dólares, trata la key como si fuera
  mainnet para construir hábitos seguros.

## 1. Setup local (una sola vez)

```bash
cd ~/aml-thesis

# Confirmar que Foundry está instalado
~/.foundry/bin/forge --version   # debería imprimir v1.7.0 o superior

# Confirmar dependencias Python
conda activate aml-thesis
python -c "from aml.chains.mimc import deploy_mimc; print('ok')"

# Asegurar que el verifier ZK está construido (contracts/Verifier.sol es per-machine)
ls contracts/Verifier.sol || bash scripts/setup_zk.sh withdraw

# Compilar todos los contratos
~/.foundry/bin/forge build
```

## 2. Poblar .env.sepolia

```bash
cp .env.sepolia.example .env.sepolia
# Editá .env.sepolia con tu editor de texto favorito.
# Rellená: SEPOLIA_RPC_URL, SEPOLIA_DEPLOYER_PRIVATE_KEY, ETHERSCAN_API_KEY (opcional)
```

## 3. Dry-run — verificar entorno sin gastar gas

```bash
python scripts/deploy_eth_mocks_sepolia.py --dry-run
```

Salida esperada:
```
Sepolia RPC:        https://eth-sepolia.g.alchemy.com/v2/*** (chain_id=11155111)
Deployer:           0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4
Balance:            8.0000 ETH
[--dry-run] Environment OK. Skipping deploy.
```

Si el balance es < 0.15 ETH el script saldrá con error. Financia con el faucet
Sepolia antes de continuar.

## 4. Deploy real

```bash
python scripts/deploy_eth_mocks_sepolia.py
```

Esperar: ~5-8 minutos de wall-clock (7 transacciones secuenciales, 12 s de block
time en Sepolia). Coste: ~0.05-0.1 ETH gas dependiendo del precio del momento.

El script imprime al final un resumen con los enlaces Etherscan de cada contrato.
Las direcciones se guardan también en `deployments/sepolia.json` para que el
runner de campañas las lea.

## 5. Smoke test — verificar que el deploy está operativo

```bash
python scripts/verify_sepolia_deployment.py
```

Read-only, cuesta 0 ETH. Confirma:
- Todos los contratos tienen bytecode en su dirección (no son EOAs).
- El pool tiene liquidez bootstrap.
- Tornado tiene la profundidad + denominación correctas.
- El operator del bridge está seteado.

## 6. (Opcional) Verificar source code en Etherscan

Una vez los contratos están desplegados, usa Foundry para publicar el source.
Requiere `ETHERSCAN_API_KEY` en `.env.sepolia`.

```bash
# Cargar .env.sepolia en la shell actual
set -a; source .env.sepolia; set +a

# Verificar cada contrato. Reemplaza 0x... con la dirección real de deployments/sepolia.json.
~/.foundry/bin/forge verify-contract \
    --chain sepolia \
    --etherscan-api-key "$ETHERSCAN_API_KEY" \
    0x<MockUSDT_ADDRESS> \
    contracts/MockUSDT.sol:MockUSDT

# El Pool tiene un constructor arg (usdt address) — hay que pasar --constructor-args:
~/.foundry/bin/forge verify-contract \
    --chain sepolia \
    --etherscan-api-key "$ETHERSCAN_API_KEY" \
    --constructor-args $(~/.foundry/bin/cast abi-encode "constructor(address)" 0x<MockUSDT_ADDRESS>) \
    0x<Pool_ADDRESS> \
    contracts/MockUniswapV2Pool.sol:MockUniswapV2Pool

# Tornado: 3 constructor args (verifier, hasher, levels)
~/.foundry/bin/forge verify-contract \
    --chain sepolia \
    --etherscan-api-key "$ETHERSCAN_API_KEY" \
    --constructor-args $(~/.foundry/bin/cast abi-encode "constructor(address,address,uint32)" 0x<Verifier> 0x<MiMC> 10) \
    0x<Tornado_ADDRESS> \
    contracts/MockTornado.sol:MockTornado
```

La verificación sube el source a Sepolia Etherscan para que cualquiera (Chema, los
revisores del TFM) pueda leer el código en
`https://sepolia.etherscan.io/address/0x<ADDR>#code`.

## 7. Siguiente paso — campaña atacante (Task #7)

Una vez `verify_sepolia_deployment.py` pasa:
- Las direcciones de deploy viven en `deployments/sepolia.json`.
- El runner de campaña atacante (Task #7 — script aparte) lee ese JSON y ejecuta
  transacciones reales sobre Sepolia contra el stack desplegado.

## Troubleshooting

**"insufficient funds for gas * price + value"**
La wallet deployer está vacía en Sepolia. Financia por faucet.

**"replacement transaction underpriced"**
Sepolia tenía una tx pendiente de un intento previo. Espera ~30 s y reintenta,
o sube `INTER_TX_SLEEP_S` en el script de 2 a 5.

**"execution reverted" durante bootstrap del pool**
Normalmente significa que el allowance USDT no fue seteado — verificá que las
txs de mint + approve tuvieron éxito en Etherscan antes de la llamada bootstrap.

**"Cannot connect to Sepolia RPC"**
Prueba manualmente:
```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' \
  "$SEPOLIA_RPC_URL"
# esperado: {"jsonrpc":"2.0","id":1,"result":"0xaa36a7"}   (0xaa36a7 = 11155111)
```

**Etherscan verify falla con "Unable to locate ContractCode"**
Espera ~30-60 s tras el deploy para que Etherscan indexe la dirección, luego
reintenta.

**MiMC deploy falla con `node: not found`**
El bytecode de MiMCSponge se genera on-demand por `scripts/zk_helpers.js` que
requiere Node. Ejecuta `bash scripts/install_zk_tools.sh` si es necesario.
