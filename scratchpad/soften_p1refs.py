"""Soften prominent P1-XX inline mentions to descriptive prose.
Keep tables/section headings intact (they anchor commit refs).
Target: reduce jarring appearances in prose paragraphs."""
from pathlib import Path
import re

REPLACEMENTS = [
    # Cap 5 novelty + design — soften citation-like mentions in prose
    (r'con el algoritmo P1-71 \(§8\.9\.G\)', r'con el algoritmo post-hoc de merge por centroide (§8.9.G)'),
    (r'\(P1-73, §8\.9\.H\)', r'(auto-tune por silhouette, §8.9.H)'),
    (r'el post-procesamiento P1-71 \(§8\.9\.G\)', r'el post-procesamiento code-level (§8.9.G)'),
    # Cap 7 A/B — descriptive labels
    (r'la intervención P1-71 \(post-hoc cluster merge\)',
     r'la intervención de post-hoc cluster merge'),
    (r'el post-procesamiento code-level \(P1-71\)',
     r'el post-procesamiento code-level determinístico'),
    # Cap 6 §8.9 — soften some inline prose (leave tables + section headings)
    (r'La ablation P1-70 sobre prompt',
     r'La ablation sobre prompt'),
    # Legacy dev codes appearing as anti-pattern
    (r'\(P1-42, §8\.9\.[A-Z]\)', r'(§8.9.G)'),
]

targets = [
    '/home/anon/aml-thesis/tfm/chapters_v3/05_diseno_dataset.md',
    '/home/anon/aml-thesis/tfm/chapters_v3/06_experimentos_resultados.md',
    '/home/anon/aml-thesis/tfm/chapters_v3/07_visibilidad_conclusiones.md',
]

for fpath in targets:
    p = Path(fpath)
    text = p.read_text(encoding='utf-8')
    orig = text
    for pat, repl in REPLACEMENTS:
        text = re.sub(pat, repl, text)
    if text != orig:
        n_changes = sum(len(re.findall(pat, orig)) for pat, _ in REPLACEMENTS)
        p.write_text(text, encoding='utf-8')
        print(f'{p.name}: applied {n_changes} soft replacements')
    else:
        print(f'{p.name}: no matches')

# Count remaining P1- occurrences per file
print('\nRemaining P1-XX references (mostly in tables/section headings — intentional):')
for fpath in targets:
    n = Path(fpath).read_text().count('P1-')
    print(f'  {Path(fpath).name}: {n}')
