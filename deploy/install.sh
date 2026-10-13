#!/usr/bin/env bash
# Install or upgrade Marcel on the NUC, as the owner (never root). Safe to run again.
# Usage: make -C deploy install   (docs/operating.md, "Install")
#
# It sets up, under $MARCEL_HOME (default ~/marcel):
#   node/        node 22 for the plugin's channel server (the system node is left alone)
#   plugins/     the marcel-local marketplace: a built copy of plugins/marcel/
#   brain/       the brain workspace, rendered from brain/, with the plugin installed and trusted
#   data/ state/ run/ workspaces/scratch/   the hub's data, the redeploy flag, the runner's socket
# then the systemd user units and the hub container.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$SRC/deploy"
MARCEL_HOME="${MARCEL_HOME:-$HOME/marcel}"
NODE_VERSION="${NODE_VERSION:-v22.23.3}"
UNIT_DIR="$HOME/.config/systemd/user"
CLAUDE="$HOME/.local/bin/claude"
ENV_LOCAL="$DEPLOY/.env.local"

say() { printf '\n== %s\n' "$*"; }
die() { printf 'Install stopped: %s\n' "$*" >&2; exit 1; }

# The value of NAME in deploy/.env.local, or empty.
env_value() {
    [ -f "$ENV_LOCAL" ] || return 0
    sed -n "s/^$1=//p" "$ENV_LOCAL" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

check_host() {
    [ "$(id -u)" -ne 0 ] || die "run it as the owner, not root."
    command -v docker >/dev/null || die "docker is not installed."
    docker info >/dev/null 2>&1 || die "docker is not reachable (is the owner in the docker group?)."
    [ -x "$CLAUDE" ] || die "claude is not at $CLAUDE."
    /usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
        || die "/usr/bin/python3 must be 3.12 or newer (the runner runs on it)."
    [ -f "$ENV_LOCAL" ] || die "$ENV_LOCAL is missing. Create it first (docs/operating.md, \"Secrets\")."
    [ -n "$(env_value MARCEL_OWNER_NAME)" ] || die "set MARCEL_OWNER_NAME in $ENV_LOCAL."
}

make_dirs() {
    say "Folders under $MARCEL_HOME"
    mkdir -p "$MARCEL_HOME"/{data,state,run,brain,plugins,workspaces/scratch} "$HOME/.config/marcel" "$UNIT_DIR"
    chmod 700 "$MARCEL_HOME/state" "$HOME/.config/marcel"
    chmod 750 "$MARCEL_HOME/run"
}

install_node() {
    local want="${NODE_VERSION#v}" have=""
    [ -x "$MARCEL_HOME/node/bin/node" ] && have="$("$MARCEL_HOME/node/bin/node" --version)"
    if [ "$have" = "$NODE_VERSION" ]; then
        echo "node $have is installed."
        return
    fi
    say "node $NODE_VERSION into $MARCEL_HOME/node"
    local tmp name="node-$NODE_VERSION-linux-x64"
    tmp="$(mktemp -d)"
    curl -fsSL "https://nodejs.org/dist/$NODE_VERSION/$name.tar.xz" -o "$tmp/$name.tar.xz"
    curl -fsSL "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
    (cd "$tmp" && grep " $name.tar.xz\$" SHASUMS256.txt | sha256sum -c --quiet -) \
        || die "the node $want download does not match its checksum."
    tar -xJf "$tmp/$name.tar.xz" -C "$tmp"
    rm -rf "$MARCEL_HOME/node"
    mv "$tmp/$name" "$MARCEL_HOME/node"
    rm -rf "$tmp"
}

install_plugin() {
    say "Plugin: the marcel-local marketplace in $MARCEL_HOME/plugins"
    local dest="$MARCEL_HOME/plugins/marcel"
    # A released copy, never the working tree: a directory plugin runs in place (SP1).
    mkdir -p "$dest"
    rsync -a --delete --exclude node_modules --exclude dist --exclude .venv --exclude __pycache__ \
        --exclude coverage "$SRC/plugins/marcel/" "$dest/"
    # dist/ is gitignored: build it. The channel server needs ws from node_modules at run time.
    (cd "$dest/channel" && PATH="$MARCEL_HOME/node/bin:$PATH" npm ci --silent --no-audit --no-fund \
        && PATH="$MARCEL_HOME/node/bin:$PATH" npm run --silent build)
    mkdir -p "$MARCEL_HOME/plugins/.claude-plugin"
    cp "$DEPLOY/marketplace.json" "$MARCEL_HOME/plugins/.claude-plugin/marketplace.json"
    if "$CLAUDE" plugin marketplace list 2>/dev/null | grep -q 'marcel-local'; then
        "$CLAUDE" plugin marketplace update marcel-local
    else
        "$CLAUDE" plugin marketplace add "$MARCEL_HOME/plugins"
    fi
}

write_secrets() {
    say "Secrets"
    local secret="$MARCEL_HOME/data/hub-secret"
    if [ ! -s "$secret" ]; then
        (umask 077 && /usr/bin/python3 -c 'import secrets; print(secrets.token_hex(32))' > "$secret")
        echo "Made a new hub secret."
    fi
    chmod 600 "$secret"
    # The plugin reads the same secret from $CLAUDE_PLUGIN_DATA/hub-secret (docs/channel-server.md).
    local plugin_data="$HOME/.claude/plugins/data/marcel-marcel-local"
    mkdir -p "$plugin_data"
    install -m 600 "$secret" "$plugin_data/hub-secret"
    # Sessions inherit the runner's environment, so they get only Harry's token, never the hub's secrets.
    local harry
    harry="$(env_value MARCEL_HARRY_TOKEN)"
    (umask 077 && { [ -z "$harry" ] || printf 'MARCEL_HARRY_TOKEN=%s\n' "$harry"; } \
        > "$HOME/.config/marcel/session.env")
    [ -n "$harry" ] || echo "MARCEL_HARRY_TOKEN is not set: sessions run without Harry's tools."
}

install_brain() {
    say "Brain workspace in $MARCEL_HOME/brain"
    /usr/bin/python3 "$SRC/brain/render.py" --out "$MARCEL_HOME/brain" \
        --var "AGENT_NAME=$(env_value MARCEL_AGENT_NAME | grep . || echo Marcel)" \
        --var "OWNER_NAME=$(env_value MARCEL_OWNER_NAME)" \
        --var "MARKETPLACE_DIR=$MARCEL_HOME/plugins" \
        --var "MEMORY_DIR=$MARCEL_HOME/data/memory"
    (cd "$MARCEL_HOME/brain" && "$CLAUDE" plugin install marcel@marcel-local --scope project)
    # The trust prompt, accepted the way the runner does it for a clone (SP2), once.
    PYTHONPATH="$SRC/runner/src" /usr/bin/python3 - "$CLAUDE" "$MARCEL_HOME/brain" <<'PY'
import os, sys
from pathlib import Path
from marcel_runner.claude_env import scrubbed_env
from marcel_runner.workspaces import accept_trust, trust_problem

claude, cwd = Path(sys.argv[1]), Path(sys.argv[2])
if trust_problem(Path.home(), cwd) is None:
    print(f'{cwd} is trusted.')
else:
    accept_trust(claude, cwd, scrubbed_env(os.environ))
    print(f'Trusted {cwd}.')
PY
}

install_units() {
    say "systemd user units"
    local unit backup="$HOME/marcel-v2-units"
    for unit in "$DEPLOY"/systemd/*; do
        local name target
        name="$(basename "$unit")"
        target="$UNIT_DIR/$name"
        # A unit of the same name that is not ours (old Marcel's) is kept aside, never lost.
        if [ -f "$target" ] && ! grep -q 'marcel/src' "$target"; then
            mkdir -p "$backup"
            cp -p "$target" "$backup/$name"
            echo "Kept the old $name in $backup/."
        fi
        cp "$unit" "$target"
    done
    systemctl --user daemon-reload
    systemctl --user enable --now marcel-claude-daemon.service
    systemctl --user enable marcel-runner.service
    systemctl --user restart marcel-runner.service
    if [ -x "$DEPLOY/redeploy.sh" ]; then
        systemctl --user enable --now marcel-redeploy.path
    else
        systemctl --user disable --now marcel-redeploy.path 2>/dev/null || true
        echo "No deploy/redeploy.sh yet (S-11.2): approved self-changes are not redeployed automatically."
    fi
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
        echo "WARNING: linger is off, so these units stop at logout and do not start at boot."
        echo "         Run once: sudo loginctl enable-linger $USER"
    fi
}

start_hub() {
    say "Hub container"
    "$DEPLOY/compose.sh" up -d --build
    "$DEPLOY/health.sh"
}

check_host
make_dirs
install_node
install_plugin
write_secrets
install_brain
install_units
start_hub
