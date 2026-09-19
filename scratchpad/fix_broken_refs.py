"""Fix broken references left after renumbering."""
from pathlib import Path
import re

TFM = Path('/home/anon/aml-thesis/tfm/chapters_v3')

REPLACEMENTS = [
    # §8.9.X (deleted §8.9.Z, remaining should point to §5.9)
    (r'§8\.9\.Z', r'§4.F Presupuesto'),
    (r'§8\.9\.([A-Z])', r'§5.9.\1'),
    (r'§8\.9\.(\d+)', r'§5.9'),
    (r'§8\.10', r'§5.10'),
    (r'§8\.11', r'§6.2.4.29'),
    # §8.1 through §8.8
    (r'§8\.([1-8])', r'§5.\1'),
    # Chapter references
    (r'Capítulo 8', r'Capítulo 5'),
    (r'Capítulo 7 (?!—)', r'Capítulo 6 '),  # only when not followed by — (title)
    (r'Capítulo 10', r'Capítulo 6'),
    # §10.X
    (r'§10\.([1-5])', r'§6.2.\1'),
    (r'§10\.4\.([A-Z])', r'§6.2.4.\1'),
    (r'§10\.4\.(\d+)', r'§6.2.4.\1'),
    # §7.C etc — already handled but check
    (r'§7\.([ABC])', r'§6.\1'),
    # §4.6 (Analisis+tech chapter) — probably still valid inside Cap 3 refs
    # No change needed
]

for f in TFM.glob('*.md'):
    text = f.read_text(encoding='utf-8')
    orig = text
    for pat, repl in REPLACEMENTS:
        text = re.sub(pat, repl, text)
    if text != orig:
        f.write_text(text, encoding='utf-8')
        print(f'{f.name}: fixed broken refs')

# Verify no more §8.X or §10.X remain
print('\n=== Remaining suspicious refs ===')
for f in TFM.glob('*.md'):
    t = f.read_text(encoding='utf-8')
    matches = re.findall(r'§8\.\w+|§10\.\w+|§7\.[ABC]|Capítulo 8|Capítulo 10', t)
    if matches:
        print(f'{f.name}: {len(matches)} → {set(matches)}')
