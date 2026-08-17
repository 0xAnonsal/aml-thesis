# TFM — writeup

Trabajo Fin de Máster: **"Detección adversarial multi-agente de blanqueo de capitales en Ethereum: simulación y detección basadas en agentes LLM bajo visibilidad parcial federada"**

Escrito en Markdown por portabilidad. Convertible a LaTeX (via pandoc) o Word cuando el formato final esté decidido con la universidad.

## Estructura de capítulos

| # | Archivo | Título | Estado |
|---|---|---|---|
| 1 | `chapters/01_introduccion.md` | Introducción | Draft en curso |
| 2 | `chapters/02_estado_del_arte.md` | Estado del arte | Pendiente |
| 3 | `chapters/03_arquitectura.md` | Arquitectura del sistema | Pendiente |
| 4 | `chapters/04_metodologia.md` | Metodología experimental | Pendiente |
| 5 | `chapters/05_resultados.md` | Resultados y discusión | Pendiente |
| 6 | `chapters/06_conclusiones.md` | Conclusiones y trabajo futuro | Pendiente |
| A | `chapters/A_anexos.md` | Anexos (código, prompts, hashes tx) | Pendiente |

## Convertir a otros formatos

```bash
# A PDF via pandoc + LaTeX
pandoc chapters/*.md -o tfm.pdf --pdf-engine=xelatex --toc

# A DOCX (Word) — mismo pandoc, solo cambia el output
pandoc chapters/*.md -o tfm.docx --toc

# A LaTeX
pandoc chapters/*.md -o tfm.tex --toc
```

## Bibliografía

Refs en formato BibTeX pendiente en `tfm/references.bib`. Por ahora, citas inline `[Autor Año]` en Markdown; se convertirán a `\cite{}` cuando pase a LaTeX.

## Notas de estilo

- **Idioma**: español académico. Términos técnicos aceptados en inglés (mixer, layering, actor cluster, ground truth) van en cursiva la primera vez y luego sin.
- **Formalidad**: 3ª persona ("se propone", "se implementa"). Evitar "yo" o "nosotros".
- **Métricas**: siempre con unidad ("F1 = 0,68", "ARI = 0,066", "coste = 0,14 USD"). Coma decimal.
- **Direcciones Ethereum**: full 42-char en tablas + primeros 10 + últimos 4 en prosa (`0xa513e6e4...c853`).
