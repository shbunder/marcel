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
#
# Environment variables:
#   DRY_RUN=1  — exit 0 immediately after flag-file cleanup. Test-only hook;
#                lets tests/core/test_redeploy.py verify the cleanup happens
#                without invoking docker.

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

# ── Clear the env-scoped restart flag ─────────────────────────────────────────
# Dev has no in-container watchdog to clear the flag (uvicorn is PID 1 in the
# dev container), so redeploy.sh does it. Idempotent and safe for prod too —
# the prod watchdog normally clears the flag first, but `rm -f` on a missing
# file is a no-op. One mechanism, both environments.
#
# Leaving the flag in place re-triggers marcel-{dev-,}redeploy.path on any
# subsequent systemd restart / host reboot.
FLAG_DIR="${MARCEL_DATA_DIR:-$HOME/.marcel}/watchdog"
rm -f "$FLAG_DIR/restart_requested.$ENV_NAME"

# Test hook — exit before invoking docker. Lets tests verify cleanup happened
# without needing a real docker runtime.
if [[ "${DRY_RUN:-}" == "1" ]]; then
  echo "[redeploy:$ENV_NAME] DRY_RUN=1 — cleared flag, exiting before docker."
  exit 0
fi

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
