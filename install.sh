#!/usr/bin/env bash
# Marcel installer
#
# Installs the Rust CLI binary and (optionally) bootstraps the Docker-based
# production server.
#
# Usage (from repo):
#   ./install.sh                                    # CLI only
#   ./install.sh --server                           # CLI + Docker server
#   ./install.sh --host 192.168.1.10 --port 7420 --user alice
#
# Usage (via curl):
#   curl -fsSL https://raw.githubusercontent.com/shbunder/marcel/main/install.sh | bash
#   curl -fsSL ... | bash -s -- --server

set -euo pipefail

HOST=""
PORT=""
USER_SLUG=""
CLEANUP_DIR=""
SERVER=false

# Parse optional args
while [[ $# -gt 0 ]]; do
    case $1 in
        --host) HOST="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --user) USER_SLUG="$2"; shift 2 ;;
        --server) SERVER=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Determine source directory — either we're in the repo or we need to clone it
if [[ -f "${BASH_SOURCE[0]:-}" ]] && [[ -d "$(dirname "${BASH_SOURCE[0]}")/src/marcel_cli" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_DIR="$SCRIPT_DIR"
    SRC_DIR="$SCRIPT_DIR/src/marcel_cli"
else
    # Running via curl pipe — clone to a temp directory
    echo "Cloning Marcel repository..."
    CLEANUP_DIR="$(mktemp -d)"
    git clone --depth 1 https://github.com/shbunder/marcel.git "$CLEANUP_DIR"
    REPO_DIR="$CLEANUP_DIR"
    SRC_DIR="$CLEANUP_DIR/src/marcel_cli"
    trap 'rm -rf "$CLEANUP_DIR"' EXIT
fi

# ── CLI installation ──────────────────────────────────────────────────────

# Check for Rust toolchain
if ! command -v cargo &>/dev/null; then
    echo "Rust toolchain not found. Installing via rustup..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    # shellcheck source=/dev/null
    source "$HOME/.cargo/env"
fi

echo "Building Marcel CLI..."
cd "$SRC_DIR"
cargo install --path .
echo "Installed marcel binary to ~/.cargo/bin/marcel"

# ── Config setup ──────────────────────────────────────────────────────────

CONFIG_DIR="$HOME/.marcel"
CONFIG_FILE="$CONFIG_DIR/config.toml"

mkdir -p "$CONFIG_DIR"

# Write config only if it doesn't already exist
if [[ ! -f "$CONFIG_FILE" ]]; then
    cat > "$CONFIG_FILE" <<TOML
# Marcel server address (the machine running marcel-core, e.g. your NUC)
host = "${HOST:-localhost}"
port = ${PORT:-7420}

# Your user slug
user = "${USER_SLUG:-$(whoami)}"

# Long-lived developer token (auth not yet enforced in Phase 1)
token = ""

# Claude model to use
model = "claude-sonnet-4-6"

# Dev server port (used by \`marcel --dev\`)
dev_port = 7421
TOML
    echo "Config written to $CONFIG_FILE"
else
    # Apply overrides to existing config if flags were passed
    if [[ -n "$HOST" ]]; then
        sed -i "s/^host = .*/host = \"$HOST\"/" "$CONFIG_FILE"
        echo "Updated host -> $HOST"
    fi
    if [[ -n "$PORT" ]]; then
        sed -i "s/^port = .*/port = $PORT/" "$CONFIG_FILE"
        echo "Updated port -> $PORT"
    fi
    if [[ -n "$USER_SLUG" ]]; then
        sed -i "s/^user = .*/user = \"$USER_SLUG\"/" "$CONFIG_FILE"
        echo "Updated user -> $USER_SLUG"
    fi
fi

# ── Data directory setup ──────────────────────────────────────────────────

mkdir -p "$CONFIG_DIR/watchdog"
mkdir -p "$CONFIG_DIR/users"

# ── Server setup (optional) ──────────────────────────────────────────────

if [[ "$SERVER" == "true" ]]; then
    echo ""
    echo "Setting up Marcel server..."

    # The deploy script handles prerequisite checks, systemd units, and Docker
    cd "$REPO_DIR"
    exec ./deploy/setup.sh
fi

echo ""
echo "Done! Run: marcel"
echo "Config:    $CONFIG_FILE"
