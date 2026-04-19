#!/usr/bin/env bash
# redeploy.sh — Rebuild and restart a Marcel Docker container (prod or dev).
#
# Designed to be safe to call from any environment (laptop, NUC, CI).
# By default it checks whether the target environment's container is actually
# running before doing anything, so invoking it on a machine where Marcel is
# not deployed is a clean no-op.
#
# Usage:
#   ./scripts/redeploy.sh                        # prod, skip if not running
#   ./scripts/redeploy.sh --force                # prod, deploy even if not up
#   ./scripts/redeploy.sh --env dev              # dev, skip if not running
#   ./scripts/redeploy.sh --env dev --force      # dev, first-time deploy
#
# Triggered from systemd by:
#   - marcel-redeploy.service      → --env prod --force
#   - marcel-dev-redeploy.service  → --env dev --force

set -euo pipefail

# ── Resolve repo root ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# ── Argument parsing ──────────────────────────────────────────────────────────
ENV_NAME=prod
FORCE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENV_NAME="${2:-}"
      shift 2
      ;;
    --env=*)
      ENV_NAME="${1#--env=}"
      shift
      ;;
    --force)
      FORCE=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

case "$ENV_NAME" in
  prod) COMPOSE_FILE="docker-compose.yml" ;;
  dev)  COMPOSE_FILE="docker-compose.dev.yml" ;;
  *)    echo "Invalid --env value: '$ENV_NAME' (expected 'dev' or 'prod')" >&2; exit 1 ;;
esac

COMPOSE="docker compose -f $COMPOSE_FILE"

# ── Running check ─────────────────────────────────────────────────────────────
# Only redeploy when the target env's container is already running. This keeps
# the script safe to invoke from Claude Code hooks, where the environment may
# be a dev laptop rather than the NUC where Marcel is actually deployed.
if ! $FORCE; then
  if ! $COMPOSE ps --status running --quiet 2>/dev/null | grep -q .; then
    echo "Marcel ($ENV_NAME) is not running — skipping redeploy. Use --force to deploy from scratch."
    exit 0
  fi
fi

# ── Redeploy ──────────────────────────────────────────────────────────────────
echo "[redeploy:$ENV_NAME] Rebuilding image..."
$COMPOSE build

echo "[redeploy:$ENV_NAME] Restarting container..."
$COMPOSE up -d

echo "[redeploy:$ENV_NAME] Done."
