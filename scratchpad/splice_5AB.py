"""Replace §5.A + §5.B in Cap 5 with compact version."""
from pathlib import Path

src = Path('/home/anon/aml-thesis/tfm/chapters_v3/05_diseno_dataset.md')
compact = Path('/mnt/c/Users/Asus/AppData/Local/Temp/claude/C--Users-Asus-Downloads-files/2b4bd8c7-673e-4254-a0dc-583f5d6cecc2/scratchpad/compact_5AB.md')

lines = src.read_text(encoding='utf-8').splitlines()
compact_lines = compact.read_text(encoding='utf-8').splitlines()

# Locate §5.A start and §5.C start (or §5.1 which comes right after §5.C header)
start = None
end = None
for i, l in enumerate(lines):
    if l.startswith('## 5.A ') and start is None:
        start = i
    if l.startswith('## 5.C ') and end is None:
        end = i
        break

if start is None or end is None:
    raise RuntimeError(f'boundaries: start={start} end={end}')

print(f'Original §5.A-B spans lines {start+1}..{end} ({end-start} lines)')
print(f'Replacement is {len(compact_lines)} lines')

new_lines = lines[:start] + compact_lines + [''] + lines[end:]
print(f'Total lines: {len(lines)} → {len(new_lines)} (reduction {len(lines)-len(new_lines)})')
src.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
print(f'Wrote {src}')
