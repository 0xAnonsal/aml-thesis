#!/usr/bin/env bash
# Compile the ZK circuit, run phase-2 trusted setup, export the Solidity
# verifier. Idempotent — skips steps with cached artifacts. Safe to re-run.
#
# Prereq: scripts/install_zk_tools.sh has run successfully and circom + snarkjs
# are on PATH. Re-run this script after editing circuits/.
#
# Output (under circuits/build/):
#   <circuit>.r1cs                   compiled constraint system
#   <circuit>_js/<circuit>.wasm      witness generator
#   <circuit>_final.zkey             phase-2 proving key (after one contribution)
#   verification_key.json            verification key in JSON
#   Verifier.sol                     auto-generated Solidity Groth16 verifier
set -euo pipefail

CIRCUIT="${1:-multiplier}"
ROOT="$(git rev-parse --show-toplevel)"
CIRCUITS="$ROOT/circuits"
BUILD="$CIRCUITS/build/$CIRCUIT"
PTAU="$CIRCUITS/ptau"
PTAU_FILE="$PTAU/phase2.ptau"

# pot=14 supports up to 2^14 = 16384 constraints. Comfortably above the
# trivial multiplier (1 constraint) and the real Tornado-style withdraw
# circuit we'll add in PR 4.2 (~4000 constraints with Merkle depth 20).
PTAU_POWER=14

mkdir -p "$BUILD" "$PTAU"

# --- 1. Phase-1 trusted setup ---------------------------------------------
# Generate locally with one contribution. The Hermez S3 mirror that the
# snarkjs docs recommend has been returning 403; for thesis-grade research
# a single local contribution is sufficient (we are not making security
# claims about the ceremony itself, only about whether the proof system
# verifies correctly). Takes ~30-60s for pot=14.
if [ ! -f "$PTAU_FILE" ]; then
    echo "[1/5] Generating phase-1 trusted setup locally (pot=$PTAU_POWER, ~30-60s) ..."
    if ! command -v snarkjs >/dev/null 2>&1; then
        echo "      ERROR: snarkjs not on PATH. Run: bash scripts/install_zk_tools.sh"
        echo "      then: source ~/.bashrc"
        exit 1
    fi
    pushd "$PTAU" >/dev/null
    # Don't suppress snarkjs output — we want to see progress and any errors.
    snarkjs powersoftau new bn128 "$PTAU_POWER" pot_0000.ptau
    snarkjs powersoftau contribute pot_0000.ptau pot_0001.ptau \
        --name="aml-thesis local" \
        -e="aml-thesis-$(date +%s)"
    snarkjs powersoftau prepare phase2 pot_0001.ptau phase2.ptau
    rm -f pot_0000.ptau pot_0001.ptau
    popd >/dev/null
    echo "      generated: $PTAU_FILE"
else
    echo "[1/5] phase-1 .ptau cached: $PTAU_FILE"
fi

# --- 2. Compile circuit -----------------------------------------------------
echo "[2/5] Compiling $CIRCUIT.circom ..."
(
    cd "$CIRCUITS"
    circom "$CIRCUIT.circom" --r1cs --wasm --sym -o "build/$CIRCUIT" >/dev/null
)
echo "      r1cs:  $BUILD/$CIRCUIT.r1cs"
echo "      wasm:  $BUILD/${CIRCUIT}_js/$CIRCUIT.wasm"

# --- 3. Phase-2 setup -------------------------------------------------------
ZKEY_0="$BUILD/${CIRCUIT}_0000.zkey"
ZKEY_FINAL="$BUILD/${CIRCUIT}_final.zkey"

if [ ! -f "$ZKEY_FINAL" ]; then
    echo "[3/5] Phase-2 setup (Groth16) ..."
    snarkjs groth16 setup "$BUILD/$CIRCUIT.r1cs" "$PTAU_FILE" "$ZKEY_0" >/dev/null
    # Single deterministic contribution. For production-grade research a
    # multi-party ceremony would replace this; for thesis experiments a
    # one-shot contribution is sufficient and well-documented.
    snarkjs zkey contribute "$ZKEY_0" "$ZKEY_FINAL" \
        --name="thesis test contribution" \
        -e="aml-thesis $CIRCUIT phase2 contribution" >/dev/null
    rm -f "$ZKEY_0"
else
    echo "[3/5] phase-2 zkey cached: $ZKEY_FINAL"
fi

# --- 4. Export verification key --------------------------------------------
VKEY="$BUILD/verification_key.json"
if [ ! -f "$VKEY" ] || [ "$ZKEY_FINAL" -nt "$VKEY" ]; then
    echo "[4/5] Exporting verification key ..."
    snarkjs zkey export verificationkey "$ZKEY_FINAL" "$VKEY" >/dev/null
fi
echo "      vkey: $VKEY"

# --- 5. Export Solidity verifier -------------------------------------------
VERIFIER="$BUILD/Verifier.sol"
if [ ! -f "$VERIFIER" ] || [ "$ZKEY_FINAL" -nt "$VERIFIER" ]; then
    echo "[5/5] Exporting Solidity verifier ..."
    snarkjs zkey export solidityverifier "$ZKEY_FINAL" "$VERIFIER" >/dev/null
fi
echo "      verifier: $VERIFIER"

echo
echo "=== $CIRCUIT setup complete ==="
echo "Run: pytest tests/test_zk_toolchain.py"
