# Auditoría de citas — TFM AML

Documento de trabajo interno. Preserva la trazabilidad de las citas
verificadas y las correcciones aplicadas respecto a la memoria del
proyecto anterior. NO forma parte del draft entregable; se convertirá
en `tfm/references.bib` (BibTeX estándar) antes del submit final.

## Citas verificadas contra Google Scholar / arXiv / ACM DL (2026-08-03)

- **Weber et al. 2019** (Elliptic): confirmado, arXiv:1908.02591
- **Elmougy y Liu, KDD 2023** (Elliptic++): confirmado, arXiv:2306.06108
  - Corrección: memoria del proyecto tenía "Bellei 2024", es incorrecto.
- **Wu et al. IEEE TIFS 2023** (EthereumHeist): confirmado, arXiv:2305.14748
- **Altman, Blanuša, Egressy et al. NeurIPS 2023** (AMLWorld): confirmado,
  arXiv:2306.16424
  - Corrección: era "Egressy 2024", es Altman et al. 2023.
- **OpenAML**: NO es paper formal — proyecto FINOS/Duke, Juvinski como
  maintainer principal.
- **Sun, Wu et al. ICSE 2024** (GPTScan): confirmado, arXiv:2308.03314
  - Corrección: era "David 2023".
- **Wu, Bansal et al. 2023** (AutoGen): confirmado, arXiv:2308.08155
  - Corrección: año era "2024".
- **Fatemi, Halcrow, Perozzi ICLR 2024** (*Talk like a graph*): confirmado,
  arXiv:2402.05862
- **Béres, Seres, Benczúr, Quintyne-Collins IEEE DAPPS 2021**: confirmado
- **Wallet Fingerprints ACM SAC 2025**: confirmado,
  [dl.acm.org/10.1145/3672608.3707896](https://dl.acm.org/10.1145/3672608.3707896)
- **Hide-and-seek multi-agent AML**: Springer *Complex & Intelligent
  Systems* 2025

## Citas eliminadas (fabricadas o no verificables)

Estas citas aparecían en la memoria del proyecto previo pero no se
pudieron verificar contra ninguna fuente académica (arXiv, Google
Scholar, DBLP, ACM DL, IEEE Xplore). Se eliminaron del draft para no
introducir afirmaciones no verificables:

- Vasan 2023
- Sun 2024 "GPT-4 launderer"
- Chen 2024 "SmartLLM"
- Zhou 2024 "AuditGPT"
- Chen 2024 "tabular"
- Rezaei 2024
- Wang 2023 "Tornado"

## Próximo paso

Compilar `tfm/references.bib` en formato BibTeX estándar antes del
submit del TFM, con las claves consistentes con las que se usan en
`\cite{}` a lo largo de los capítulos.
