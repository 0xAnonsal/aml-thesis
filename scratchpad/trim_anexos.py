"""Trim anexos: keep A (prompts), F (repo), G (attributions), H (AI declaration).
Drop B (repeated in Ch), C (in repo), D (in repo), E (in repo).
"""
from pathlib import Path

src = Path('/home/anon/aml-thesis/tfm/chapters_v3/A_anexos.md')
lines = src.read_text(encoding='utf-8').splitlines()

# Locate anexo boundaries
anexo_starts = {}
for i, l in enumerate(lines):
    if l.startswith('## Anexo '):
        letter = l.split()[2]  # A, B, C, ...
        anexo_starts[letter] = i

# Build a new list keeping A + F + G + H
# The header (lines 0..anexo_starts['A']-1) is kept
header = lines[:anexo_starts['A']]
kept_letters = ['A', 'F', 'G', 'H']
new_body = []

# Compute end of each anexo (start of next OR end of file)
letters_ordered = sorted(anexo_starts.keys())
for letter in kept_letters:
    if letter not in anexo_starts:
        continue
    start = anexo_starts[letter]
    # Find next anexo start
    next_starts = [anexo_starts[l] for l in letters_ordered if anexo_starts[l] > start]
    end = min(next_starts) if next_starts else len(lines)
    section = lines[start:end]
    new_body.extend(section)

# Insert a note about dropped anexos
note = [
    "> **Nota**: los Anexos B (prompt defensor), C (catálogo íntegro de",
    "> herramientas), D (contratos desplegados) y E (comandos de reproducción)",
    "> del draft previo se han retirado por brevedad. Todo su contenido está",
    "> disponible en el repositorio público",
    "> [`github.com/0xAnonsal/aml-thesis`](https://github.com/0xAnonsal/aml-thesis):",
    "> el prompt del defensor está en `src/aml/detectors/multi_agent.py:_LLM_COORDINATOR_SYSTEM_PROMPT`,",
    "> el catálogo de herramientas en `src/aml/attackers/tools.py`, los",
    "> contratos verificados en Sepolia Etherscan (ver Anexo F), y los",
    "> comandos exactos en `scripts/` y `scratchpad/`.",
    "",
]

new_lines = header + note + new_body
print(f'Total lines: {len(lines)} → {len(new_lines)} (reduction {len(lines)-len(new_lines)})')
src.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
print(f'Wrote {src}')
