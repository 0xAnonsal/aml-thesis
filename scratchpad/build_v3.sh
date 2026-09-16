#!/bin/bash
# Build tfm/chapters_v3/ from v2 by concatenation and header replacement.
# Preserves all §X.Y section numbers as legacy identifiers.

set -e
V2=/home/anon/aml-thesis/tfm/chapters_v2
V3=/home/anon/aml-thesis/tfm/chapters_v3
mkdir -p $V3

# --- Chapter 1: Introducción ---
sed '1s/# Capítulo 1 — Introducción/# Capítulo 1 — Introducción, contexto, motivación y objetivos/' \
    $V2/01_introduccion.md > $V3/01_introduccion.md

# --- Chapter 2: Estado del arte y conceptos previos ---
{
    echo "# Capítulo 2 — Estado del arte y conceptos previos"
    echo ""
    echo "Este capítulo cubre (a) los trabajos previos relevantes en detección"
    echo "AML sobre criptomonedas y agentes LLM, y (b) los conceptos técnicos"
    echo "necesarios para entender los capítulos posteriores: LLMs, AI aplicada"
    echo "a crypto laundering, arquitectura de exchanges, y las herramientas de"
    echo "prueba en Sepolia y Anvil que este trabajo utiliza."
    echo ""
    tail -n +2 $V2/02_analisis_comparaciones.md
} > $V3/02_estado_arte_conceptos.md

# --- Chapter 3: Limitaciones del estado del arte (NEW, written below) ---
# We'll write this file separately via Write tool.
touch $V3/03_limitaciones_previas.md

# --- Chapter 4: Análisis del problema y tecnologías ---
sed '1s/# Capítulo 3 — Tecnologías/# Capítulo 4 — Análisis del problema y tecnologías/' \
    $V2/03_tecnologias.md > $V3/04_analisis_tecnologias.md

# --- Chapter 5: Diseño, dataset y lenguajes (merged from 4, 5, 6, 9) ---
{
    echo "# Capítulo 5 — Diseño, dataset y lenguajes"
    echo ""
    echo "Este capítulo reúne el diseño arquitectónico del sistema, los"
    echo "lenguajes empleados en la implementación, y la especificación de"
    echo "datasets y parámetros. Se divide en cuatro bloques principales:"
    echo ""
    echo "- **§5.A Diseño y diagramas** — vista general del sistema, arquitecturas atacante y defensor, flujo end-to-end."
    echo "- **§5.B Arquitectura de software** — capas de contratos, blockchain, herramientas y coordinación multi-agente."
    echo "- **§5.C Lenguajes empleados** — Python, Solidity, Circom, JavaScript, Bash, Markdown con LOC por lenguaje."
    echo "- **§5.D Datasets y parámetros** — datasets propios y externos, particionado federado, configuración de detectores y LLMs."
    echo ""
    echo "---"
    echo ""
    echo "## 5.A Diseño y diagramas del sistema"
    tail -n +2 $V2/04_diseno_diagrama.md
    echo ""
    echo "---"
    echo ""
    echo "## 5.B Arquitectura del software"
    tail -n +2 $V2/06_arquitectura_software.md
    echo ""
    echo "---"
    echo ""
    echo "## 5.C Lenguajes de programación empleados"
    tail -n +2 $V2/05_lenguajes_usados.md
    echo ""
    echo "---"
    echo ""
    echo "## 5.D Datasets y parámetros"
    tail -n +2 $V2/09_datasets_parametros.md
} > $V3/05_diseno_dataset.md

# --- Chapter 6: Experimentos y resultados (attack + defense analysis) ---
sed '1s/# Capítulo 8 — Implementación y pruebas/# Capítulo 6 — Experimentos y resultados (attack primero, luego defense)/' \
    $V2/08_implementacion_pruebas.md > $V3/06_experimentos_resultados.md

# --- Chapter 7: Visibilidad parcial (novedad) + conclusiones + findings ---
{
    echo "# Capítulo 7 — Visibilidad parcial, conclusiones y findings"
    echo ""
    echo "Este capítulo cierra el TFM con (a) la argumentación de la novedad"
    echo "central del trabajo — visibilidad parcial federada por exchange,"
    echo "el patrón que ningún trabajo previo ha explorado — y (b) las"
    echo "conclusiones, decisiones clave de diseño, findings publishable y"
    echo "reflexión final."
    echo ""
    echo "---"
    echo ""
    echo "## 7.A Decisiones clave de diseño"
    tail -n +2 $V2/07_decisiones.md
    echo ""
    echo "---"
    echo ""
    echo "## 7.B Conclusiones, findings y trabajo futuro"
    tail -n +2 $V2/10_conclusiones.md
} > $V3/07_visibilidad_conclusiones.md

# --- Anexos (unchanged) ---
cp $V2/A_anexos.md $V3/A_anexos.md

echo "Built v3:"
wc -l $V3/*.md
