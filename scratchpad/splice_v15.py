"""Apply v15 humanization compactions."""
from pathlib import Path

TFM = Path('/home/anon/aml-thesis/tfm/chapters_v3')
SC = Path('/mnt/c/Users/Asus/AppData/Local/Temp/claude/C--Users-Asus-Downloads-files/2b4bd8c7-673e-4254-a0dc-583f5d6cecc2/scratchpad')

def splice(fname, start_marker, end_marker, compact_file):
    src = TFM / fname
    compact = SC / compact_file
    lines = src.read_text(encoding='utf-8').splitlines()
    compact_lines = compact.read_text(encoding='utf-8').splitlines()

    start = end = None
    for i, l in enumerate(lines):
        if l.startswith(start_marker) and start is None:
            start = i
        if end_marker and l.startswith(end_marker) and end is None and (start is not None and i > start):
            end = i
            break
    if end is None:
        end = len(lines)

    print(f'{fname}: {start_marker!r}..{end_marker!r} = lines {start+1}..{end} ({end-start})')
    new_lines = lines[:start] + compact_lines + [''] + lines[end:]
    print(f'  {len(lines)} → {len(new_lines)} (Δ={len(lines)-len(new_lines)})')
    src.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')

splice('05_diseno_dataset.md', '## 5.A ', '## 5.C ', 'v15_5AB.md')
splice('05_diseno_dataset.md', '## 5.C ', '## 5.D ', 'v15_5C.md')
splice('07_visibilidad_conclusiones.md', '## 7.A ', '## 7.B ', 'v15_7A.md')

import subprocess
print('\nFinal:')
for f in sorted(TFM.glob('*.md')):
    lines = f.read_text().splitlines()
    print(f'  {len(lines):>5}  {f.name}')
total = sum(len(f.read_text().splitlines()) for f in TFM.glob('*.md'))
print(f'  TOTAL: {total}')
