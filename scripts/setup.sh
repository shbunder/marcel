#!/usr/bin/env bash
# setup.sh — Install Marcel's systemd units, build the Docker image, and start everything.
#
# This is a Linux-only script. On other platforms it exits with a clear message.
#
# Usage:
#   ./scripts/setup.sh           # full setup
#   ./scripts/setup.sh --check   # dry-run: verify prerequisites only, make no changes

set -euo pipefail

# ── Resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="$REPO_ROOT/deploy"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DATA_DIR="${MARCEL_DATA_DIR:-$HOME/.marcel}"

# ── Colour helpers ─────────────────────────────────────────────────────────────
GREEN='\033[1;32m'
YELLOW='\033[0;93m'
RED='\033[0;31m'
NC='\033[0m'
info()  { echo -e "${GREEN}[   INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARNING]${NC} $*"; }
error() { echo -e "${RED}[  ERROR]${NC} $*" >&2; }
die()   { error "$*"; exit 1; }

# ── Argument parsing ──────────────────────────────────────────────────────────
CHECK_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=true ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

# ── OS check ──────────────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Linux" ]]; then
  warn "Marcel's systemd setup is Linux-only."
  warn "On this machine ($(uname -s)) you can run Marcel with:"
  warn "  make serve      — development server (Docker, hot-reload on :7421)"
  warn "  make docker-up  — Docker container (no systemd watcher)"
  exit 0
fi

# ── Prerequisites ─────────────────────────────────────────────────────────────
PREREQ_OK=true

check_cmd() {
  local cmd="$1" hint="$2"
  if command -v "$cmd" &>/dev/null; then
    info "  ✓ $cmd found"
  else
    error "  ✗ $cmd not found — $hint"
    PREREQ_OK=false
  fi
}

info "Checking prerequisites..."
check_cmd docker    "install Docker: https://docs.docker.com/engine/install/"
check_cmd systemctl "systemd is required (most modern Linux distros have it)"

# docker compose (plugin, not standalone)
if docker compose version &>/dev/null 2>&1; then
  info "  ✓ docker compose plugin found"
else
  error "  ✗ docker compose plugin not found — install the Compose plugin for Docker"
  PREREQ_OK=false
fi

# docker group membership
if id -nG | grep -qw docker; then
  info "  ✓ user is in the docker group"
else
  error "  ✗ user is not in the docker group — run: sudo usermod -aG docker \$USER"
  error "    Then log out and back in, or run: newgrp docker"
  PREREQ_OK=false
fi

# systemd user session
if systemctl --user status &>/dev/null 2>&1; then
  info "  ✓ systemd user session is active"
else
  error "  ✗ systemd user session not active"
  error "    Enable linger with: sudo loginctl enable-linger \$USER"
  PREREQ_OK=false
fi

if ! $PREREQ_OK; then
  die "Prerequisites not met. Fix the issues above and re-run."
fi

if $CHECK_ONLY; then
  info "All prerequisites met. (--check mode — no changes made)"
  exit 0
fi

# ── .env ──────────────────────────────────────────────────────────────────────
cd "$REPO_ROOT"
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    warn ".env created from .env.example — please fill in your credentials before Marcel can run."
    warn "Edit $REPO_ROOT/.env then re-run this script."
    exit 0
  else
    die ".env not found and no .env.example to copy from."
  fi
fi

# ── Render and install systemd units ─────────────────────────────────────────
info "Installing systemd user units..."
mkdir -p "$SYSTEMD_DIR"

render_template() {
  local tmpl="$1" dest="$2"
  sed \
    -e "s|@@REPO_ROOT@@|$REPO_ROOT|g" \
    -e "s|@@DATA_DIR@@|$DATA_DIR|g" \
    -e "s|@@HOME@@|$HOME|g" \
    "$tmpl" > "$dest"
  info "  Installed $dest"
}

render_template "$DEPLOY_DIR/marcel.service.tmpl"              "$SYSTEMD_DIR/marcel.service"
render_template "$DEPLOY_DIR/marcel-redeploy.path.tmpl"        "$SYSTEMD_DIR/marcel-redeploy.path"
render_template "$DEPLOY_DIR/marcel-redeploy.service.tmpl"     "$SYSTEMD_DIR/marcel-redeploy.service"
# Dev environment: same flag-file mechanism, different flag suffix + compose file.
# The marcel-dev container itself is started manually via `make serve` (not a
# systemd unit), but self-mod restarts go through the same host-side path.
render_template "$DEPLOY_DIR/marcel-dev-redeploy.path.tmpl"    "$SYSTEMD_DIR/marcel-dev-redeploy.path"
render_template "$DEPLOY_DIR/marcel-dev-redeploy.service.tmpl" "$SYSTEMD_DIR/marcel-dev-redeploy.service"

systemctl --user daemon-reload
info "systemd units reloaded."

# ── Enable and start ──────────────────────────────────────────────────────────
info "Enabling services..."
systemctl --user enable marcel.service
systemctl --user enable marcel-redeploy.path
systemctl --user enable marcel-dev-redeploy.path

info "Starting Marcel..."
systemctl --user start marcel.service
systemctl --user start marcel-redeploy.path
systemctl --user start marcel-dev-redeploy.path

# ── Health check ──────────────────────────────────────────────────────────────
HEALTH_URL="http://localhost:7420/health"
TIMEOUT=60
INTERVAL=2
info "Waiting for Marcel to be healthy (up to ${TIMEOUT}s)..."

elapsed=0
while true; do
  if curl -sf "$HEALTH_URL" &>/dev/null; then
    info "Marcel is healthy at $HEALTH_URL"
    break
  fi
  if (( elapsed >= TIMEOUT )); then
    die "Marcel did not become healthy within ${TIMEOUT}s. Check logs: journalctl --user -u marcel -f"
  fi
  sleep "$INTERVAL"
  (( elapsed += INTERVAL ))
done

# ── Install the zoo + its deps ────────────────────────────────────────────────
# The kernel ships zero habitats — integrations, skills, channels, jobs, and
# agents all live in marcel-zoo. A first-boot operator who stops here would
# have a silent Marcel with no habitats. We do two installs:
#   1. Host-side: clone the zoo + `uv pip install` its deps into the kernel
#      venv. Required for dev workflows (`make serve`, `make cli-dev`).
#   2. Container-side: the prod image bakes in kernel deps only, so zoo deps
#      (e.g. caldav/vobject for iCloud) need a second `uv pip install` inside
#      the running container, run through `docker exec`. The zoo pyproject is
#      read from the bind-mounted ${HOME}/.marcel/zoo volume.
# Idempotent — re-running after first boot is a no-op.
info "Installing marcel-zoo (host)..."
"$SCRIPT_DIR/zoo-setup.sh"

info "Installing zoo deps into the prod container..."
docker exec marcel bash /app/scripts/zoo-setup.sh --deps-only

# The prod container already finished discover_integrations() at startup, so
# handlers that import the newly-installed deps (caldav for iCloud, vobject,
# …) are currently still broken for this boot. A restart makes them
# importable. First-boot operators who stop at `make setup` without this
# restart will see "iCloud: caldav not installed" errors until they cycle
# the container.
warn ""
warn "NOTE: The container started before zoo deps were installed."
warn "      Restart to pick them up — zoo integrations won't import until then:"
warn "        make docker-restart"

info ""
info "Setup complete. Useful commands:"
info "  systemctl --user status marcel                  — prod container status"
info "  systemctl --user status marcel-redeploy.path    — prod restart watcher"
info "  systemctl --user status marcel-dev-redeploy.path — dev restart watcher"
info "  journalctl --user -u marcel -f                  — follow Marcel logs"
info "  make serve                                      — start dev container on :\${MARCEL_DEV_PORT:-7421}"
info "  make zoo-docker-sync                            — pull zoo updates + refresh container deps"
info "  make teardown                                   — stop everything"
