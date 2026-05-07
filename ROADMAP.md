# AML Multi-Agent Thesis — Project Roadmap

**Working title:** *Adversarial Multi-Agent Systems for Cryptocurrency Anti-Money Laundering: Red-Team Generation and Collaborative Detection on Ethereum and Tron, with a Stablecoin and Cross-Chain Focus*

---

## 1. Problem statement

Existing AML detectors for cryptocurrency transactions — predominantly graph neural networks trained on labeled datasets such as Elliptic — are evaluated against historical illicit transactions or simple synthetic adversarial perturbations. Two gaps remain:

1. **Realistic adversarial generation.** Gradient-based attacks produce transaction perturbations that satisfy ML evasion objectives but are not typologically realistic. They do not resemble how real laundering organizations operate: split roles, FATF-recognized patterns (placement / layering / integration), use of mixers, smurfing across exchanges, **cross-chain bridge layering, and stablecoin (USDT) denomination — the dominant illicit-flow profile reported by Chainalysis 2024–2025.**
2. **Collaborative detection under privacy constraints.** Real-world AML requires multiple exchanges to share suspicion signals across partial views of the transaction graph, but legal and jurisdictional constraints (GDPR, AML Directive, MiCA) prevent raw data pooling. Most published detectors assume full-graph visibility — an unrealistic assumption. **In a multi-chain world, the same constraint extends across chains: bridge-mediated laundering can only be flagged if exchange-level signals on Ethereum and Tron are correlated without raw graph sharing.**

This thesis addresses both gaps by building (a) an LLM-based multi-agent launderer that generates typologically realistic attack patterns on simulated Ethereum and Tron environments — with explicit support for stablecoin (USDT) flows on both chains and cross-chain bridge layering — and (b) an LLM-based multi-agent collaborative detector that operates over partial graph views with explicit privacy boundaries and correlates suspicion signals across chains.

---

## 2. Novelty (the *novedad* requirement)

### Prior work / baselines to compare against

| Reference | Contribution | Used as |
|---|---|---|
| Weber et al. 2019 (*Anti-Money Laundering in Bitcoin*) | Elliptic dataset + GCN baseline | Victim detector |
| Pareja et al. 2020 (*EvolveGCN*) | Temporal GNN for evolving graphs | Victim detector |
| Cardoso et al. 2022 | AML GNN benchmarks on Bitcoin | Methodology reference |
| Egressy et al. 2023 | Adversarial attacks on AML GNNs (gradient-based) | Attacker baseline |
| Altman et al. 2023 (*IBM AMLworld / AMLSim*) | Synthetic AML simulator with ground truth | Environment |

### Specific novelty claims

1. **First LLM-multi-agent system generating typologically grounded laundering attacks** (FATF placement-layering-integration roles) on Ethereum and Tron specifically, rather than gradient-based perturbations.
2. **First evaluation of AML detectors against attacks mimicking realistic stablecoin (USDT) laundering across both ERC-20 (Ethereum) and TRC-20 (Tron), including cross-chain bridge layering** — the dominant illicit-flow profile per Chainalysis 2024–2025 reports, but underrepresented in academic AML literature, which has been dominated by native-asset Bitcoin work.
3. **Multi-agent collaborative detection across partial-view exchange agents on both Ethereum and Tron, with explicit cross-chain correlation of suspicion signals via bridge events** — modeling the real legal constraint that exchanges cannot share raw transaction data, only aggregated suspicion signals, and extending that constraint across jurisdictions and chains.

---

## 3. Methodology

### 3.1 Environments

| Chain | Source | Use |
|---|---|---|
| Ethereum | Local fork via Anvil/Hardhat. Mock contracts deployed on the fork: USDT-shaped ERC-20 (6 decimals), Uniswap-V2-style swap pool (USDT↔ETH), mock Tornado Cash mixer, minimal lock-and-mint bridge contract. | Stablecoin, mixer, and cross-chain layering experiments |
| Tron | Custom event-log simulator + sampled real TRC-20 USDT graph (read-only via TronGrid API). Bridge "mint" events injected as the Tron-side leg of cross-chain transfers. | Stablecoin peel-chain + cross-chain destination experiments |
| Bitcoin | Elliptic / Elliptic++ dataset | Train baseline victim detectors only — Bitcoin is **not** an attacker target in this thesis. It serves as the labeled-data foundation for the GNN baselines the LLM attacker tries to evade. |

Beyond the chain environments above, a shared **historical price oracle** sources hourly OHLC for ETH, TRX, and USDT vs USD back to 2018, via CoinGecko's free tier and pre-downloaded to a local CSV cache for reproducibility. The oracle is described as a service in §3.2; both attacker and defender call it as a tool, but it is not itself an LLM agent.

### 3.2 Architecture

**Attacker (Launderer) — LLM multi-agent:**
- *Coordinator* (Claude Opus 4.7): plans the laundering campaign, allocates volume across roles, chooses which chain(s) to operate on, observes detector feedback
- *Placement agent* (Claude Sonnet 4.6): orchestrates initial deposits / on-ramp obfuscation, denominated in USDT where realistic
- *Layering agent* (Claude Sonnet 4.6): generates peel chains, token swaps (USDT↔ETH for mixer access), mixer interactions, **cross-chain bridge transfers (USDT-ERC20 → USDT-TRC20 and back)**, smurfing patterns across exchanges and chains
- *Integration agent* (Claude Sonnet 4.6): selects exit ramps, withdrawal cadence, choice of chain on which to cash out

**Defender — LLM multi-agent:**
- *N exchange agents* (Claude Sonnet 4.6 each): each sees a partial subgraph (deposits/withdrawals at "their" exchange on a specific chain)
- *Coordinator* (Claude Opus 4.7): aggregates suspicion signals across exchanges and chains, **correlates ERC-20 ↔ TRC-20 USDT flows via observed bridge lock/mint events**, issues alerts
- *Privacy constraint:* agents share signal scores and alert flags, NEVER raw transaction graphs. Bridge-event timestamps and amounts are treated as a public observable both sides can independently see.

**Victim baseline detectors (off the shelf, used as the attacker's reward signal):**
- GCN, GAT, EvolveGCN trained on Elliptic + AMLworld
- Reproduce published F1 / precision / recall before any attack experiments

**Shared infrastructure (used by both attacker and defender — NOT an LLM agent):**
- *PriceOracle*: deterministic Python service mapping `(asset, timestamp) → USD price`. Backed by a pre-downloaded CoinGecko CSV cache (hourly OHLC for ETH, TRX, USDT, 2018–present).
  - Attacker uses it to size each laundering leg under FATF USD thresholds (e.g., the $10k smurfing threshold), choose USDT vs ETH for value-stable holding during layering, and time exit ramps relative to recent price movement.
  - Defender uses it to USD-normalize observed flows across heterogeneous assets, apply USD-denominated suspicion rules, and detect cross-asset smurfing patterns that would be invisible if measured in native units.
  - Pricing is treated as **data, not strategy**: the oracle is a Python tool that LLM agents call. We do NOT spend LLM tokens on price lookups, and there is no dedicated "pricing agent" on either side.
  - USDT default: $1.00. Configurable depeg events for ablation (e.g., the March 2023 USDC depeg that briefly dragged USDT off-peg).

### 3.3 Cost optimization

- Develop with Haiku 4.5 sub-agents (~10× cheaper than Sonnet)
- Final paper experiments with Sonnet 4.6 sub-agents + Opus 4.7 coordinators
- Aggressive prompt caching on system prompts and tool definitions (~90% input cost reduction on cache hits)
- LLM only at strategic decision points; deterministic Python executes individual transactions
- Estimated total budget: $600–2000 for the full thesis

---

## 4. Timeline (12 weeks)

| Week | Phase | Deliverable |
|---|---|---|
| 1 | Setup + data | conda env operational, Elliptic + AMLworld downloaded, initial EDA notebook |
| 2 | Baseline detectors | Reproduce GCN / GAT / EvolveGCN F1 on Elliptic |
| 3 | Ethereum simulator | Anvil fork + mock USDT-ERC20 + Uniswap-style swap mock + Tornado mock + minimal lock-and-mint bridge mock, transaction injection API |
| 4 | Tron simulator | Custom event-log replayer, TRC-20 USDT graph loaded, bridge-mint event hooks aligned with the ETH-side bridge mock |
| 5–6 | Attacker agents v1 | Coordinator + 3 sub-agents, evade single GCN baseline; layering tactics include swaps, mixer use, and cross-chain bridge transfers |
| 7 | Attacker evaluation | Attack success rate, typology realism scoring, cross-chain vs single-chain ablation |
| 8–9 | Defender agents | Multi-exchange partial-view detector with cross-chain bridge-event correlation in the coordinator |
| 10 | End-to-end battles | Attacker vs defender, multiple seeds, ablations (with/without cross-chain correlation; with/without mixer use) |
| 11 | Analysis | Cost analysis, error analysis, comparison vs gradient attacks, per-chain and cross-chain breakdowns |
| 12 | Writeup | Thesis chapter draft |

---

## 5. Evaluation metrics

**Attacker side:**
- Attack Success Rate (ASR): proportion of laundering campaigns undetected by victim model
- **USD-volume-weighted ASR**: ASR weighted by the USD-equivalent volume successfully laundered, so a campaign that moves $1M of ETH dominates a campaign that moves $1k of TRX. Prevents the headline number from being inflated by many low-stakes successes.
- Typology realism: automated scoring against FATF typologies + spot-check by advisor
- Cost per successful evasion (USD API spend / successful campaign)
- Cross-chain ASR uplift: ASR using cross-chain bridge layering vs single-chain campaigns of equivalent volume

**Defender side:**
- Detection rate against LLM attacker (vs gradient-baseline attacker)
- False positive rate on benign transactions
- Information sharing efficiency (suspicion signals exchanged per detection)
- Cross-chain detection rate: proportion of bridge-mediated laundering campaigns flagged by the inter-chain coordinator vs the same defender with cross-chain correlation disabled (ablation)
- **USD-equivalent detection precision and recall**: standard precision/recall weighted by USD value of the flagged flows, so correctly catching $1M is worth more than correctly catching $100. Prevents the headline number from being padded by easy small-volume catches.

**Comparative:**
- LLM attacker vs PGD-style gradient attacker on the same victim model
- Multi-agent defender vs single-model GCN baseline
- Per-chain breakdown (Ethereum vs Tron) for both attacker and defender
- Per-asset breakdown (native ETH/TRX vs USDT) for laundering volume and detection
- **Price-aware-timing ablation**: launderer with full PriceOracle access vs launderer with prices hidden — does intra-campaign price awareness measurably shift ASR, or does the launderer move fast enough that ETH/TRX volatility is irrelevant on the timescale of a single campaign?

---

## 6. Ethics and legal scope

- **All work on synthetic data and forked / local test chains. No real funds on mainnet, ever.**
- Tornado Cash is OFAC-sanctioned in the United States. Interaction is with locally deployed mock contracts on a forked chain only — never the live deployed contracts on mainnet.
- Bridges and DEX swaps are simulated by minimal mock contracts on the local fork. The thesis does **not** interact with deployed bridge protocols (e.g., Wormhole, Stargate, deBridge) or DEX protocols on mainnet.
- The mock USDT-ERC20 is a research artifact with no relation to the real Tether contract; it is deployed only on the local Anvil fork.
- TRC-20 USDT data is read-only sampling for graph structure; no transactions are submitted to Tron mainnet.
- Ethics committee filing in week 1. **Action item: confirm with advisor what your university requires (IRB / CEI / equivalent).**
- All attacker code clearly labeled as research artifact; not packaged for redistribution. License: research-use-only (specific license TBD with advisor).

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| 4 GB GPU VRAM (GTX 1650 Ti) insufficient for full Elliptic GNN training | Use `NeighborSampler` mini-batching; smaller hidden dims; fall back to Colab / Kaggle for final runs |
| LLM API costs exceed budget | Strict per-experiment episode caps; Haiku in dev; cached prompts; LLM only at strategic decisions |
| AMLworld lacks Ethereum / Tron support natively | Build minimal chain-specific simulators (in scope, weeks 3–4) |
| Bridge mock too simplified to be representative | Document mock semantics explicitly (lock-and-mint, no validator set, no fees); position cross-chain layering as a *typology-level* contribution, not a bridge-protocol-security contribution |
| "Novelty" claim challenged by reviewers | Maintain explicit prior-work comparison table; pre-register experiment design with advisor before running |
| Reproducibility | All experiments seed-controlled; configs in `experiments/` as YAML; W&B run logging |
| Tornado Cash legal sensitivity | Mock contracts only; document this prominently; advisor sign-off before week 3 |
| CoinGecko free-tier rate limits or schema changes break experiments | Pre-download all needed historical prices to a versioned local CSV cache; PriceOracle reads from cache, not live API, during experiments. Live calls only for one-off backfill. |
