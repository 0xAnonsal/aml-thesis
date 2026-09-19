"""Final polish of chapter numbering:
- Cap 4 (05_diseno_dataset.md): §9.X → §4.X (datasets sub-sections)
- Cap 6 (07_visibilidad_conclusiones.md): flatten letters to numeric
  §6.A → §6.1, §6.B → §6.2 (with sub-sections), §6.C → §6.3
"""
from pathlib import Path
import re

TFM = Path('/home/anon/aml-thesis/tfm/chapters_v3')

# --- Cap 4 (diseño): §9.X → §4.X ---
c4 = TFM / '05_diseno_dataset.md'
t = c4.read_text(encoding='utf-8')
# ## 9.1-9.6 → ## 4.G.1-4.G.6 (they're sub-sections of the datasets/params block)
# But existing §5.A/B/C/D/E/F use letters. Merge §9.X under §5.D as sub-sections.
# Simpler: renumber §9.X → §4.G, §4.H, etc? No — keep as §4.D.N (dataset section)
# Cleanest: renumber §9.1-9.6 → §4.D.1-4.D.6 (sub-sections of Datasets y parámetros)
t = re.sub(r'^## 9\.([1-6])\b', r'### 4.D.\1', t, flags=re.MULTILINE)
# Also update §5.A/B/C/D/E/F headings to §4.A/B/C/D/E/F
t = re.sub(r'^## 5\.([ABCDEF])\b', r'## 4.\1', t, flags=re.MULTILINE)
# Novedad §5.0 stays as ## 4.0 since Cap 4 now
t = re.sub(r'^## 5\.0\b', r'## 4.0', t, flags=re.MULTILINE)
t = re.sub(r'^### 5\.0\.([1-5])\b', r'### 4.0.\1', t, flags=re.MULTILINE)
# Update inline refs to §5.A/B/C/D/E/F → §4.A/B/C/D/E/F within this file
t = re.sub(r'§5\.([ABCDEF])', r'§4.\1', t)
t = re.sub(r'§5\.0(\.[1-5])?', r'§4.0\1', t)
c4.write_text(t, encoding='utf-8')
print('Cap 4 (diseño): §9.X → §4.D.X + §5.A-F → §4.A-F')

# Also fix cross-refs in other files
for f in TFM.glob('*.md'):
    if f.name == '05_diseno_dataset.md':
        continue
    t = f.read_text(encoding='utf-8')
    orig = t
    t = re.sub(r'§5\.([ABCDEF])(?![\d.])', r'§4.\1', t)
    t = re.sub(r'§5\.0(\.[1-5])?', r'§4.0\1', t)
    if t != orig:
        f.write_text(t, encoding='utf-8')
        print(f'{f.name}: cross-refs §5.A-F → §4.A-F updated')

# --- Cap 6 (visibilidad): flatten §6.A/B/C mix ---
c6 = TFM / '07_visibilidad_conclusiones.md'
t = c6.read_text(encoding='utf-8')
# The letter labels + numeric labels are confusing. Structure was:
# §6.A Decisiones + §6.B Conclusiones (with §6.1-6.5 inside) + §6.C Competencias
# Flatten to sequential numeric:
# §6.A → §6.1 Decisiones de diseño
# §6.B → §6.2 Conclusiones (with §6.2.1-6.2.5 inside — was §6.1-6.5)
# §6.C → §6.3 Justificación de competencias

# First save current §6.1-6.5 titles (inside §6.B)
# Then renumber:
# Old §6.1 → §6.2.1
# Old §6.2 → §6.2.2
# Old §6.3 → §6.2.3
# Old §6.4 → §6.2.4 (contains §6.4.A/B/C/D + §6.4.28 + §6.4.29)
# Old §6.5 → §6.2.5

# Approach: do it in TWO passes to avoid double-substitution
# Pass 1: temporarily mark §6.A/B/C
t = re.sub(r'^## 6\.A\b', r'## TEMP_A', t, flags=re.MULTILINE)
t = re.sub(r'^## 6\.B\b', r'## TEMP_B', t, flags=re.MULTILINE)
t = re.sub(r'^## 6\.C\b', r'## TEMP_C', t, flags=re.MULTILINE)
t = re.sub(r'§6\.A\b', r'§TEMP_A', t)
t = re.sub(r'§6\.B\b', r'§TEMP_B', t)
t = re.sub(r'§6\.C\b', r'§TEMP_C', t)

# Pass 2: renumber §6.N → §6.2.N (old numeric sub-sections)
t = re.sub(r'^## 6\.(\d+)\b', r'### 6.2.\1', t, flags=re.MULTILINE)
t = re.sub(r'§6\.(\d+)\b', r'§6.2.\1', t)
# §6.4.29 that was ## → ### under §6.2.4
t = re.sub(r'^## 6\.4\.29\b', r'#### 6.2.4.29', t, flags=re.MULTILINE)
# §6.4.28 same treatment
t = re.sub(r'^### 6\.4\.28\b', r'#### 6.2.4.28', t, flags=re.MULTILINE)
# §6.4.A-D sub-sections (from §10.4.A-D)
t = re.sub(r'^### 6\.4\.([ABCD])\b', r'#### 6.2.4.\1', t, flags=re.MULTILINE)

# Pass 3: convert TEMP → new numbers
t = t.replace('## TEMP_A', '## 6.1')
t = t.replace('## TEMP_B', '## 6.2')
t = t.replace('## TEMP_C', '## 6.3')
t = t.replace('§TEMP_A', '§6.1')
t = t.replace('§TEMP_B', '§6.2')
t = t.replace('§TEMP_C', '§6.3')

c6.write_text(t, encoding='utf-8')
print('Cap 6: flattened §6.A/B/C → §6.1/6.2/6.3 with sub-sections')

# Show current heading structure of Cap 6
print('\nCap 6 current headings:')
import subprocess
subprocess.run(['grep', '-nE', r'^##+ 6\.', str(c6)])

print('\nCap 4 current headings:')
subprocess.run(['grep', '-nE', r'^##+ 4\.', str(c4)])
