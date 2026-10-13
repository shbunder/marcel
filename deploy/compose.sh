#!/usr/bin/env bash
# docker compose for Marcel's hub, with the variables the compose file needs.
# Usage: deploy/compose.sh <compose arguments>, e.g. `deploy/compose.sh ps`.
set -euo pipefail
DEPLOY="$(cd "$(dirname "$0")" && pwd)"
export MARCEL_HOME="${MARCEL_HOME:-$HOME/marcel}"
MARCEL_UID="$(id -u)"
MARCEL_GID="$(id -g)"
export MARCEL_UID MARCEL_GID
exec docker compose -f "$DEPLOY/docker-compose.yml" "$@"
