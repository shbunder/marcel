#!/usr/bin/env bash
# redeploy.sh — Rebuild and restart the Marcel Docker container.
#
# Designed to be safe to call from any environment (laptop, NUC, CI).
# By default it checks whether Marcel is actually running before doing anything,
# so running it on a machine where Marcel is not deployed is a clean no-op.
#
# Usage:
#   ./scripts/redeploy.sh           # skip if Marcel is not running
#   ./scripts/redeploy.sh --force   # deploy even if container is not up (first-time setup)

set -euo pipefail

# ── Resolve repo root ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# ── Argument parsing ──────────────────────────────────────────────────────────
FORCE=false
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# ── Running check ─────────────────────────────────────────────────────────────
# Only redeploy when Marcel is already running. This keeps the script safe to
# invoke from Claude Code hooks, where the environment may be a dev laptop
# rather than the NUC where Marcel is actually deployed.
if ! $FORCE; then
  if ! docker compose ps --status running --quiet 2>/dev/null | grep -q .; then
    echo "Marcel is not running — skipping redeploy. Use --force to deploy from scratch."
    exit 0
  fi
fi

# ── Redeploy ──────────────────────────────────────────────────────────────────
echo "[redeploy] Rebuilding Marcel image..."
docker compose build

echo "[redeploy] Restarting Marcel container..."
docker compose up -d

echo "[redeploy] Done."
