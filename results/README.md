# results/ — de dónde sale cada cifra del TFM

Todos los ficheros JSON de este directorio están bajo control de versiones y son la fuente de las tablas del
Capítulo 5 del TFM (`tfm/TFM_SalehSinawi.docx`). Los directorios de campaña (`sepolia_campaign/`, `anvil/`,
`anvil_option_a/`, `archive/`) contienen los artefactos de cada corrida del atacante (`campaign.json`, `meta.json`,
`summary.txt`, `chain_trace.jsonl`, `addresses.json`); los `wallets_keys.jsonl` de testnet están gitignorados.

| Fichero(s) | Script | Tabla / sección del TFM |
|---|---|---|
| `loco_simulation_precision.json` | `scripts/loco_simulation_precision.py` | Tablas 9-10, §5.10 (split 80/20 por corrida y LOCO con FPR en campañas benignas) |
| `ethereum_heist_baselines.json`, `loco_ethereum_heist.json` | `scripts/train_ethereum_heist.py`, `scripts/loco_ethereum_heist.py` | Tabla 11 (Random Forest de 8 features, split y leave-one-heist-out) |
| `elliptic_pp_baselines.json`, `openaml_v1_baselines.json` | `scripts/baseline_elliptic_pp.py`, `scripts/baseline_openaml_v1.py` | Tabla 12 (sanity check tabular RF/LR) |
| `gcn_baseline.json`, `gat_baseline.json`, `gcn_baseline_v1.json` | `scripts/train_baseline.py` | §5.5.4 (GCN/GAT sobre el grafo Elliptic original) |
| `eval_llm_defender_{haiku,sonnet}.json`, `eval_llm_defender_heist_*_no_upbit.json` | `scripts/eval_llm_defender.py`, `scripts/eval_llm_defender_heist.py` | Tablas 13-14, §5.6 (coseno frente a LLM) |
| `llm_defender_ethereum_heist.json`, `p171_posthoc_ethereum_heist.json` | `scripts/eval_ethereum_heist_llm.py`, `scripts/reps/p171_posthoc.py` (heist) | §5.9.5 (RF de 4 features reentrenado sobre EthereumHeist + P1-71/73) |
| `cross_eval_baselines_our_datasets.json`, `hard_neg_eval_*.json`, `llm_defender_*_haiku.json` | `scripts/hard_negative_eval.py` | Tablas 17-18, §5.9.3-5.9.4 (Louvain/GCN sobre los 5 datasets con corpus benigno; `bg_fpr` = fondo sin etiqueta marcado) |
| `p171_posthoc_*_{haiku,sonnet}.json` + `reps/rep{2,3}_p171_posthoc_*.json` | `scripts/reps/p171_posthoc.py`, `scripts/reps/run_reps.sh` | Tablas 20-22 (media ± σ de 3 ejecuciones: `reps/aggregate.json`) |
| `p171_heldout_anvil_90{0,1}_haiku.json` + `reps/rep*_p171_heldout_*` | `scripts/reps/p171_heldout_eval.py` | Tabla 23 (held-out) |
| `multi_campaign_loco_haiku.json`, `campaign_id_loco_haiku.json` + `reps/` | `scripts/reps/multi_campaign_loco_eval.py`, `scripts/reps/campaign_id_loco_eval.py` | Tabla 24 y §5.9.8 (prueba de carga; no es LOCO) |
| `feature_ablation_sepolia_803_haiku.json` + `reps/` | `scripts/reps/feature_ablation_eval.py` | Tablas 25-26 |
| `reps/aggregate.json` | `scripts/reps/aggregate_reps.py` | Medias ± σ de todo lo anterior |
| `eval_defender_seed403_comparison.json`, `eval_detection_seed403.json` | `scripts/eval_llm_defender_seed403.py` | §5.9.7 (cinco configuraciones del defensor, Opus incluido) |
| `sepolia_campaign/*/summary.txt`, `anvil*/*/summary.txt` | `scripts/run_sepolia_campaign.py`, `aml.attackers.run_campaign` | Tabla 16 (coste LLM y *honest recovery* por campaña) |
| `feature_separation_full_graph.json`, `mixer_shortcut_check.json` | `scripts/feature_separation_check.py`, `scripts/mixer_shortcut_check.py` | §5.9.4 y §5.10 (separación por feature con d de Cohen y regla «mixer ⇒ atacante», calculadas sobre el grafo completo como las ve el GCN; corrigen las comprobaciones 2 y 4 de `audit_f1_memorization.json`) |

## Ficheros superados (se conservan por trazabilidad)

- `audit_f1_memorization.json` — primera auditoría LOCO (pickle anterior de 10 campañas atacantes + 10 benignas). Su
  media de F1 (0,42) incluía los 10 folds benignos, donde F1 = 0 por construcción; el protocolo corregido está en
  `loco_simulation_precision.json` (§5.10, Hallazgo 1). Sus comprobaciones 2 (d de Cohen por feature) y 4 (regla
  «mixer ⇒ atacante») calculaban los features solo sobre los nodos etiquetados, y `extract_features` descarta las
  aristas con un extremo fuera de esa lista (los contratos no están etiquetados), por lo que las columnas `mixer_*` y
  `swap_*` salían a cero; la versión correcta, sobre el grafo completo, está en `feature_separation_full_graph.json` y
  `mixer_shortcut_check.json`.
- `loco_simulation_3detectors.json` — ejecución con un error de script (los tres detectores devuelven valores
  idénticos); no se cita en el TFM.
- `eval_sepolia_*seed100/500*.json`, `eval_sepolia_gcn_experiments.json` — evaluaciones de las campañas de
  validación 100 y 500, anteriores a las oficiales.
