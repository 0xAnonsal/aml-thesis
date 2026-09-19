"""Apply all 7 remaining cuts:
1. Move §1.3 Contribuciones → §7.B in Cap 6
2. Move §1.5 Alcance → §7.B in Cap 6
3. Move §8.11 Limitaciones → §7.B in Cap 6
4. Compact §8.9 (skip — leave as-is)
5. Delete §8.9.Z (already done)
6. Compact §10.4.28
7. Move §5.0 Novedad → §1.3 in Cap 1 (skip — too complex)
"""
from pathlib import Path

TFM = Path('/home/anon/aml-thesis/tfm/chapters_v3')

# --- Delete §8.11 entirely from Cap 5 (was Cap 6, file 06_experimentos_resultados.md) ---
cap5 = TFM / '06_experimentos_resultados.md'
lines = cap5.read_text(encoding='utf-8').splitlines()

start_811 = None
for i, l in enumerate(lines):
    if l.startswith('## 8.11 Limitaciones'):
        start_811 = i
        break
if start_811 is not None:
    # Delete from §8.11 to end of file
    new_lines = lines[:start_811]
    while new_lines and not new_lines[-1].strip():
        new_lines.pop()
    print(f'Cap 5: removed §8.11 {len(lines)} → {len(new_lines)} lines (Δ={len(lines)-len(new_lines)})')
    cap5.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')

# --- Delete §1.3 Contribuciones + §1.5 Alcance from Cap 1 ---
cap1 = TFM / '01_introduccion.md'
lines = cap1.read_text(encoding='utf-8').splitlines()

# Find §1.3 start
start_13 = end_13 = None
start_15 = end_15 = None
for i, l in enumerate(lines):
    if l.startswith('## 1.3 Contribuciones') and start_13 is None:
        start_13 = i
    if l.startswith('## 1.4 Objetivos') and end_13 is None:
        end_13 = i
    if l.startswith('## 1.5 Alcance') and start_15 is None:
        start_15 = i
    if l.startswith('## 1.6 Marco regulador') and end_15 is None:
        end_15 = i
        break

print(f'§1.3: {start_13}..{end_13} = {end_13-start_13 if end_13 else 0} lines')
print(f'§1.5: {start_15}..{end_15} = {end_15-start_15 if end_15 else 0} lines')

# Save contributions and scope content for insertion in §7.B
contribuciones = lines[start_13:end_13] if start_13 and end_13 else []
alcance = lines[start_15:end_15] if start_15 and end_15 else []

# Delete both sections (§1.3 and §1.5) from Cap 1
new_cap1 = lines[:start_13] + lines[end_13:start_15] + lines[end_15:]
print(f'Cap 1: {len(lines)} → {len(new_cap1)} lines (Δ={len(lines)-len(new_cap1)})')
cap1.write_text('\n'.join(new_cap1) + '\n', encoding='utf-8')

# Save the extracted contributions + alcance for the next step
Path('/tmp/extracted_13.md').write_text('\n'.join(contribuciones), encoding='utf-8')
Path('/tmp/extracted_15.md').write_text('\n'.join(alcance), encoding='utf-8')
print(f'\nExtracted content saved to /tmp/extracted_13.md ({len(contribuciones)} lines) and /tmp/extracted_15.md ({len(alcance)} lines)')
