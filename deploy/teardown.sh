#!/usr/bin/env bash
# Marcel deployment teardown
#
# Stops Marcel and removes systemd units. Does NOT delete data (~/.marcel/)
# or configuration (.env). Safe to re-run.
#
# Usage:
#   ./deploy/teardown.sh

set -euo pipefail

GREEN='\033[0;32m'
NC='\033[0m'
log() { echo -e "${GREEN}[teardown]${NC} $*"; }

SYSTEMD_DIR="$HOME/.config/systemd/user"
UNITS=(marcel.service marcel-redeploy.service marcel-redeploy.path)

# Stop and disable units
for unit in "${UNITS[@]}"; do
    if systemctl --user is-active "$unit" &>/dev/null; then
        systemctl --user stop "$unit"
        log "Stopped $unit"
    fi
    if systemctl --user is-enabled "$unit" &>/dev/null 2>&1; then
        systemctl --user disable "$unit" 2>/dev/null
        log "Disabled $unit"
    fi
done

# Remove unit files
for unit in "${UNITS[@]}"; do
    if [[ -f "$SYSTEMD_DIR/$unit" ]]; then
        rm "$SYSTEMD_DIR/$unit"
        log "Removed $unit"
    fi
done

systemctl --user daemon-reload
log "Systemd daemon reloaded."

# Stop the Docker container if running
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if docker compose -f "$REPO_DIR/docker-compose.yml" ps -q marcel &>/dev/null 2>&1; then
    docker compose -f "$REPO_DIR/docker-compose.yml" down
    log "Docker container stopped."
fi

echo ""
log "Marcel has been stopped and systemd units removed."
log "Data in ~/.marcel/ has been preserved."
log "To start again: make setup"
