"""Trim Anexo I from A_anexos.md (RGPD not in Fernando/Lara/June and not
in UC3M Normativa TFM — only in DPO guide, optional)."""
from pathlib import Path
src = Path('/home/anon/aml-thesis/tfm/chapters_v3/A_anexos.md')
lines = src.read_text(encoding='utf-8').splitlines()
start = None
for i, l in enumerate(lines):
    if l.startswith('## Anexo I '):
        start = i
        break
if start is None:
    raise RuntimeError('Anexo I not found')
# Keep everything before Anexo I
new_lines = lines[:start]
# Remove trailing blank lines
while new_lines and not new_lines[-1].strip():
    new_lines.pop()
print(f'A_anexos.md: {len(lines)} → {len(new_lines)} lines (removed Anexo I RGPD, {len(lines)-len(new_lines)} lines)')
src.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
