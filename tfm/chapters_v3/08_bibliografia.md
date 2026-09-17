# Bibliografía

Todas las referencias han sido verificadas contra arXiv, ACM Digital
Library, IEEE Xplore o el DOI oficial del editor. El archivo BibTeX
íntegro está publicado en `tfm/references.bib` del repositorio.

## Datasets AML

[1] Weber, M., Domeniconi, G., Chen, J., Weidele, D. K. I., Bellei,
C., Robinson, T., & Leiserson, C. E. (2019). *Anti-Money Laundering
in Bitcoin: Experimenting with Graph Convolutional Networks for
Financial Forensics*. KDD '19 Workshop on Anomaly Detection in
Finance. arXiv:1908.02591.

[2] Elmougy, Y., & Liu, L. (2023). *Demystifying Fraudulent
Transactions and Illicit Nodes in the Bitcoin Network for Financial
Forensics*. Proc. 29th ACM SIGKDD Conference on Knowledge Discovery
and Data Mining (KDD '23), pp. 5040-5050.
DOI: 10.1145/3580305.3599803. arXiv:2306.06108. Dataset Elliptic++:
822k Bitcoin wallets, 1.27M temporal interactions. Repo:
`github.com/git-disl/EllipticPlusPlus`.

[3] Wu, J., Lin, D., Fu, Q., Yang, S., Chen, T., Zheng, Z., & Song,
B. (2023). *Toward Understanding Asset Flows in Crypto Money
Laundering Through the Lenses of Ethereum Heists*. IEEE Transactions
on Information Forensics and Security, 19, 1994-2009.
DOI: 10.1109/TIFS.2023.3346276. arXiv:2305.14748. Dataset
EthereumHeist: 23 real ETH heist campaigns.

[4] Altman, E., Blanuša, J., von Niederhäusern, L., Egressy, B.,
Anghel, A., & Atasu, K. (2023). *Realistic Synthetic Financial
Transactions for Anti-Money Laundering Models*. Advances in Neural
Information Processing Systems (NeurIPS) Datasets and Benchmarks
Track. arXiv:2306.16424. Dataset AMLWorld: 10⁷ synthetic
transactions.

[5] OpenAML Project (FINOS). (2025). *OpenAML: Open and Intelligent
Compliance for On-Chain Anti-Money Laundering*. Software repository,
Linux Foundation FINOS.
`github.com/finos-labs/dtcch-2025-OpenAML`. Winning project of DTCC
AI Hackathon 2025. Maintainer: Luciano Juvinski.

## Marco regulatorio

[6] Financial Action Task Force (FATF). (2021). *Virtual Assets
Red Flag Indicators of Money Laundering and Terrorist Financing*.
FATF/OECD.

[7] European Union. (2023). *Regulation (EU) 2023/1114 on markets in
crypto-assets (MiCA)*. Official Journal of the European Union.

[8] European Union. (2023). *Regulation (EU) 2023/1113 on
information accompanying transfers of funds and certain
crypto-assets*. Official Journal of the European Union.

## Detectores, GNNs y métricas

[9] Kipf, T. N., & Welling, M. (2017). *Semi-Supervised
Classification with Graph Convolutional Networks*. 5th International
Conference on Learning Representations (ICLR). arXiv:1609.02907.

[10] Blondel, V. D., Guillaume, J.-L., Lambiotte, R., & Lefebvre, E.
(2008). *Fast unfolding of communities in large networks*. Journal
of Statistical Mechanics: Theory and Experiment, 2008(10), P10008.
DOI: 10.1088/1742-5468/2008/10/P10008.

[11] Hubert, L., & Arabie, P. (1985). *Comparing partitions*.
Journal of Classification, 2(1), 193-218.

[12] Rosenberg, A., & Hirschberg, J. (2007). *V-Measure: A
Conditional Entropy-Based External Cluster Evaluation Measure*.
Proc. 2007 Joint Conference on Empirical Methods in Natural Language
Processing and Computational Natural Language Learning (EMNLP-CoNLL),
pp. 410-420.

[13] Breiman, L. (2001). *Random Forests*. Machine Learning, 45(1),
5-32. DOI: 10.1023/A:1010933404324.

[14] Rousseeuw, P. J. (1987). *Silhouettes: a graphical aid to the
interpretation and validation of cluster analysis*. Journal of
Computational and Applied Mathematics, 20, 53-65.
DOI: 10.1016/0377-0427(87)90125-7.

## LLMs, agentes y razonamiento sobre grafos

[15] Sun, Y., Wu, D., Xue, Y., Liu, H., Wang, H., Xu, Z., Xie, X.,
& Liu, Y. (2024). *GPTScan: Detecting Logic Vulnerabilities in
Smart Contracts by Combining GPT with Program Analysis*. Proc.
IEEE/ACM 46th International Conference on Software Engineering
(ICSE 2024). DOI: 10.1145/3597503.3639117. arXiv:2308.03314.

[16] Fatemi, B., Halcrow, J., & Perozzi, B. (2024). *Talk like a
Graph: Encoding Graphs for Large Language Models*. 12th
International Conference on Learning Representations (ICLR).
arXiv:2310.04560.

[17] Wu, Q., Bansal, G., Zhang, J., Wu, Y., Zhang, S., Zhu, E., Li,
B., Jiang, L., Zhang, X., & Wang, C. (2023). *AutoGen: Enabling
Next-Gen LLM Applications via Multi-Agent Conversation Framework*.
arXiv:2308.08155.

[18] Park, J. S., O'Brien, J. C., Cai, C. J., Morris, M. R., Liang,
P., & Bernstein, M. S. (2023). *Generative Agents: Interactive
Simulacra of Human Behavior*. Proc. 36th Annual ACM Symposium on
User Interface Software and Technology (UIST). arXiv:2304.03442.

[19] Anthropic. (2026). *Claude Sonnet 4.6 and Haiku 4.5 model
cards*. `anthropic.com/model-cards`. Modelos LLM utilizados como
coordinador defensor y atacante.

## Tornado Cash, zk-SNARKs y deanonimización

[20] Pertsev, A., Semenov, R., & Storm, R. (2019). *Tornado Cash
Privacy Solution Version 1.4*. Whitepaper.

[21] Béres, F., Seres, I. A., Benczúr, A. A., & Quintyne-Collins, M.
(2021). *Blockchain is Watching You: Profiling and Deanonymizing
Ethereum Users*. IEEE International Conference on Decentralized
Applications and Infrastructures (DAPPS 2021).

[22] Anonymous. (2025). *Attacking Anonymity Set in Tornado Cash via
Wallet Fingerprints*. Proc. 40th ACM/SIGAPP Symposium on Applied
Computing (SAC 2025). DOI: 10.1145/3672608.3707896. Reduce el
anonymity set de Tornado Cash ~37 % vía fingerprinting de wallet
software.

[23] Groth, J. (2016). *On the Size of Pairing-Based Non-Interactive
Arguments*. EUROCRYPT 2016, Lecture Notes in Computer Science, vol.
9666, pp. 305-326. DOI: 10.1007/978-3-662-49896-5_11. Sistema de
prueba Groth16 empleado en el circuit ZK del mezclador.

## Simuladores adversariales AML multi-agente

[24] Complex & Intelligent Systems authors. (2025). *Hide and Seek
in Transaction Networks: A Multi-Agent Framework for Simulating and
Detecting Money Laundering Activities*. Complex & Intelligent
Systems. DOI: 10.1007/s40747-025-01913-w.

[25] AMLNet authors. (2025). *AMLNet: A Knowledge-Based Multi-Agent
Framework to Generate and Detect Realistic Money Laundering
Transactions*. arXiv:2509.11595.

## Herramientas y frameworks

[26] Foundry Framework. (2023). *Foundry: A blazing fast, portable
and modular toolkit for Ethereum application development*.
`getfoundry.sh`.

[27] Chainalysis. (2025). *Crypto Crime Trends 2025*. Chainalysis
Annual Report. Fuente del dato «>84 % del volumen de fraude cripto
en 2025 concentrado en USDT/USDC» citado en §1.1.

[28] Hagberg, A. A., Schult, D. A., & Swart, P. J. (2008).
*Exploring network structure, dynamics, and function using
NetworkX*. Proceedings of the 7th Python in Science Conference
(SciPy2008), pp. 11-15.

[29] Fey, M., & Lenssen, J. E. (2019). *Fast Graph Representation
Learning with PyTorch Geometric*. ICLR Workshop on Representation
Learning on Graphs and Manifolds. arXiv:1903.02428.

[30] Iancu, M. Y., et al. (2020). *snarkjs: JavaScript
implementation of the Groth16 zk-SNARK protocol*. Software
repository, iden3. `github.com/iden3/snarkjs`.

## Otras fuentes citadas

[31] Universidad Carlos III de Madrid. (2024). *Normativa Propia
Trabajo Fin de Máster — Máster Universitario en Tecnologías del
Sector Financiero (FinTech)*. Documento institucional.

[32] Universidad Carlos III de Madrid. (2019). *Directrices para la
Organización y Evaluación de las asignaturas de Trabajo Fin de
Estudios*. BOEL de 5 de diciembre de 2019, Consejo de Gobierno de
14 de noviembre de 2019.

[33] Universidad Carlos III de Madrid. *Guía Básica de Actuación
para los Supuestos de Tratamiento de Datos Personales en el Proceso
de Elaboración de una Tesis Doctoral o un Trabajo Fin de Titulación*.
Delegado de Protección de Datos UC3M. Disponible en
`www.uc3m.es/protecciondedatos`.
