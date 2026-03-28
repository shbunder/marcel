#!/usr/bin/env bash
# Marcel CLI installer
# Installs the `marcel` command into an isolated uv-managed tool environment.
# The CLI connects to a Marcel server running elsewhere (e.g. your NUC).
#
# Usage:
#   ./install.sh                        # install from this directory
#   ./install.sh --host 192.168.1.10    # set default server host
#   ./install.sh --port 7420            # set default server port
#   ./install.sh --user alice           # set default user

set -euo pipefail

HOST=""
PORT=""
USER_SLUG=""

# Parse optional args
while [[ $# -gt 0 ]]; do
    case $1 in
        --host) HOST="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --user) USER_SLUG="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "Installing Marcel CLI..."
uv tool install . --force

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
TOML
    echo "Config written to $CONFIG_FILE"
else
    # Apply overrides to existing config if flags were passed
    if [[ -n "$HOST" ]]; then
        sed -i "s/^host = .*/host = \"$HOST\"/" "$CONFIG_FILE"
        echo "Updated host → $HOST"
    fi
    if [[ -n "$PORT" ]]; then
        sed -i "s/^port = .*/port = $PORT/" "$CONFIG_FILE"
        echo "Updated port → $PORT"
    fi
    if [[ -n "$USER_SLUG" ]]; then
        sed -i "s/^user = .*/user = \"$USER_SLUG\"/" "$CONFIG_FILE"
        echo "Updated user → $USER_SLUG"
    fi
fi

echo ""
echo "Done. Run: marcel"
echo "Config:    $CONFIG_FILE"
