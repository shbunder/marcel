#!/usr/bin/env bash
# Marcel deployment setup
#
# Installs user-level systemd units that manage the Marcel Docker container
# and watches for self-restart requests. No sudo required — only needs the
# current user to be in the 'docker' group.
#
# Usage:
#   ./deploy/setup.sh          # install and start everything
#   ./deploy/setup.sh --check  # dry-run: verify prerequisites only
#
# What this does:
#   1. Checks prerequisites (docker, docker compose, systemd, docker group)
#   2. Creates .env from .env.example if missing
#   3. Renders systemd unit templates with correct paths
#   4. Installs units to ~/.config/systemd/user/
#   5. Enables and starts the service + path watcher
#   6. Waits for health check
#
# To undo everything: ./deploy/teardown.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="${MARCEL_DATA_DIR:-$HOME/.marcel}"
SYSTEMD_DIR="$HOME/.config/systemd/user"
MARCEL_PORT="${MARCEL_PORT:-7420}"
CHECK_ONLY=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()   { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[setup]${NC} $*"; }
err()   { echo -e "${RED}[setup]${NC} $*" >&2; }
header() { echo -e "\n${BOLD}${CYAN}── $* ──${NC}"; }

while [[ $# -gt 0 ]]; do
    case $1 in
        --check) CHECK_ONLY=true; shift ;;
        *) err "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Prerequisites ────────────────────────────────────────────────────────

header "Checking prerequisites"

ERRORS=0

# Docker
if command -v docker &>/dev/null; then
    log "docker: $(docker --version | head -1)"
else
    err "docker not found. Install: https://docs.docker.com/engine/install/"
    ERRORS=$((ERRORS + 1))
fi

# Docker Compose
if docker compose version &>/dev/null 2>&1; then
    log "docker compose: $(docker compose version --short)"
else
    err "docker compose plugin not found. Install: https://docs.docker.com/compose/install/"
    ERRORS=$((ERRORS + 1))
fi

# Docker group membership
if groups | grep -qw docker; then
    log "docker group: yes"
else
    err "Current user is not in the 'docker' group."
    err "Fix: sudo usermod -aG docker \$USER && newgrp docker"
    ERRORS=$((ERRORS + 1))
fi

# Systemd user session
if systemctl --user status &>/dev/null 2>&1; then
    log "systemd user session: active"
else
    err "systemd user session not available."
    err "This usually means lingering is not enabled."
    err "Fix: sudo loginctl enable-linger \$USER"
    ERRORS=$((ERRORS + 1))
fi

# Lingering (units run even when user is not logged in)
if loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q "Linger=yes"; then
    log "loginctl linger: enabled"
else
    warn "loginctl linger: not enabled (units will stop when you log out)"
    warn "Fix: sudo loginctl enable-linger $USER"
    # Not a hard error — Marcel works fine while logged in
fi

if (( ERRORS > 0 )); then
    err ""
    err "$ERRORS prerequisite(s) failed. Fix the issues above and re-run."
    exit 1
fi

log "All prerequisites met."

if [[ "$CHECK_ONLY" == "true" ]]; then
    exit 0
fi

# ── .env setup ───────────────────────────────────────────────────────────

header "Configuration"

if [[ ! -f "$REPO_DIR/.env" ]]; then
    if [[ -f "$REPO_DIR/.env.example" ]]; then
        cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
        warn "Created .env from .env.example"
        warn "Edit $REPO_DIR/.env and fill in your API keys, then re-run this script."
        exit 0
    else
        err "No .env or .env.example found in $REPO_DIR"
        exit 1
    fi
fi

# Check for required keys
MISSING_KEYS=0
while IFS='=' read -r key value; do
    key=$(echo "$key" | xargs)
    value=$(echo "$value" | xargs)
    if [[ "$key" == "ANTHROPIC_API_KEY" && -z "$value" ]]; then
        err "ANTHROPIC_API_KEY is empty in .env"
        MISSING_KEYS=$((MISSING_KEYS + 1))
    fi
done < "$REPO_DIR/.env"

# Also check .env.local if it exists
if [[ -f "$REPO_DIR/.env.local" ]]; then
    source "$REPO_DIR/.env.local" 2>/dev/null || true
fi

if (( MISSING_KEYS > 0 )); then
    err "Fill in the required keys in $REPO_DIR/.env (or .env.local) and re-run."
    exit 1
fi

log "Configuration looks good."

# ── Data directory ───────────────────────────────────────────────────────

mkdir -p "$DATA_DIR/watchdog"
mkdir -p "$DATA_DIR/users"

# ── Systemd units ────────────────────────────────────────────────────────

header "Installing systemd units"

mkdir -p "$SYSTEMD_DIR"

for tmpl in "$SCRIPT_DIR"/*.tmpl; do
    unit_name="$(basename "$tmpl" .tmpl)"
    target="$SYSTEMD_DIR/$unit_name"

    sed \
        -e "s|@@REPO_DIR@@|$REPO_DIR|g" \
        -e "s|@@DATA_DIR@@|$DATA_DIR|g" \
        "$tmpl" > "$target"

    log "Installed $unit_name"
done

systemctl --user daemon-reload
log "Systemd daemon reloaded."

# ── Start services ───────────────────────────────────────────────────────

header "Starting Marcel"

# Enable units so they survive reboot
systemctl --user enable marcel.service marcel-redeploy.path 2>/dev/null

# Start the path watcher first (it's instant)
systemctl --user start marcel-redeploy.path
log "Restart watcher: active"

# Start the main service (builds + starts container)
log "Building and starting container (this may take a minute)..."
systemctl --user start marcel.service

# ── Health check ─────────────────────────────────────────────────────────

header "Health check"

TIMEOUT=60
ELAPSED=0
while (( ELAPSED < TIMEOUT )); do
    if curl -sf "http://localhost:${MARCEL_PORT}/health" >/dev/null 2>&1; then
        break
    fi
    sleep 3
    ELAPSED=$((ELAPSED + 3))
    printf "."
done
echo ""

if (( ELAPSED >= TIMEOUT )); then
    err "Marcel did not become healthy within ${TIMEOUT}s."
    err "Check logs: docker compose logs -f marcel"
    err "Or: journalctl --user -u marcel.service"
    exit 1
fi

# ── Done ─────────────────────────────────────────────────────────────────

header "Marcel is running"

echo ""
echo -e "  ${BOLD}Server${NC}       http://localhost:${MARCEL_PORT}"
echo -e "  ${BOLD}Health${NC}       http://localhost:${MARCEL_PORT}/health"
echo -e "  ${BOLD}Logs${NC}         docker compose logs -f marcel"
echo -e "  ${BOLD}Status${NC}       systemctl --user status marcel"
echo -e "  ${BOLD}Restart${NC}      systemctl --user restart marcel"
echo -e "  ${BOLD}Stop${NC}         systemctl --user stop marcel"
echo -e "  ${BOLD}Teardown${NC}     make teardown"
echo ""
echo -e "  Marcel will auto-restart when it modifies its own code."
echo -e "  The restart watcher is managed by systemd (no Docker socket needed)."
echo ""
