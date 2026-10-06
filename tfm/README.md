# TFM — material público

Trabajo Fin de Máster: **"Detección adversarial multi-agente de blanqueo de capitales en Ethereum: simulación y detección basadas en agentes LLM bajo visibilidad parcial federada"** (Máster en Tecnologías del Sector Financiero: Fintech, UC3M, 2025-2026).

## Qué hay aquí

- `chapters_v3/` — notas de trabajo por capítulo en Markdown (estructura UC3M: seis capítulos más anexos). Son las notas técnicas de las que salió la primera versión de la memoria con `scratchpad/md_to_docx.py`; van por detrás de la memoria entregada y no deben usarse como fuente de cifras.
- `figures/` — figuras de la memoria. Se regeneran con los scripts de `scripts/figures/` y `scratchpad/render_subgraph_v2.py` (Figura 4).
- `references.bib` — bibliografía en BibTeX de una versión anterior (33 entradas). La memoria final tiene 44 referencias numeradas por orden de primera aparición.
- `references_audit.md` — verificación de cada referencia.

## Qué no hay aquí

La memoria entregada (`.docx`) y los borradores anteriores de capítulos están en un repositorio privado del autor, y se pueden facilitar al tribunal si los pide.

Tampoco están los `campaign.json` de las corridas Sepolia 201, 500, 604 y 801, porque contienen notas de depósito del mezclador (secreto y nullifier). Sus `addresses.json`, `summary.txt` y trazas sí están en `results/sepolia_campaign/`.

Todo lo necesario para reproducir los resultados (código, contratos, scripts, `results/` y el `dataset.pkl` de la release `dataset-2026-06-26`) es público.
