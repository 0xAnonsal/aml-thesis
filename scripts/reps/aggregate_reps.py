"""Aggregate the repeated LLM-defender evaluations into mean ± std.

rep1 = the original JSONs in results/ (the ones the thesis tables were built from);
rep2, rep3 = results/reps/rep{N}_*.json written by scripts/reps/run_reps.sh.

Output: results/reps/aggregate.json + a printed summary (values as in the thesis tables).
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "results"
REPS = R / "reps"
DATASETS = ["sepolia_800", "sepolia_802", "sepolia_803", "anvil_830", "anvil_850"]


def load(name: str):
    """Return the list of available repetitions (rep1 from results/, others from results/reps/)."""
    out = []
    p = R / f"{name}.json"
    if p.exists():
        out.append(json.load(open(p)))
    for k in (2, 3, 4, 5):
        q = REPS / f"rep{k}_{name}.json"
        if q.exists():
            out.append(json.load(open(q)))
    return out


def ms(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    if not xs:
        return None
    return {"mean": round(st.mean(xs), 4), "std": round(st.stdev(xs), 4) if len(xs) > 1 else 0.0,
            "n": len(xs), "values": [round(x, 4) for x in xs]}


def sweep_val(d, key, field="ari"):
    sw = d.get("sweep") or (d.get("phase2") or {}).get("sweep") or {}
    v = sw.get(key)
    if isinstance(v, dict):
        return v.get(field)
    return v


def sil_val(d, field="ari"):
    s = d.get("silhouette_auto") or d.get("silhouette") or (d.get("phase2") or {}).get("silhouette_auto") or (d.get("phase2") or {}).get("silhouette") or {}
    return s.get(field) if isinstance(s, dict) else None


agg = {"posthoc": {}, "feature_ablation": {}, "heldout": {}, "multi_campaign": {}, "campaign_id": {}}

# ---- Tabla 20 / 21 / 22: P1-71 sweep + P1-73 silhouette per dataset, Haiku and Sonnet ----
for model in ("haiku", "sonnet"):
    per = {}
    for ds in DATASETS:
        reps = load(f"p171_posthoc_{ds}_{model}")
        per[ds] = {
            "n_reps": len(reps),
            "baseline": ms([sweep_val(d, "baseline") for d in reps]),
            "max_3": ms([sweep_val(d, "max_3") for d in reps]),
            "max_5": ms([sweep_val(d, "max_5") for d in reps]),
            "max_8": ms([sweep_val(d, "max_8") for d in reps]),
            "max_12": ms([sweep_val(d, "max_12") for d in reps]),
            "max_20": ms([sweep_val(d, "max_20") for d in reps]),
            "silhouette": ms([sil_val(d) for d in reps]),
            "silhouette_k": [sil_val(d, "picked_k") for d in reps],
            "cost_usd": ms([(d.get("llm") or {}).get("cost_usd") or d.get("cost_usd") for d in reps]),
        }
    # dataset-mean per repetition index (so the "Mean" row has its own std)
    def mean_row(key):
        n = max(p["n_reps"] for p in per.values())
        rows = []
        for i in range(n):
            vals = [per[ds][key]["values"][i] for ds in DATASETS if per[ds][key] and i < len(per[ds][key]["values"])]
            if len(vals) == len(DATASETS):
                rows.append(st.mean(vals))
        return ms(rows)
    per["MEAN"] = {k: mean_row(k) for k in ("baseline", "max_3", "max_5", "silhouette")}
    agg["posthoc"][model] = per

# EthereumHeist (single run, not repeated) for the Tabla 21 six-dataset means
heist = json.load(open(R / "p171_posthoc_ethereum_heist.json")) if (R / "p171_posthoc_ethereum_heist.json").exists() else None
if heist:
    agg["posthoc"]["ethereum_heist_single"] = {k: sweep_val(heist, k) for k in ("baseline", "max_3", "max_5", "max_8", "max_12", "max_20")} | {"silhouette": sil_val(heist), "silhouette_k": sil_val(heist, "picked_k")}

# ---- Tabla 26: feature ablation (sepolia_803, Haiku) ----
reps = load("feature_ablation_sepolia_803_haiku")
fa = {}
for cfg in ("all_19", "top_10", "top_5"):
    blocks = [(d.get("ablations") or d).get(cfg) or {} for d in reps]
    fa[cfg] = {"baseline": ms([b.get("baseline_ari") for b in blocks]),
               "max_3": ms([b.get("max_c_3_ari") for b in blocks]),
               "silhouette": ms([b.get("silhouette_ari") for b in blocks])}
fa["n_reps"] = len(reps)
agg["feature_ablation"] = fa

# ---- Tabla 23: held-out 900/901 (Haiku) ----
for lab in ("anvil_900", "anvil_901"):
    reps = load(f"p171_heldout_{lab}_haiku")
    ph1 = [d.get("phase1") or {} for d in reps]
    agg["heldout"][lab] = {"n_reps": len(reps),
                          "louvain_f1": ms([p.get("louvain_f1") or p.get("f1") for p in ph1]),
                          "precision": ms([p.get("precision") for p in ph1]),
                          "recall": ms([p.get("recall") for p in ph1]),
                          "silhouette": ms([sil_val(d) for d in reps]),
                          "silhouette_k": [sil_val(d, "picked_k") for d in reps],
                          "max_3": ms([sweep_val(d, "max_3") for d in reps])}

# ---- Tabla 24: multi-campaign load test (Haiku) ----
reps = load("multi_campaign_loco_haiku")
ph1 = [d.get("phase1") or {} for d in reps]
agg["multi_campaign"] = {"n_reps": len(reps), "louvain_f1": ms([p.get("louvain_f1") for p in ph1]),
                         "precision": ms([p.get("precision") for p in ph1]), "recall": ms([p.get("recall") for p in ph1]),
                         "silhouette": ms([sil_val(d) for d in reps]), "silhouette_k": [sil_val(d, "picked_k") for d in reps],
                         "max_3": ms([sweep_val(d, "max_3") for d in reps])}

# ---- campaign-id variant (role vs campaign ground truth) ----
reps = load("campaign_id_loco_haiku")
agg["campaign_id"] = {"n_reps": len(reps),
                      "max_3_role": ms([sweep_val(d, "max_3", "ari_role") for d in reps]),
                      "max_3_camp": ms([sweep_val(d, "max_3", "ari_camp") for d in reps]),
                      "sil_role": ms([sil_val(d, "ari_role") for d in reps]),
                      "sil_camp": ms([(sil_val(d, "ari_camp") if sil_val(d, "ari_camp") is not None else sil_val(d, "ari_campaign")) for d in reps]),
                      "sil_k": [sil_val(d, "picked_k") for d in reps]}

REPS.mkdir(exist_ok=True)
(REPS / "aggregate.json").write_text(json.dumps(agg, indent=2))


def f(x):
    return "—" if not x else f"{x['mean']:.3f} ± {x['std']:.3f} (n={x['n']})"


print("=== Tabla 20 (Haiku) ===")
for ds in DATASETS + ["MEAN"]:
    p = agg["posthoc"]["haiku"][ds]
    print(f"{ds:12s} baseline {f(p['baseline'])} | max_3 {f(p['max_3'])} | max_5 {f(p['max_5'])} | silhouette {f(p['silhouette'])} k={p.get('silhouette_k')}")
print("=== Tabla 22 (Sonnet) ===")
for ds in DATASETS + ["MEAN"]:
    p = agg["posthoc"]["sonnet"][ds]
    print(f"{ds:12s} baseline {f(p['baseline'])} | max_3 {f(p['max_3'])} | max_5 {f(p['max_5'])} | silhouette {f(p['silhouette'])}")
print("=== Tabla 26 (feature ablation 803) ===")
for cfg in ("all_19", "top_10", "top_5"):
    print(f"{cfg:8s} baseline {f(fa[cfg]['baseline'])} | max_3 {f(fa[cfg]['max_3'])} | silhouette {f(fa[cfg]['silhouette'])}")
print("=== Tabla 23 (held-out) ===")
for lab in ("anvil_900", "anvil_901"):
    h = agg["heldout"][lab]
    print(f"{lab} F1 {f(h['louvain_f1'])} P {f(h['precision'])} R {f(h['recall'])} | silhouette {f(h['silhouette'])} k={h['silhouette_k']} | max_3 {f(h['max_3'])}")
print("=== Tabla 24 (multi-campaign) ===")
m = agg["multi_campaign"]
print(f"F1 {f(m['louvain_f1'])} P {f(m['precision'])} R {f(m['recall'])} | silhouette {f(m['silhouette'])} k={m['silhouette_k']} | max_3 {f(m['max_3'])}")
c = agg["campaign_id"]
print(f"campaign_id: max_3 role {f(c['max_3_role'])} camp {f(c['max_3_camp'])} | sil role {f(c['sil_role'])} camp {f(c['sil_camp'])} k={c['sil_k']}")
print("Wrote", REPS / "aggregate.json")
