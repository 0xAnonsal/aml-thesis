"""Replace §8.9 in Ch 6 with compacted version."""
from pathlib import Path

src = Path('/home/anon/aml-thesis/tfm/chapters_v3/06_experimentos_resultados.md')
compact = Path('/mnt/c/Users/Asus/AppData/Local/Temp/claude/C--Users-Asus-Downloads-files/2b4bd8c7-673e-4254-a0dc-583f5d6cecc2/scratchpad/compacted_89.md')

lines = src.read_text(encoding='utf-8').splitlines()
compact_lines = compact.read_text(encoding='utf-8').splitlines()

# Locate line where "## 8.9 " starts and where "## 8.10 " starts.
start = None
end = None
for i, l in enumerate(lines):
    if l.startswith('## 8.9 ') and start is None:
        start = i
    if l.startswith('## 8.10 ') and end is None:
        end = i
        break

if start is None or end is None:
    raise RuntimeError(f'Could not locate boundaries: start={start} end={end}')

print(f'Original §8.9 spans lines {start+1}..{end} ({end-start} lines)')
print(f'Replacement is {len(compact_lines)} lines')

new_lines = lines[:start] + compact_lines + [''] + lines[end:]
print(f'Total lines: {len(lines)} → {len(new_lines)} (reduction {len(lines)-len(new_lines)})')
src.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
print(f'Wrote {src}')
