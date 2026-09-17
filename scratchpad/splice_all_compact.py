"""Apply 3 compressions: Cap 4 whole, Cap 5.C block, Cap 7.A block."""
from pathlib import Path

TFM = Path('/home/anon/aml-thesis/tfm/chapters_v3')
SC = Path('/mnt/c/Users/Asus/AppData/Local/Temp/claude/C--Users-Asus-Downloads-files/2b4bd8c7-673e-4254-a0dc-583f5d6cecc2/scratchpad')

def splice(fname: str, start_marker: str, end_marker: str | None, compact_file: str):
    src = TFM / fname
    compact_path = SC / compact_file
    lines = src.read_text(encoding='utf-8').splitlines()
    compact_lines = compact_path.read_text(encoding='utf-8').splitlines()

    start = end = None
    for i, l in enumerate(lines):
        if l.startswith(start_marker) and start is None:
            start = i
        if end_marker and l.startswith(end_marker) and end is None and (start is not None and i > start):
            end = i
            break

    if end is None:
        end = len(lines)

    print(f'{fname}: {start_marker} .. {end_marker or "END"} = lines {start+1}..{end} ({end-start})')
    new_lines = lines[:start] + compact_lines + [''] + lines[end:]
    print(f'  {len(lines)} → {len(new_lines)} (Δ={len(lines)-len(new_lines)})')
    src.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')

# 1. Cap 4: replace whole file (from line 1)
splice('04_analisis_tecnologias.md', '# Cap', None, 'compact_cap4.md')

# 2. Cap 5.C: from "## 5.C " to "## 5.D "
splice('05_diseno_dataset.md', '## 5.C ', '## 5.D ', 'compact_5C.md')

# 3. Cap 7.A: from "## 7.A " to "## 7.B "
splice('07_visibilidad_conclusiones.md', '## 7.A ', '## 7.B ', 'compact_7A.md')

print('\nFinal line counts:')
import subprocess
subprocess.run(['wc', '-l', str(TFM) + '/01_introduccion.md', str(TFM) + '/02_estado_arte_conceptos.md',
                str(TFM) + '/03_limitaciones_previas.md', str(TFM) + '/04_analisis_tecnologias.md',
                str(TFM) + '/05_diseno_dataset.md', str(TFM) + '/06_experimentos_resultados.md',
                str(TFM) + '/07_visibilidad_conclusiones.md', str(TFM) + '/08_bibliografia.md',
                str(TFM) + '/A_anexos.md'])
