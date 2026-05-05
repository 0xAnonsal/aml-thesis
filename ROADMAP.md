# AML Multi-Agent Thesis — Project Roadmap

**Working title:** *Adversarial Multi-Agent Systems for Cryptocurrency Anti-Money Laundering: Red-Team Generation and Collaborative Detection on Ethereum and Tron*

---

## 1. Problem statement

Existing AML detectors for cryptocurrency transactions — predominantly graph neural networks trained on labeled datasets such as Elliptic — are evaluated against historical illicit transactions or simple synthetic adversarial perturbations. Two gaps remain:

1. **Realistic adversarial generation.** Gradient-based attacks produce transaction perturbations that satisfy ML evasion objectives but are not typologically realistic. They do not resemble how real laundering organizations operate: split roles, FATF-recognized patterns (placement / layering / integration), use of mixers, smurfing across exchanges.
2. **Collaborative detection under privacy constraints.** Real-world AML requires multiple exchanges to share suspicion signals across partial views of the transaction graph, but legal and jurisdictional constraints (GDPR, AML Directive, MiCA) prevent raw data pooling. Most published detectors assume full-graph visibility — an unrealistic assumption.

This thesis addresses both gaps by building (a) an LLM-based multi-agent launderer that generates typologically realistic attack patterns on simulated Ethereum and Tron environments, and (b) an LLM-based multi-agent collaborative detector that operates over partial graph views with explicit privacy boundaries.

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
2. **First evaluation of AML detectors against attacks designed to mimic stablecoin (USDT TRC-20) laundering on Tron** — currently the dominant illicit-flow vector per Chainalysis 2024–2025 reports, but underrepresented in academic AML literature.
3. **Multi-agent collaborative detection across partial-view exchange agents**, modeling the real legal constraint that exchanges cannot share raw transaction data, only aggregated suspicion signals.

---

## 3. Methodology

### 3.1 Environments

| Chain | Source | Use |
|---|---|---|
| Ethereum | Local fork via Anvil/Hardhat + locally deployed mock Tornado Cash | Mixer laundering experiments |
| Tron | Custom event-log simulator + sampled real TRC-20 USDT graph (read-only via TronGrid API) | Stablecoin peel-chain experiments |
| Bitcoin | Elliptic / Elliptic++ dataset | Train baseline detectors only |

### 3.2 Architecture

**Attacker (Launderer) — LLM multi-agent:**
- *Coordinator* (Claude Opus 4.7): plans the laundering campaign, allocates volume across roles, observes detector feedback
- *Placement agent* (Claude Sonnet 4.6): orchestrates initial deposits / on-ramp obfuscation
- *Layering agent* (Claude Sonnet 4.6): generates peel chains, mixer interactions, smurfing patterns
- *Integration agent* (Claude Sonnet 4.6): selects exit ramps, withdrawal cadence

**Defender — LLM multi-agent:**
- *N exchange agents* (Claude Sonnet 4.6 each): each sees a partial subgraph (deposits/withdrawals at "their" exchange)
- *Coordinator* (Claude Opus 4.7): aggregates suspicion signals, issues alerts
- *Privacy constraint:* agents share signal scores and alert flags, NEVER raw transaction graphs

**Victim baseline detectors (off the shelf, used as the attacker's reward signal):**
- GCN, GAT, EvolveGCN trained on Elliptic + AMLworld
- Reproduce published F1 / precision / recall before any attack experiments

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
| 3 | Ethereum simulator | Anvil fork + Tornado mock, transaction injection API |
| 4 | Tron simulator | Custom event-log replayer, TRC-20 USDT graph loaded |
| 5–6 | Attacker agents v1 | Coordinator + 3 sub-agents, evade single GCN baseline |
| 7 | Attacker evaluation | Attack success rate, typology realism scoring |
| 8–9 | Defender agents | Multi-exchange partial-view detector |
| 10 | End-to-end battles | Attacker vs defender, multiple seeds, ablations |
| 11 | Analysis | Cost analysis, error analysis, comparison vs gradient attacks |
| 12 | Writeup | Thesis chapter draft |

---

## 5. Evaluation metrics

**Attacker side:**
- Attack Success Rate (ASR): proportion of laundering campaigns undetected by victim model
- Typology realism: automated scoring against FATF typologies + spot-check by advisor
- Cost per successful evasion (USD API spend / successful campaign)

**Defender side:**
- Detection rate against LLM attacker (vs gradient-baseline attacker)
- False positive rate on benign transactions
- Information sharing efficiency (suspicion signals exchanged per detection)

**Comparative:**
- LLM attacker vs PGD-style gradient attacker on the same victim model
- Multi-agent defender vs single-model GCN baseline
- Per-chain breakdown (Ethereum vs Tron) for both attacker and defender

---

## 6. Ethics and legal scope

- **All work on synthetic data and forked / local test chains. No real funds on mainnet, ever.**
- Tornado Cash is OFAC-sanctioned in the United States. Interaction is with locally deployed mock contracts on a forked chain only — never the live deployed contracts on mainnet.
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
| "Novelty" claim challenged by reviewers | Maintain explicit prior-work comparison table; pre-register experiment design with advisor before running |
| Reproducibility | All experiments seed-controlled; configs in `experiments/` as YAML; W&B run logging |
| Tornado Cash legal sensitivity | Mock contracts only; document this prominently; advisor sign-off before week 3 |
