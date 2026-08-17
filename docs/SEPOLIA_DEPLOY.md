# Sepolia deploy — TFM external validation runbook

End-to-end guide to deploy the mock AML contract stack on Ethereum Sepolia
testnet. This is Task #4 in the TFM plan and unblocks Task #7 (6-8h attacker
campaign, main external validation for chapter 5).

**Total time budget**: ~45-60 min once you have the 3 keys ready.

## 0. Prerequisites — keys you need

Have all three ready BEFORE starting. Missing any of them will block
mid-deploy and you'll waste Sepolia ETH on retries.

| Key                          | Where to get it                                          | Time  |
|------------------------------|----------------------------------------------------------|-------|
| Sepolia RPC URL              | https://dashboard.alchemy.com (Create App -> Sepolia)    | 5 min |
| Deployer private key         | MetaMask -> Account details -> Show private key          | 1 min |
| Etherscan API key (optional) | https://etherscan.io/myapikey                             | 3 min |

**SECURITY**:
- Never paste the private key into chat, screenshots, logs, or Git.
- The `.env.sepolia` file is gitignored. Keep it that way.
- Even though Sepolia ETH has no dollar value, treat the key like mainnet
  so you build safe habits.

## 1. Local setup (one-time)

```bash
cd ~/aml-thesis

# Confirm Foundry is installed
~/.foundry/bin/forge --version   # should print v1.7.0 or newer

# Confirm Python deps
conda activate aml-thesis
python -c "from aml.chains.mimc import deploy_mimc; print('ok')"

# Ensure ZK verifier is built (contracts/Verifier.sol is per-machine)
ls contracts/Verifier.sol || bash scripts/setup_zk.sh withdraw

# Compile all contracts
~/.foundry/bin/forge build
```

## 2. Populate .env.sepolia

```bash
cp .env.sepolia.example .env.sepolia
# Edit .env.sepolia with your text editor of choice.
# Fill: SEPOLIA_RPC_URL, SEPOLIA_DEPLOYER_PRIVATE_KEY, ETHERSCAN_API_KEY (optional)
```

## 3. Dry-run — verify environment without spending gas

```bash
python scripts/deploy_eth_mocks_sepolia.py --dry-run
```

Expected output:
```
Sepolia RPC:        https://eth-sepolia.g.alchemy.com/v2/*** (chain_id=11155111)
Deployer:           0x54539B5ef33cfC3C57b9b572fc77d1e5F1CFf4c4
Balance:            8.0000 ETH
[--dry-run] Environment OK. Skipping deploy.
```

If balance < 0.15 ETH the script will exit. Fund via Sepolia faucet before proceeding.

## 4. Real deploy

```bash
python scripts/deploy_eth_mocks_sepolia.py
```

Expected: ~5-8 minutes wall-clock (7 sequential txs, 12s Sepolia block time).
Cost: ~0.05-0.1 ETH gas depending on Sepolia gas price.

The script prints a summary at the end with Etherscan links for each contract.
Addresses are also saved to `deployments/sepolia.json` for the campaign runner.

## 5. Smoke test — verify deployment is operational

```bash
python scripts/verify_sepolia_deployment.py
```

Read-only, costs 0 ETH. Confirms:
- All contracts have bytecode at their address (not just EOAs)
- Pool has bootstrap liquidity
- Tornado has correct depth + denomination
- Bridge operator is set

## 6. (Optional) Verify source code on Etherscan

Once contracts are deployed, use Foundry to publish source. Requires
`ETHERSCAN_API_KEY` in `.env.sepolia`.

```bash
# Load .env.sepolia into current shell
set -a; source .env.sepolia; set +a

# Verify each contract. Replace 0x... with real address from deployments/sepolia.json.
~/.foundry/bin/forge verify-contract \
    --chain sepolia \
    --etherscan-api-key "$ETHERSCAN_API_KEY" \
    0x<MockUSDT_ADDRESS> \
    contracts/MockUSDT.sol:MockUSDT

# Pool has a constructor arg (usdt address) — need constructor-args:
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

Verification uploads the source to Sepolia Etherscan so anyone (Chema, TFM
reviewers) can read the code at
`https://sepolia.etherscan.io/address/0x<ADDR>#code`.

## 7. Next step — attacker campaign (Task #7)

Once `verify_sepolia_deployment.py` passes:
- Deployment addresses live in `deployments/sepolia.json`
- The 6-8h attacker campaign runner (Task #7 — separate script) will read
  that JSON and drive real Sepolia txs through the deployed stack.

## Troubleshooting

**"insufficient funds for gas * price + value"**
The deployer wallet is empty on Sepolia. Fund it via faucet.

**"replacement transaction underpriced"**
Sepolia had a pending tx from a previous attempt. Wait ~30s and retry, or
bump `INTER_TX_SLEEP_S` in the script from 2 to 5.

**"execution reverted" during pool bootstrap**
Usually means USDT allowance wasn't set — verify the mint + approve txs
succeeded on Etherscan before the bootstrap call.

**"Cannot connect to Sepolia RPC"**
Test manually:
```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' \
  "$SEPOLIA_RPC_URL"
# expected: {"jsonrpc":"2.0","id":1,"result":"0xaa36a7"}   (0xaa36a7 = 11155111)
```

**Etherscan verify fails with "Unable to locate ContractCode"**
Wait ~30-60s after deploy for Etherscan to index the address, then retry.

**MiMC deploy fails with `node: not found`**
The MiMCSponge bytecode is generated on-demand by `scripts/zk_helpers.js`
which requires Node. Run `bash scripts/install_zk_tools.sh` if needed.
