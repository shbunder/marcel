#!/usr/bin/env bash
# teardown.sh — Stop Marcel and remove its systemd units.
#
# User data in ~/.marcel/ is NEVER deleted.
# This is a Linux-only script. On other platforms it exits with a clear message.
#
# Usage:
#   ./scripts/teardown.sh

set -euo pipefail

# ── Resolve paths ─────────────────────────────────────────────────────────────
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# ── Colour helpers ─────────────────────────────────────────────────────────────
GREEN='\033[1;32m'
YELLOW='\033[0;93m'
RED='\033[0;31m'
NC='\033[0m'
info()  { echo -e "${GREEN}[   INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARNING]${NC} $*"; }
error() { echo -e "${RED}[  ERROR]${NC} $*" >&2; }

# ── OS check ──────────────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Linux" ]]; then
  warn "Marcel's systemd teardown is Linux-only."
  warn "On this machine ($(uname -s)) you can stop Marcel with:"
  warn "  make docker-down  — stop the Docker container"
  exit 0
fi

# ── Stop and disable units ────────────────────────────────────────────────────
stop_unit() {
  local unit="$1"
  if systemctl --user is-active --quiet "$unit" 2>/dev/null; then
    info "Stopping $unit..."
    systemctl --user stop "$unit"
  else
    info "  $unit is not running — skipping stop"
  fi
  if systemctl --user is-enabled --quiet "$unit" 2>/dev/null; then
    systemctl --user disable "$unit"
    info "  Disabled $unit"
  fi
}

stop_unit "marcel-redeploy.path"
stop_unit "marcel.service"

# ── Remove unit files ─────────────────────────────────────────────────────────
UNITS=(
  "$SYSTEMD_DIR/marcel.service"
  "$SYSTEMD_DIR/marcel-redeploy.path"
  "$SYSTEMD_DIR/marcel-redeploy.service"
)

for unit_file in "${UNITS[@]}"; do
  if [[ -f "$unit_file" ]]; then
    rm "$unit_file"
    info "Removed $unit_file"
  fi
done

systemctl --user daemon-reload
info "systemd units reloaded."

# ── Data safety notice ────────────────────────────────────────────────────────
DATA_DIR="${MARCEL_DATA_DIR:-$HOME/.marcel}"
info ""
info "Teardown complete. Marcel is stopped and systemd units are removed."
warn "User data at $DATA_DIR has been preserved."
warn "To also remove user data: rm -rf $DATA_DIR  (irreversible)"
