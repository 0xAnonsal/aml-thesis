"""Final renumbering pass."""
from pathlib import Path
import re

TFM = Path('/home/anon/aml-thesis/tfm/chapters_v3')

for f in TFM.glob('*.md'):
    text = f.read_text(encoding='utf-8')
    orig = text
    # §7.A/B/C → §6.A/B/C (they were in Cap 6 all along)
    text = re.sub(r'§7\.([ABC])\b', r'§6.\1', text)
    text = re.sub(r'^## 7\.([ABC])\b', r'## 6.\1', text, flags=re.MULTILINE)
    # "Capítulo 8" → "Capítulo 5"
    text = re.sub(r'Capítulo 8\b', 'Capítulo 5', text)
    # §5.9.Z (was Cost summary, deleted) → §5.F Presupuesto
    text = re.sub(r'§5\.9\.Z', r'§4.F', text)
    # §5.9.NN (numeric IDs like §5.9.42) → simplify to §5.9
    text = re.sub(r'§5\.9\.\d+', r'§5.9', text)
    # Remaining §5.0.4/5 references stay (they're actual section IDs in Cap 4)
    if text != orig:
        f.write_text(text, encoding='utf-8')
        print(f'{f.name}: final polish applied')
