# TFM — writeup

Trabajo Fin de Máster: **"Detección adversarial multi-agente de blanqueo de capitales en Ethereum: simulación y detección basadas en agentes LLM bajo visibilidad parcial federada"**

## Qué fichero es el bueno

- **`TFM_SalehSinawi.docx` es el documento canónico**: el que se entrega y se defiende. Todas las revisiones desde agosto de 2026 (cifras trazadas a `results/`, marco regulador, referencias, estilo) se hicieron directamente sobre el Word; al abrirlo, aceptar la actualización de campos (o Ctrl+A, F9) para regenerar los índices.
- `chapters_v3/*.md` son las notas de trabajo en Markdown de las que salió la primera versión de esta estructura (6 capítulos + anexos) con `scratchpad/md_to_docx.py`. Van muy por detrás del docx: no las uses como fuente de cifras.
- `figures/` — figuras generadas (`scripts/figures/gen_fig*_v2.py` para las Figuras 5-7 actuales; los ficheros sin `_v2` son versiones anteriores).
- `references.bib` — bibliografía en BibTeX de una versión anterior (33 entradas). El docx actual tiene 44 referencias numeradas por orden de primera aparición; `references_audit.md` recoge la verificación de cada una.

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
| `A_anexos.md` | Anexos A (prompts), B (repositorio), C (atribuciones), D (declaración de IA) |

## Regenerar el docx desde Markdown

```bash
python scratchpad/md_to_docx.py   # genera un docx a partir de chapters_v3/ (no sobrescribas TFM_SalehSinawi.docx con él)
```

Ojo: sobrescribe el docx canónico con la versión Markdown, que está desactualizada respecto al Word.

## Notas de estilo

- Español académico, impersonal. Términos técnicos en inglés en cursiva la primera vez (*mixer*, *layering*, *actor cluster*).
- Decimales con coma en el texto en español; el Abstract en inglés y los números de versión (Solidity 0.8, Sonnet 4.6) mantienen el punto.
- Citas numéricas `[n]` enlazadas a `08_bibliografia.md`.
