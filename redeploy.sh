#!/usr/bin/env bash
# Marcel self-redeploy script
#
# Called from inside the container (via Docker socket) or from the host.
# Rebuilds and restarts the Marcel container with rollback on health failure.
#
# Usage:
#   ./redeploy.sh              # from repo root
#   ./redeploy.sh --no-build   # skip docker build (code-only changes)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE="docker compose"
SERVICE="marcel"
HEALTH_URL="http://localhost:7420/health"
HEALTH_TIMEOUT=60
HEALTH_INTERVAL=3
DATA_DIR="${HOME}/.marcel"
RESULT_FILE="${DATA_DIR}/watchdog/restart_result"
NO_BUILD=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-build) NO_BUILD=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

mkdir -p "${DATA_DIR}/watchdog"

log() { echo "[redeploy] $(date '+%H:%M:%S') $*"; }

# Tag current state as known-good
GOOD_SHA="$(git rev-parse HEAD)"
log "Current known-good SHA: ${GOOD_SHA}"

# Health check function
check_health() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    while (( SECONDS < deadline )); do
        if curl -sf "${HEALTH_URL}" >/dev/null 2>&1; then
            return 0
        fi
        sleep "$HEALTH_INTERVAL"
    done
    return 1
}

# Build and restart
if [[ "$NO_BUILD" == "false" ]]; then
    log "Building new image..."
    $COMPOSE build "$SERVICE"
fi

log "Restarting container..."
$COMPOSE up -d "$SERVICE"

# Wait for health
log "Waiting for health check..."
if check_health; then
    log "Deploy successful."
    echo "ok" > "$RESULT_FILE"
    exit 0
fi

# Health failed — rollback
log "Health check failed. Rolling back to ${GOOD_SHA}..."
git checkout "$GOOD_SHA" -- .

if [[ "$NO_BUILD" == "false" ]]; then
    log "Rebuilding with previous version..."
    $COMPOSE build "$SERVICE"
fi

log "Restarting with previous version..."
$COMPOSE up -d "$SERVICE"

if check_health; then
    log "Rollback successful. Running on ${GOOD_SHA}."
    echo "rolled_back" > "$RESULT_FILE"
    exit 1
else
    log "FATAL: Rollback also failed. Manual intervention required."
    echo "rollback_failed" > "$RESULT_FILE"
    exit 2
fi
