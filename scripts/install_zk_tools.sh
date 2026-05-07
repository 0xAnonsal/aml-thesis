#!/usr/bin/env bash
# Install the ZK proving stack: Node.js (via nvm), snarkjs, circom.
# Idempotent — skips steps already complete. Safe to re-run.
#
# Run once on a fresh checkout:
#   bash scripts/install_zk_tools.sh
#
# After it completes (and after `source ~/.bashrc`), `node`, `npm`,
# `snarkjs`, and `circom` are all on PATH.
set -eo pipefail   # NB: no -u — nvm.sh references unbound variables internally

echo "=== ZK toolchain install ==="

# --- 1. Node.js via nvm ----------------------------------------------------
if [ ! -d "$HOME/.nvm" ]; then
    echo "[1/3] Installing nvm..."
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash
fi
export NVM_DIR="$HOME/.nvm"
# shellcheck source=/dev/null
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# Always install + activate nvm's LTS Node, even if a system Node exists.
# Otherwise system Node's npm tries to write to /usr/lib (requires sudo)
# while nvm-managed Node uses a per-user directory and works without root.
echo "[1/3] Activating Node.js LTS via nvm..."
nvm install --lts >/dev/null 2>&1
nvm use --lts >/dev/null
echo "    node:    $(node --version)   [$(command -v node)]"
echo "    npm:     $(npm --version)"

# --- 2. snarkjs ------------------------------------------------------------
if ! command -v snarkjs >/dev/null 2>&1; then
    echo "[2/3] Installing snarkjs (global)..."
    npm install -g snarkjs >/dev/null
fi
echo "    snarkjs: $(snarkjs --version 2>&1 | head -1)"

# --- 3. circom (Rust build) ------------------------------------------------
if ! command -v circom >/dev/null 2>&1; then
    echo "[3/3] Installing circom 2.x (Rust build, ~5 min on first run)..."
    if ! command -v cargo >/dev/null 2>&1; then
        echo "       installing Rust toolchain first..."
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y >/dev/null
        # shellcheck source=/dev/null
        . "$HOME/.cargo/env"
    fi

    BUILD_DIR=$(mktemp -d)
    trap 'rm -rf "$BUILD_DIR"' EXIT
    git clone --depth 1 --branch v2.1.9 https://github.com/iden3/circom.git "$BUILD_DIR/circom" >/dev/null 2>&1
    (
        cd "$BUILD_DIR/circom"
        cargo build --release
    )

    INSTALL_PATH="$HOME/.local/bin"
    mkdir -p "$INSTALL_PATH"
    install "$BUILD_DIR/circom/target/release/circom" "$INSTALL_PATH/circom"

    if ! echo "$PATH" | grep -q "$INSTALL_PATH"; then
        echo "    NOTE: $INSTALL_PATH is not on PATH. Add this to ~/.bashrc:"
        echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi
    export PATH="$INSTALL_PATH:$PATH"
fi
echo "    circom:  $(circom --version | head -1)"

echo
echo "=== ZK toolchain ready ==="
echo "Next: bash scripts/setup_zk.sh    # compiles the test circuit + runs trusted setup"
