# TFM — writeup

Trabajo Fin de Máster: **"Detección adversarial multi-agente de blanqueo de capitales en Ethereum: simulación y detección basadas en agentes LLM bajo visibilidad parcial federada"**

## Qué fichero es el bueno

- **`TFM_Sinawi_UC3M.docx` es el documento canónico**: el que se entrega y se defiende. Las últimas revisiones (referencias cruzadas, marco regulador, citas en línea, pasada de estilo, consistencia con el repositorio) se hicieron directamente sobre el Word.
- `chapters_v3/*.md` es la fuente Markdown de la que se generó la primera versión de esta estructura (6 capítulos + anexos) con `scratchpad/md_to_docx.py`. Va por detrás del docx.
- `figures/` — figuras generadas (`scratchpad/gen_*_fig*.py`).
- `references.bib` — bibliografía en BibTeX (33 entradas, numeradas [1]-[33] en el texto). `references_audit.md` — verificación de cada referencia.

## Estructura (`chapters_v3/`)

| Fichero | Contenido |
|---|---|
| `00_portada.md` | Portada UC3M |
| `01_introduccion.md` | Cap. 1 — Resumen/Abstract, contexto, problema, objetivos y requisitos, marco regulador, estructura |
| `02_estado_arte_conceptos.md` | Cap. 2 — Estado del arte, brecha y contexto tecnológico 2024-2026 |
| `04_analisis_tecnologias.md` | Cap. 3 — Análisis del problema y tecnologías |
| `05_diseno_dataset.md` | Cap. 4 — Diseño, datasets, planificación y presupuesto |
| `06_experimentos_resultados.md` | Cap. 5 — Experimentos y resultados |
| `07_visibilidad_conclusiones.md` | Cap. 6 — Visibilidad parcial, conclusiones, limitaciones, competencias |
| `08_bibliografia.md` | Bibliografía |
| `A_anexos.md` | Anexos A (prompts), F (repositorio), G (atribuciones), H (declaración de IA) |

## Regenerar el docx desde Markdown

```bash
python scratchpad/md_to_docx.py   # escribe tfm/TFM_Sinawi_UC3M.docx a partir de chapters_v3/
```

Ojo: sobrescribe el docx canónico con la versión Markdown, que está desactualizada respecto al Word.

## Notas de estilo

- Español académico, impersonal. Términos técnicos en inglés en cursiva la primera vez (*mixer*, *layering*, *actor cluster*).
- Decimales con coma en el texto en español; el Abstract en inglés y los números de versión (Solidity 0.8, Sonnet 4.6) mantienen el punto.
- Citas numéricas `[n]` enlazadas a `08_bibliografia.md`.
