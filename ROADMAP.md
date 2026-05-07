# AML Multi-Agent Thesis — Project Roadmap

**Working title:** *Adversarial Multi-Agent Systems for Cryptocurrency Anti-Money Laundering: Red-Team Generation and Collaborative Detection on Ethereum, with Stablecoin Laundering and Smurfing Detection*

---

## 1. Problem statement

Existing AML detectors for cryptocurrency transactions — predominantly graph neural networks trained on labeled datasets such as Elliptic — are evaluated against historical illicit transactions or simple synthetic adversarial perturbations. Two gaps remain:

1. **Realistic adversarial generation.** Gradient-based attacks produce transaction perturbations that satisfy ML evasion objectives but are not typologically realistic. They do not resemble how real laundering organizations operate: split roles, FATF-recognized patterns (placement / layering / integration), use of mixers, smurfing across multiple controlled wallets, **stablecoin (USDT) denomination — the dominant illicit-flow profile reported by Chainalysis 2024–2025.**
2. **Collaborative detection under privacy constraints, with actor-level clustering.** Real-world AML requires multiple exchanges to share suspicion signals across partial views of the transaction graph, but legal and jurisdictional constraints (GDPR, AML Directive, MiCA) prevent raw data pooling. Most published detectors assume full-graph visibility *and* operate at per-address granularity — flagging individual addresses as illicit. Real laundering uses many short-lived burner wallets per campaign; the detection task is therefore **identifying which addresses belong to the same actor**, not just classifying each address in isolation.

This thesis addresses both gaps by building (a) an LLM-based multi-agent launderer that generates typologically realistic attack patterns on a simulated Ethereum environment — focused on USDT-denominated flows and a real zero-knowledge mixer — and (b) an LLM-based multi-agent collaborative detector that operates over partial graph views, performs actor-level clustering of related wallets (smurfing detection), and respects explicit privacy boundaries between simulated exchange agents.

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

1. **First LLM-multi-agent system generating typologically grounded laundering attacks** on Ethereum (FATF placement-layering-integration roles + multi-wallet smurfing) rather than gradient-based perturbations.
2. **Evaluation against attacks that combine real ZK-mixer use (Tornado-style with Groth16) and stablecoin (USDT-ERC20) laundering** — reflecting the dominant 2024–2025 illicit-flow profile per Chainalysis. Most academic AML literature still focuses on native-Bitcoin transparent flows; stablecoin + privacy-mixer combinations are underrepresented.
3. **First multi-agent collaborative detector to perform actor-level clustering (smurfing / related-wallet identification) under partial graph visibility.** Most published detectors classify addresses individually; the harder and more practically relevant task is recognizing that addresses A, B, C, D belong to the same launderer despite no direct on-chain link, while only seeing a subset of the global transaction graph (the legal-realistic exchange-level view).

---

## 3. Methodology

### 3.1 Environments

| Asset / source | Source | Use |
|---|---|---|
| Ethereum | Local fork via Anvil/Hardhat. Mock contracts deployed on the fork: USDT-shaped ERC-20 (6 decimals), Uniswap-V2-style swap pool (USDT↔ETH), real ZK mixer (Tornado-style: Groth16 verifier + Pedersen commitments + MiMC Merkle tree). | Stablecoin and mixer laundering experiments |
| Bitcoin | Elliptic / Elliptic++ dataset | Train baseline victim detectors only — Bitcoin is **not** an attacker target in this thesis. It serves as the labeled-data foundation for the GNN baselines the LLM attacker tries to evade. |
| Real ETH transactions (optional) | Etherscan archive sampling (free tier) | Realistic background traffic for the defender's exchange-agent training set, mixed with synthetic launderer transactions |

Beyond the chain environments above, a shared **historical price oracle** sources hourly OHLC for ETH and USDT vs USD back to 2018, via CoinGecko's free tier and pre-downloaded to a local CSV cache for reproducibility. The oracle is described as a service in §3.2; both attacker and defender call it as a tool, but it is not itself an LLM agent.

### 3.2 Architecture

**Attacker (Launderer) — LLM multi-agent:**
- *Coordinator* (Claude Opus 4.7): plans the laundering campaign, allocates volume across roles, generates the set of burner wallets used by the campaign (`secrets.token_bytes` + `eth_keys`), observes detector feedback.
- *Placement agent* (Claude Sonnet 4.6): orchestrates initial deposits / on-ramp obfuscation, denominated in USDT where realistic.
- *Layering agent* (Claude Sonnet 4.6): generates peel chains, token swaps (USDT↔ETH for mixer access), ZK-mixer deposits and withdrawals to fresh burner addresses, smurfing patterns sized under FATF USD thresholds.
- *Integration agent* (Claude Sonnet 4.6): selects exit ramps and withdrawal cadence.

**Defender — LLM multi-agent:**
- *N exchange agents* (Claude Sonnet 4.6 each): each sees a partial subgraph (deposits/withdrawals at "their" exchange). Computes per-address suspicion features locally.
- *Coordinator* (Claude Opus 4.7): aggregates suspicion signals across exchanges and performs **actor-level clustering** — i.e. infers which sets of addresses are likely controlled by the same launderer based on common funding origin, temporal co-occurrence, common downstream sinks, gas-price / nonce fingerprints, and graph community structure. Issues alerts at the cluster level, not the address level.
- *Privacy constraint:* exchange agents share only suspicion scores and feature summaries — never raw transaction graphs.

**Victim baseline detectors (off the shelf, used as the attacker's reward signal):**
- GCN, GAT, EvolveGCN trained on Elliptic + AMLworld
- Reproduce published F1 / precision / recall before any attack experiments

**Shared infrastructure (used by both attacker and defender — NOT an LLM agent):**
- *PriceOracle*: deterministic Python service mapping `(asset, timestamp) → USD price`. Backed by a pre-downloaded CoinGecko CSV cache (hourly OHLC for ETH and USDT, 2018–present).
  - Attacker uses it to size each laundering leg under FATF USD thresholds (e.g., the $10k smurfing threshold), choose USDT vs ETH for value-stable holding during layering, and time exit ramps relative to recent price movement.
  - Defender uses it to USD-normalize observed flows across heterogeneous assets and apply USD-denominated suspicion rules.
  - Pricing is treated as **data, not strategy**: the oracle is a Python tool that LLM agents call. We do NOT spend LLM tokens on price lookups, and there is no dedicated "pricing agent" on either side.
  - USDT default: $1.00. Configurable depeg events for ablation.

### 3.3 Cost optimization

- Develop with Haiku 4.5 sub-agents (~10× cheaper than Sonnet)
- Final paper experiments with Sonnet 4.6 sub-agents + Opus 4.7 coordinators
- Aggressive prompt caching on system prompts and tool definitions (~90% input cost reduction on cache hits)
- LLM only at strategic decision points; deterministic Python executes individual transactions
- Estimated total budget: $600–2000 for the full thesis

---

## 4. Timeline (12 weeks)

| Week | Phase | Status |
|---|---|---|
| 1 | Setup + data: conda env, Elliptic + AMLworld downloaded, EDA notebook | ✅ done |
| 2 | Baseline detectors: GCN F1=0.476, GAT F1=0.480 on Elliptic (cleared Weber 2019); PriceOracle service | ✅ done |
| 3 | Ethereum simulator: Anvil + four mock contracts (USDT, Uniswap-pool, Tornado, Bridge) — 26 integration tests passing | ✅ done |
| 4 | ZK Tornado upgrade: replace keccak-mock mixer with real Groth16 + Pedersen + Merkle tree (circom circuit, snarkjs setup, generated Verifier.sol, Python proof wrapper) | in progress |
| 5–6 | Attacker agents v1: Coordinator + 3 sub-agents, multi-burner-wallet smurfing, USDT/ETH swaps, real ZK mixer use, evade single GCN baseline | pending |
| 7 | Attacker evaluation: Attack Success Rate, typology realism scoring, USD-volume-weighted ASR | pending |
| 8–9 | Defender agents: multi-exchange partial-view detector with actor-level clustering (smurfing / related-wallet identification) | pending |
| 10 | End-to-end battles: attacker vs defender, multiple seeds, ablations (with/without mixer use, with/without smurfing-clustering, with/without price-aware timing) | pending |
| 11 | Analysis: cost analysis, error analysis, comparison vs gradient attacks, per-asset breakdown | pending |
| 12 | Writeup | pending |

EvolveGCN baseline is deferred to a separate small PR alongside week 5 (temporal-snapshot data shape is different from static GCN/GAT).

---

## 5. Evaluation metrics

**Attacker side:**
- Attack Success Rate (ASR): proportion of laundering campaigns undetected by victim model.
- **USD-volume-weighted ASR**: ASR weighted by the USD-equivalent volume successfully laundered, so a campaign that moves $1M dominates a campaign that moves $1k. Prevents the headline number from being inflated by many low-stakes successes.
- Typology realism: automated scoring against FATF typologies + spot-check by advisor.
- Cost per successful evasion (USD API spend / successful campaign).

**Defender side:**
- **Address-level detection rate** (vs gradient-baseline attacker, for direct comparison with Egressy 2023 and Cardoso 2022 numbers).
- **Actor-level (cluster) F1**: of the ground-truth launderer-controlled wallet clusters in each campaign, what fraction does the defender's coordinator identify as a single related cluster? This is the headline novelty-3 metric.
- False positive rate on benign transactions and benign address clusters.
- Information sharing efficiency (suspicion signals exchanged per detection).
- USD-equivalent precision/recall: precision/recall weighted by USD value of correctly-flagged flows.

**Comparative:**
- LLM attacker vs PGD-style gradient attacker on the same victim model.
- Multi-agent defender vs single-model GCN baseline (address-level).
- Multi-agent defender vs single-model GCN baseline + post-hoc graph-clustering (cluster-level — this is the meaningful comparison for novelty claim #3).
- Per-asset breakdown (native ETH vs USDT) for laundering volume and detection.
- **Price-aware-timing ablation**: launderer with full PriceOracle access vs prices hidden — does intra-campaign price awareness measurably shift ASR?
- **Smurfing-detection ablation**: defender with vs without the actor-level clustering subroutine — does explicit clustering beat per-address classification + naive grouping?

---

## 6. Ethics and legal scope

- **All work on synthetic data and a local Anvil fork. No real funds on mainnet, ever.**
- Tornado Cash is OFAC-sanctioned in the United States. We deploy a hand-written research mock (mock first, real ZK upgrade in week 4) on the local fork — never the live deployed contracts on mainnet.
- The mock USDT-ERC20 is a research artifact with no relation to the real Tether contract; it is deployed only on the local Anvil fork.
- The MockBridge contract scaffold exists for future-work cross-chain extension (see §8) but is not exercised in v1 experiments.
- Ethics committee filing in week 1. **Action item: confirm with advisor what your university requires (IRB / CEI / equivalent).**
- All attacker code clearly labeled as research artifact; not packaged for redistribution. License: research-use-only (specific license TBD with advisor).

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| 4 GB GPU VRAM (GTX 1650 Ti) insufficient for full Elliptic GNN training | Already confirmed acceptable: GCN trains in 25s, GAT in 220s on full graph at 1000 epochs |
| LLM API costs exceed budget | Strict per-experiment episode caps; Haiku in dev; cached prompts; LLM only at strategic decisions |
| ZK setup (week 4) takes longer than planned | Reuse Hermez phase-1 trusted setup (`powersOfTau28_hez_final_*.ptau`); the Tornado-style circuit is well-documented and we adapt rather than design from scratch; existing keccak-mock mixer remains a fallback if ZK overruns |
| Smurfing detection fails to beat naive baselines | Compare against post-hoc graph-clustering baseline (Louvain, Leiden) on the same partial-view setup; if multi-agent ties or loses, that itself is a publishable negative result |
| "Novelty" claim challenged by reviewers | Maintain explicit prior-work comparison table; pre-register experiment design with advisor before running |
| Reproducibility | All experiments seed-controlled; configs in `experiments/` as YAML; W&B run logging |
| Tornado Cash legal sensitivity | Mock contracts only on local fork; document this prominently; advisor sign-off before week 5 |
| CoinGecko free-tier rate limits or schema changes break experiments | Pre-download all needed historical prices to a versioned local CSV cache; PriceOracle reads from cache, not live API, during experiments |

---

## 8. Future work (out of v1 scope)

The following extensions are explicitly out of scope for this thesis but the architecture is designed so each can be added without rework:

- **Tron simulator + TRC-20 USDT**. Custom event-log replayer + sampled real TRC-20 USDT graph via TronGrid (read-only). Would extend novelty claim #2 to cross-chain stablecoin laundering. The MockBridge contract scaffold is already present (week 3.4) so cross-chain bridge laundering becomes a single-PR addition once the Tron side exists.
- **Bridge laundering as an active layering tactic**. The MockBridge contract is shipped but the launderer agents do not exercise cross-chain transfers in v1. Reactivating this is a Layering-agent-tool addition, no contract work needed.
- **Privacy-coin laundering at the boundary**. Monero, Zcash, and similar privacy-by-default chains defeat on-chain analysis by design — they cannot be modeled as transaction graphs. The realistic research framing is *boundary detection*: an attacker uses ETH→XMR→ETH (via exchanges) as a layering hop; the defender correlates timing and amount across the privacy gap. This is structurally similar to the mixer detection problem already in scope, with extra cross-asset friction.
- **Additional mixer denominations and families**. The current Tornado-style mock is single-denomination. Extending to multi-denomination pools (0.1 / 1 / 10 / 100 ETH) is a natural follow-up; same circuit shape, different parameters.
- **Real-time deployment scenarios**. The current setup runs offline against synthetic and historical data. A live-monitoring deployment (defender agents reading mainnet events as they arrive) is operations-engineering, not research, but a clear practical extension.
