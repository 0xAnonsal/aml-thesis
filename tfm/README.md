# TFM — writeup

Trabajo Fin de Máster: **"Detección adversarial multi-agente de blanqueo de capitales en Ethereum: simulación y detección basadas en agentes LLM bajo visibilidad parcial federada"**

Escrito en Markdown por portabilidad. Convertible a Word (via `python-docx`) o LaTeX (via pandoc) cuando el formato final esté decidido con la universidad. El draft docx canónico se genera con el script `scratchpad/md_to_docx.py`.

## Estructura de capítulos

| # | Archivo | Título | Estado |
|---|---|---|---|
| 1 | `chapters/01_introduccion.md` | Introducción | ✅ Completado |
| 2 | `chapters/02_estado_del_arte.md` | Estado del arte | ✅ Completado |
| 3 | `chapters/03_arquitectura.md` | Arquitectura del sistema | ✅ Completado |
| 4 | `chapters/04_metodologia.md` | Metodología experimental | ✅ Completado |
| 5 | `chapters/05_resultados.md` | Resultados y discusión | ✅ Completado |
| 6 | `chapters/06_conclusiones.md` | Conclusiones y trabajo futuro | ✅ Completado |
| A | `chapters/A_anexos.md` | Anexos (código, prompts, hashes tx) | ✅ Completado |

Tamaño combinado del draft: ~2 950 líneas, ~100 KB en formato `.docx`.

## Convertir a otros formatos

```bash
# A DOCX (formato principal para submission)
python scratchpad/md_to_docx.py

# A PDF via pandoc + LaTeX (alternativa)
pandoc chapters/*.md -o tfm.pdf --pdf-engine=xelatex --toc

# A LaTeX intermedio
pandoc chapters/*.md -o tfm.tex --toc
```

## Bibliografía

Referencias en formato BibTeX en `tfm/references.bib`. Por ahora, citas inline `[Autor Año]` en Markdown; se convertirían a `\cite{}` si se decide pasar el draft a LaTeX.

## Notas de estilo

- **Idioma**: español académico. Términos técnicos aceptados en inglés (*mixer*, *layering*, *actor cluster*, *ground truth*) van en cursiva la primera vez y luego sin.
- **Formalidad**: 3ª persona ("se propone", "se implementa"). Evitar "yo" o "nosotros".
- **Métricas**: siempre con unidad ("F1 = 0,68", "ARI = 0,066", "coste = 0,14 USD"). Coma decimal (convención española).
- **Direcciones Ethereum**: full 42-char en tablas; primeros 10 + últimos 4 en prosa (`0xa513e6e4...c853`).
