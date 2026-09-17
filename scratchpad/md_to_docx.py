"""Generate tfm/TFM_Sinawi_UC3M.docx from tfm/chapters_v2/*.md.

Uses python-docx (no pandoc dep). Produces a clean academic-style TFM
with cover page, TOC field, page numbers in footer, and monospaced
code blocks.

Run:
    python scratchpad/md_to_docx.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Cm, Pt, RGBColor


REPO = Path(__file__).resolve().parents[1]
CHAPTERS_DIR = REPO / "tfm" / "chapters_v3"
OUTPUT = REPO / "tfm" / "TFM_Sinawi_UC3M.docx"

TITLE = (
    "Detección adversarial multi-agente de blanqueo de capitales en "
    "Ethereum: simulación y detección basadas en agentes LLM bajo "
    "visibilidad parcial federada"
)
AUTHOR = "Saleh Sinawi"
INSTITUTION = "Universidad Carlos III de Madrid"
DEGREE = "Trabajo Fin de Máster"
YEAR = "2026"

CHAPTER_ORDER = [
    "00_portada.md",
    "00b_glosario.md",
    "01_introduccion.md",
    "02_estado_arte_conceptos.md",
    "03_limitaciones_previas.md",
    "04_analisis_tecnologias.md",
    "05_diseno_dataset.md",
    "06_experimentos_resultados.md",
    "07_visibilidad_conclusiones.md",
    "08_bibliografia.md",
    "A_anexos.md",
]


# ---------- helpers ---------------------------------------------------

def _add_page_number_field(paragraph):
    """Insert a Word PAGE field into a footer paragraph."""
    run = paragraph.add_run()
    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")
    instrText = OxmlElement("w:instrText")
    instrText.set(qn("xml:space"), "preserve")
    instrText.text = "PAGE"
    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "end")
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)


def _add_toc_field(paragraph):
    """Insert a TOC field that Word populates on open."""
    _add_field(paragraph, r'TOC \o "1-3" \h \z \u',
               "Índice — abrir en Word y pulsar F9 para actualizar.")


def _add_lof_field(paragraph):
    """List of Figures — Word field TOC \\c 'Figura'."""
    _add_field(paragraph, r'TOC \h \z \c "Figura"',
               "Índice de figuras — pulsar F9 en Word para poblar.")


def _add_lot_field(paragraph):
    """List of Tables — Word field TOC \\c 'Tabla'."""
    _add_field(paragraph, r'TOC \h \z \c "Tabla"',
               "Índice de tablas — pulsar F9 en Word para poblar.")


def _add_field(paragraph, instr: str, placeholder: str):
    run = paragraph.add_run()
    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")
    instrText = OxmlElement("w:instrText")
    instrText.set(qn("xml:space"), "preserve")
    instrText.text = instr
    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "separate")
    fldChar3 = OxmlElement("w:t")
    fldChar3.text = placeholder
    fldChar4 = OxmlElement("w:fldChar")
    fldChar4.set(qn("w:fldCharType"), "end")
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)
    run._r.append(fldChar3)
    run._r.append(fldChar4)


def _add_seq_field(paragraph, seq_name: str):
    """SEQ field: auto-increments a counter (e.g. Figura, Tabla)."""
    run = paragraph.add_run()
    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")
    instrText = OxmlElement("w:instrText")
    instrText.set(qn("xml:space"), "preserve")
    instrText.text = f' SEQ {seq_name} \\* ARABIC '
    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "separate")
    fldChar3 = OxmlElement("w:t")
    fldChar3.text = "?"
    fldChar4 = OxmlElement("w:fldChar")
    fldChar4.set(qn("w:fldCharType"), "end")
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)
    run._r.append(fldChar3)
    run._r.append(fldChar4)


def _configure_styles(doc: Document) -> None:
    """Compact academic styling — save vertical space."""
    normal = doc.styles["Normal"]
    normal.font.name = "Cambria"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(3)
    normal.paragraph_format.line_spacing = 1.15

    # Reduce heading spacing
    for level in range(1, 5):
        try:
            hstyle = doc.styles[f"Heading {level}"]
            hstyle.paragraph_format.space_before = Pt(8 if level == 1 else 6)
            hstyle.paragraph_format.space_after = Pt(3)
            hstyle.paragraph_format.keep_with_next = True
        except KeyError:
            pass

    # Reduce list spacing
    for name in ("List Bullet", "List Number"):
        try:
            s = doc.styles[name]
            s.paragraph_format.space_before = Pt(0)
            s.paragraph_format.space_after = Pt(2)
        except KeyError:
            pass

    styles = doc.styles
    if "CodeBlock" not in [s.name for s in styles]:
        code = styles.add_style("CodeBlock", WD_STYLE_TYPE.PARAGRAPH)
        code.font.name = "Consolas"
        code.font.size = Pt(9)
        code.paragraph_format.left_indent = Cm(0.5)
        code.paragraph_format.space_before = Pt(2)
        code.paragraph_format.space_after = Pt(2)


def _add_cover(doc: Document) -> None:
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(INSTITUTION.upper())
    run.bold = True
    run.font.size = Pt(14)

    doc.add_paragraph()

    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(DEGREE)
    run.italic = True
    run.font.size = Pt(13)

    doc.add_paragraph()
    doc.add_paragraph()

    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(TITLE)
    run.bold = True
    run.font.size = Pt(18)

    for _ in range(6):
        doc.add_paragraph()

    for line, size, bold in [
        (f"Autor: {AUTHOR}", 12, False),
        ("", 12, False),
        (YEAR, 12, True),
    ]:
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = para.add_run(line)
        run.font.size = Pt(size)
        run.bold = bold

    # TOC on its own page
    doc.add_page_break()
    para = doc.add_paragraph()
    run = para.add_run("Índice")
    run.bold = True
    run.font.size = Pt(16)
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT

    para = doc.add_paragraph()
    _add_toc_field(para)

    # List of figures
    doc.add_page_break()
    para = doc.add_paragraph()
    run = para.add_run("Índice de figuras")
    run.bold = True
    run.font.size = Pt(16)
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    para = doc.add_paragraph()
    _add_lof_field(para)

    # List of tables
    doc.add_page_break()
    para = doc.add_paragraph()
    run = para.add_run("Índice de tablas")
    run.bold = True
    run.font.size = Pt(16)
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    para = doc.add_paragraph()
    _add_lot_field(para)

    doc.add_page_break()


def _add_footer_page_number(doc: Document) -> None:
    footer = doc.sections[0].footer
    para = footer.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_page_number_field(para)


# ---------- markdown parsing (small, purpose-built) -------------------

INLINE_TOKEN = re.compile(
    r"(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|__[^_\n]+__|_[^_\n]+_|"
    r"\[[^\]]+\]\([^)]+\))",
)


def _emit_inline(paragraph, text: str) -> None:
    """Render inline formatting: **bold**, *italic*, `code`, [txt](url)."""
    pos = 0
    for match in INLINE_TOKEN.finditer(text):
        if match.start() > pos:
            paragraph.add_run(text[pos:match.start()])
        tok = match.group(0)
        if tok.startswith("`") and tok.endswith("`"):
            run = paragraph.add_run(tok[1:-1])
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif tok.startswith("**") and tok.endswith("**"):
            run = paragraph.add_run(tok[2:-2])
            run.bold = True
        elif tok.startswith("__") and tok.endswith("__"):
            run = paragraph.add_run(tok[2:-2])
            run.bold = True
        elif tok.startswith("*") and tok.endswith("*"):
            run = paragraph.add_run(tok[1:-1])
            run.italic = True
        elif tok.startswith("_") and tok.endswith("_"):
            run = paragraph.add_run(tok[1:-1])
            run.italic = True
        elif tok.startswith("["):
            # [text](url) → just show the text (bibliographic-style)
            link_text = tok[1: tok.index("]")]
            paragraph.add_run(link_text)
        pos = match.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def _add_heading(doc: Document, text: str, level: int) -> None:
    heading = doc.add_heading(level=min(level, 4))
    _emit_inline(heading, text.strip())


def _add_code_block(doc: Document, lines: list[str]) -> None:
    text = "\n".join(lines)
    para = doc.add_paragraph(style="CodeBlock")
    run = para.add_run(text)
    run.font.name = "Consolas"
    run.font.size = Pt(9)


def _add_table(doc: Document, rows: list[list[str]], caption: str | None = None) -> None:
    if not rows:
        return
    # Caption BEFORE the table with a SEQ Tabla field (for LOT)
    if caption is not None:
        cap_para = doc.add_paragraph()
        cap_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = cap_para.add_run("Tabla ")
        run.bold = True
        _add_seq_field(cap_para, "Tabla")
        if caption:
            run2 = cap_para.add_run(f": {caption}")
            run2.italic = True
    ncols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=ncols)
    table.style = "Light Grid Accent 1"
    for i, row in enumerate(rows):
        for j in range(ncols):
            cell = table.rows[i].cells[j]
            cell.text = ""
            para = cell.paragraphs[0]
            cell_text = row[j] if j < len(row) else ""
            if i == 0:
                run = para.add_run(cell_text.strip())
                run.bold = True
            else:
                _emit_inline(para, cell_text.strip())
    doc.add_paragraph()  # spacer after table


def _parse_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_html_comments(text: str) -> str:
    return HTML_COMMENT.sub("", text)


def _render_markdown(doc: Document, md_text: str) -> None:
    md_text = _strip_html_comments(md_text)
    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        raw = line
        stripped = line.strip()

        # Fenced code block
        if stripped.startswith("```"):
            i += 1
            block: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            _add_code_block(doc, block)
            i += 1
            continue

        # Table (simple pipe format with separator row of dashes)
        if (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$",
                        lines[i + 1].strip())
        ):
            rows = [_parse_table_row(stripped)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_parse_table_row(lines[i].strip()))
                i += 1
            # Auto-generate a "Tabla N" caption for every table so it appears
            # in the LOT (list of tables). Caption itself is empty — users
            # who want specific captions can add them in prose before/after.
            _add_table(doc, rows, caption="")
            continue

        # Image: ![alt](path) on its own line
        m_img = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", stripped)
        if m_img:
            from docx.shared import Inches
            alt_text = m_img.group(1)
            img_path = m_img.group(2)
            if not Path(img_path).is_absolute():
                img_path = str(REPO / img_path)
            try:
                para = doc.add_paragraph()
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = para.add_run()
                run.add_picture(img_path, width=Inches(6.0))
                # Caption with SEQ Figura field (for LOF)
                cap = doc.add_paragraph()
                cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                cap_run = cap.add_run("Figura ")
                cap_run.bold = True
                cap_run.font.size = Pt(9)
                _add_seq_field(cap, "Figura")
                if alt_text:
                    # Strip a leading "Figura N." prefix from alt_text if
                    # the author put one manually (avoid duplication).
                    clean_alt = re.sub(r"^Figura\s+\d+\.\s*", "", alt_text)
                    cap_run2 = cap.add_run(f": {clean_alt}")
                    cap_run2.italic = True
                    cap_run2.font.size = Pt(9)
            except Exception as e:
                doc.add_paragraph(f"[Imagen no disponible: {img_path} — {e}]")
            i += 1
            continue

        # Headings
        m = re.match(r"^(#{1,4})\s+(.+?)\s*$", stripped)
        if m:
            level = len(m.group(1))
            _add_heading(doc, m.group(2), level)
            i += 1
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            para = doc.add_paragraph()
            pPr = para._p.get_or_add_pPr()
            pbdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:space"), "1")
            bottom.set(qn("w:color"), "auto")
            pbdr.append(bottom)
            pPr.append(pbdr)
            i += 1
            continue

        # Bullet list
        m_bul = re.match(r"^(\s*)[-*]\s+(.+?)\s*$", raw)
        if m_bul:
            indent = len(m_bul.group(1))
            content = m_bul.group(2)
            style = "List Bullet"
            para = doc.add_paragraph(style=style)
            if indent >= 4:
                para.paragraph_format.left_indent = Cm(1.0 * (indent // 2))
            _emit_inline(para, content)
            i += 1
            continue

        # Numbered list
        m_num = re.match(r"^(\s*)(\d+)\.\s+(.+?)\s*$", raw)
        if m_num:
            para = doc.add_paragraph(style="List Number")
            _emit_inline(para, m_num.group(3))
            i += 1
            continue

        # Block quote
        if stripped.startswith("> "):
            para = doc.add_paragraph()
            para.paragraph_format.left_indent = Cm(0.75)
            run = para.add_run(stripped[2:])
            run.italic = True
            i += 1
            continue

        # Blank line — collapse consecutive blanks into a single small spacer.
        if not stripped:
            # Skip all consecutive blank lines
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            # Add ONE tiny spacer only if the surrounding content warrants it
            # (i.e. not immediately after a heading / table / image caption).
            # Just skip the blank lines entirely — Word paragraphs already have
            # natural space_after that suffices.
            i = j
            continue

        # Paragraph — gather until blank or block delimiter
        buf = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            ns = nxt.strip()
            if (
                not ns
                or ns.startswith(("#", "```", "|", "> ", "---", "***", "___"))
                or re.match(r"^\s*[-*]\s+", nxt)
                or re.match(r"^\s*\d+\.\s+", nxt)
            ):
                break
            buf.append(ns)
            i += 1
        para = doc.add_paragraph()
        _emit_inline(para, " ".join(buf))


# ---------- main ------------------------------------------------------

def _add_indices(doc: Document) -> None:
    """Add TOC + LOF + LOT on separate pages."""
    doc.add_page_break()
    para = doc.add_paragraph()
    run = para.add_run("Índice de contenidos")
    run.bold = True
    run.font.size = Pt(16)
    para = doc.add_paragraph()
    _add_toc_field(para)

    doc.add_page_break()
    para = doc.add_paragraph()
    run = para.add_run("Índice de figuras")
    run.bold = True
    run.font.size = Pt(16)
    para = doc.add_paragraph()
    _add_lof_field(para)

    doc.add_page_break()
    para = doc.add_paragraph()
    run = para.add_run("Índice de tablas")
    run.bold = True
    run.font.size = Pt(16)
    para = doc.add_paragraph()
    _add_lot_field(para)


def main() -> int:
    doc = Document()
    _configure_styles(doc)
    _add_footer_page_number(doc)

    for fname in CHAPTER_ORDER:
        path = CHAPTERS_DIR / fname
        if not path.exists():
            print(f"[md_to_docx] MISSING: {path}", file=sys.stderr)
            continue
        print(f"[md_to_docx] rendering {fname} ...", file=sys.stderr)
        text = path.read_text(encoding="utf-8")
        if not fname.startswith("00_portada"):
            doc.add_page_break()
        _render_markdown(doc, text)
        # After portada, insert TOC + LOF + LOT
        if fname.startswith("00_portada"):
            _add_indices(doc)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUTPUT))
    print(
        f"[md_to_docx] wrote {OUTPUT} "
        f"({OUTPUT.stat().st_size / 1024:.1f} KB)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
