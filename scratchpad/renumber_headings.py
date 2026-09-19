"""Safe renumbering: change section headings in Cap 5 and Cap 6 files
to match their current chapter numbers.

Cap 5 file (06_experimentos_resultados.md): §8.X → §5.X
Cap 6 file (07_visibilidad_conclusiones.md): §7.X → §6.X (already), §10.X → §6.X

Also update inline cross-references to those IDs across all files.
"""
from pathlib import Path
import re

TFM = Path('/home/anon/aml-thesis/tfm/chapters_v3')

CAP5 = TFM / '06_experimentos_resultados.md'
CAP6 = TFM / '07_visibilidad_conclusiones.md'

# --- Cap 5: change all §8.X in this file to §5.X ---
text5 = CAP5.read_text(encoding='utf-8')
before = text5.count('8.')
# Replace §8.X.Y.Z (deepest first) so we don't mess up numbers
text5 = re.sub(r'\b8\.9\.([A-Z]|\d+)\b', r'5.9.\1', text5)
text5 = re.sub(r'\b8\.1\.([1-9])\b', r'5.1.\1', text5)
text5 = re.sub(r'\b8\.2\.([1-9])\b', r'5.2.\1', text5)
text5 = re.sub(r'\b8\.5\.([1-9])\b', r'5.5.\1', text5)
text5 = re.sub(r'\b8\.7\.bis\b', r'5.7.bis', text5)
# Now the ## 8.X → ## 5.X
for n in range(1, 12):
    text5 = re.sub(rf'\b8\.{n}\b', rf'5.{n}', text5)
# Update the chapter title
text5 = re.sub(r'# Capítulo 6 — Experimentos', '# Capítulo 5 — Experimentos', text5)
after = text5.count('5.')
CAP5.write_text(text5, encoding='utf-8')
print(f'Cap 5: renumbered §8.X → §5.X headings')

# --- Cap 6: change §10.X → §6.X, §7.X stays as-is (already renumbered) ---
text6 = CAP6.read_text(encoding='utf-8')
# §10.4.X → §6.4.X
text6 = re.sub(r'\b10\.4\.([A-Z]|\d+)\b', r'6.4.\1', text6)
# Now the ## 10.X → ## 6.X
for n in range(1, 6):
    text6 = re.sub(rf'\b10\.{n}\b', rf'6.{n}', text6)
# Update chapter title if needed
text6 = re.sub(r'# Capítulo 7 — Visibilidad', '# Capítulo 6 — Visibilidad', text6)
# §7.A/B/C stays (already correct as it's within Cap 6)
CAP6.write_text(text6, encoding='utf-8')
print(f'Cap 6: renumbered §10.X → §6.X headings')

# --- Update cross-references in ALL files ---
for f in TFM.glob('*.md'):
    if f.name in ('06_experimentos_resultados.md', '07_visibilidad_conclusiones.md'):
        continue  # already done above
    text = f.read_text(encoding='utf-8')
    orig = text
    # In other files, update inline refs to §8.X and §10.X
    text = re.sub(r'§8\.9\.([A-Z]|\d+)', r'§5.9.\1', text)
    text = re.sub(r'§10\.4\.(\d+)', r'§6.4.\1', text)
    for n in range(1, 12):
        text = re.sub(rf'§8\.{n}\b', rf'§5.{n}', text)
    for n in range(1, 6):
        text = re.sub(rf'§10\.{n}\b', rf'§6.{n}', text)
    # §7.C references stay
    if text != orig:
        f.write_text(text, encoding='utf-8')
        print(f'{f.name}: cross-refs updated')

# Also fix the Cap 4 file — the chapter title says 4 but the file has ## 3.X headings from old numbering
CAP3 = TFM / '04_analisis_tecnologias.md'
text3 = CAP3.read_text(encoding='utf-8')
text3 = re.sub(r'# Capítulo 4 — ', '# Capítulo 3 — ', text3)
# Analysis + tech had ## 3.X or ## 4.X — check
text3 = re.sub(r'## 3\.(\d+)\b', r'## 3.\1', text3)  # no-op check
CAP3.write_text(text3, encoding='utf-8')
print('Cap 4 file: chapter title updated to "Capítulo 3"')

# Cap 5 file (diseño+dataset+lenguajes) currently has §5.X headings that match
CAP4 = TFM / '05_diseno_dataset.md'
text4 = CAP4.read_text(encoding='utf-8')
text4 = re.sub(r'# Capítulo 5 — Diseño', '# Capítulo 4 — Diseño', text4)
CAP4.write_text(text4, encoding='utf-8')
print('Cap 5 file (diseño): chapter title updated to "Capítulo 4"')

print('\nAll renumbering complete.')
