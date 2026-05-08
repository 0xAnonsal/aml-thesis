#!/usr/bin/env bash
# Compile a ZK circuit, run phase-2 trusted setup, export the Solidity
# verifier. Idempotent — skips steps with cached artifacts. Safe to re-run.
#
# Prereq: scripts/install_zk_tools.sh has run successfully and circom + snarkjs
# are on PATH. Re-run this script after editing circuits/.
#
# Output (under circuits/build/<circuit>/):
#   <circuit>.r1cs                   compiled constraint system
#   <circuit>_js/<circuit>.wasm      witness generator
#   <circuit>_final.zkey             phase-2 proving key
#   verification_key.json            verification key in JSON
#   Verifier.sol                     auto-generated Solidity Groth16 verifier
set -eo pipefail   # NB: no -u — sourced shell tooling may have unbound vars

CIRCUIT="${1:-multiplier}"
ROOT="$(git rev-parse --show-toplevel)"
CIRCUITS="$ROOT/circuits"
BUILD="$CIRCUITS/build/$CIRCUIT"
PTAU="$CIRCUITS/ptau"
PTAU_FILE="$PTAU/phase2.ptau"

# pot=14 supports up to 2^14 = 16384 constraints. Comfortably above the
# trivial multiplier (1 constraint) and the real Tornado-style withdraw
# circuit (~4000 constraints with Merkle depth 20).
PTAU_POWER=14

mkdir -p "$BUILD" "$PTAU"

# --- 0. JS dependencies (circomlib for circuit includes, circomlibjs for ---
#                         off-chain hashing in tests)
if [ ! -d "$ROOT/node_modules/circomlib" ] || [ ! -d "$ROOT/node_modules/circomlibjs" ]; then
    echo "[0/5] Installing JS dependencies (circomlib, circomlibjs) ..."
    if ! command -v npm >/dev/null 2>&1; then
        echo "      ERROR: npm not on PATH. Run: bash scripts/install_zk_tools.sh"
        echo "      then: source ~/.bashrc"
        exit 1
    fi
    (cd "$ROOT" && npm install --silent)
else
    echo "[0/5] node_modules cached: $ROOT/node_modules"
fi

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
    # -l flag adds an include search path. Lets circuits use
    #   include "circomlib/circuits/mimcsponge.circom";
    # rather than relative paths into node_modules.
    circom "$CIRCUIT.circom" -l "$ROOT/node_modules" \
        --r1cs --wasm --sym -o "build/$CIRCUIT" >/dev/null
)
echo "      r1cs:  $BUILD/$CIRCUIT.r1cs"
echo "      wasm:  $BUILD/${CIRCUIT}_js/$CIRCUIT.wasm"

# --- 3. Phase-2 setup -------------------------------------------------------
ZKEY_0="$BUILD/${CIRCUIT}_0000.zkey"
ZKEY_FINAL="$BUILD/${CIRCUIT}_final.zkey"

if [ ! -f "$ZKEY_FINAL" ]; then
    echo "[3/5] Phase-2 setup (Groth16) ..."
    snarkjs groth16 setup "$BUILD/$CIRCUIT.r1cs" "$PTAU_FILE" "$ZKEY_0" >/dev/null
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

# For the withdraw circuit, also copy the verifier into contracts/ so
# Foundry can compile it for on-chain verification tests. The file is
# gitignored — every developer's verifier is specific to their local
# trusted-setup contribution.
if [ "$CIRCUIT" = "withdraw" ]; then
    cp "$VERIFIER" "$ROOT/contracts/Verifier.sol"
    echo "      copied to contracts/Verifier.sol"
fi

echo
echo "=== $CIRCUIT setup complete ==="
echo "Run: pytest tests/"
