#!/usr/bin/env bash
# zoo-setup.sh — Clone marcel-zoo and install its dependencies into the kernel venv.
#
# Idempotent: safe to re-run. Clones the zoo if $MARCEL_ZOO_DIR is missing or
# empty, then reads the zoo's [project].dependencies from its pyproject.toml and
# installs them into the kernel venv via `uv pip install`. Run from the repo
# root.
#
# Usage:
#   ./scripts/zoo-setup.sh         # clone (if needed) + install zoo deps
#   ./scripts/zoo-setup.sh --sync  # git pull the zoo + re-install deps
#
# Env:
#   MARCEL_ZOO_DIR   Target directory for the zoo checkout. Defaults to
#                    $HOME/.marcel/zoo (matches docker-compose defaults).
#   MARCEL_ZOO_REPO  Git URL to clone from. Defaults to the public shbunder/marcel-zoo.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ZOO_REPO_URL="${MARCEL_ZOO_REPO:-https://github.com/shbunder/marcel-zoo.git}"
ZOO_DIR="${MARCEL_ZOO_DIR:-$HOME/.marcel/zoo}"

GREEN='\033[1;32m'
YELLOW='\033[0;93m'
RED='\033[0;31m'
NC='\033[0m'
info()  { echo -e "${GREEN}[   INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARNING]${NC} $*"; }
error() { echo -e "${RED}[  ERROR]${NC} $*" >&2; }
die()   { error "$*"; exit 1; }

SYNC_MODE=false
for arg in "$@"; do
  case "$arg" in
    --sync) SYNC_MODE=true ;;
    -h|--help)
      awk '/^# / {sub(/^# ?/,""); print; next} /^#$/ {print ""; next} NR>1 {exit}' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

command -v git      >/dev/null 2>&1 || die "git is required"
command -v uv       >/dev/null 2>&1 || die "uv is required — https://docs.astral.sh/uv/getting-started/installation/"
command -v python3  >/dev/null 2>&1 || die "python3 is required"

# ── Step 1: clone or update the zoo ────────────────────────────────────────────
if [[ ! -d "$ZOO_DIR" ]] || [[ -z "$(ls -A "$ZOO_DIR" 2>/dev/null)" ]]; then
  info "Cloning $ZOO_REPO_URL → $ZOO_DIR ..."
  mkdir -p "$(dirname "$ZOO_DIR")"
  git clone "$ZOO_REPO_URL" "$ZOO_DIR"
elif [[ "$SYNC_MODE" == "true" ]]; then
  info "Updating zoo at $ZOO_DIR (git pull --ff-only) ..."
  git -C "$ZOO_DIR" pull --ff-only
else
  info "Zoo already present at $ZOO_DIR (use --sync to pull updates)"
fi

# ── Step 2: install zoo deps into kernel venv ──────────────────────────────────
ZOO_PYPROJECT="$ZOO_DIR/pyproject.toml"
if [[ ! -f "$ZOO_PYPROJECT" ]]; then
  die "Zoo checkout missing pyproject.toml: $ZOO_PYPROJECT"
fi

info "Reading zoo dependencies from pyproject.toml ..."
mapfile -t ZOO_DEPS < <(
  ZOO_PYPROJECT="$ZOO_PYPROJECT" python3 - <<'PY'
import os, sys, tomllib
with open(os.environ["ZOO_PYPROJECT"], "rb") as fh:
    data = tomllib.load(fh)
for dep in data.get("project", {}).get("dependencies", []) or []:
    print(dep)
PY
)

if [[ "${#ZOO_DEPS[@]}" -eq 0 ]]; then
  info "Zoo declares no [project].dependencies — nothing to install."
  exit 0
fi

info "Installing ${#ZOO_DEPS[@]} dep(s) into kernel venv: ${ZOO_DEPS[*]}"
cd "$REPO_ROOT"
uv pip install "${ZOO_DEPS[@]}"

info "Zoo setup complete."
info "  Zoo dir : $ZOO_DIR"
info "  Deps    : ${ZOO_DEPS[*]}"
