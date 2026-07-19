#!/usr/bin/env bash
# zoo-setup.sh — Clone marcel-zoo and install its dependencies into a venv.
#
# Idempotent: safe to re-run. Clones the zoo if $MARCEL_ZOO_DIR is missing or
# empty, then reads the zoo's [project].dependencies from its pyproject.toml and
# installs them into the active venv via `uv pip install`. Run from the repo
# root.
#
# Usage:
#   ./scripts/zoo-setup.sh               # clone (if needed) + install zoo deps
#   ./scripts/zoo-setup.sh --sync        # git pull the zoo + re-install deps
#   ./scripts/zoo-setup.sh --deps-only   # skip clone/pull; install deps only
#                                        # (used inside the prod Docker container
#                                        #  via `make zoo-docker-deps`, where the
#                                        #  zoo is mounted from the host)
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
DEPS_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --sync) SYNC_MODE=true ;;
    --deps-only) DEPS_ONLY=true ;;
    -h|--help)
      awk '/^# / {sub(/^# ?/,""); print; next} /^#$/ {print ""; next} NR>1 {exit}' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

if [[ "$SYNC_MODE" == "true" && "$DEPS_ONLY" == "true" ]]; then
  die "--sync and --deps-only are mutually exclusive (--deps-only skips git operations entirely)"
fi

command -v uv       >/dev/null 2>&1 || die "uv is required — https://docs.astral.sh/uv/getting-started/installation/"
command -v python3  >/dev/null 2>&1 || die "python3 is required"

# ── Step 1: clone or update the zoo (skipped in --deps-only mode) ─────────────
if [[ "$DEPS_ONLY" == "true" ]]; then
  # Caller (e.g. the prod container) guarantees the zoo is already present via
  # a bind mount. git is not required here and may not be on the image.
  if [[ ! -d "$ZOO_DIR" ]] || [[ -z "$(ls -A "$ZOO_DIR" 2>/dev/null)" ]]; then
    die "Zoo dir not found or empty at $ZOO_DIR — run 'make zoo-setup' on the host first."
  fi
  info "Zoo present at $ZOO_DIR — installing deps only (--deps-only)."
else
  command -v git >/dev/null 2>&1 || die "git is required"
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
fi

# ── Step 2: install zoo deps into the active venv ─────────────────────────────
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
  info "Zoo declares no root [project].dependencies (habitats are in-process/stdlib-only)."
else
  info "Installing ${#ZOO_DEPS[@]} dep(s) into kernel venv: ${ZOO_DEPS[*]}"
  cd "$REPO_ROOT"
  uv pip install "${ZOO_DEPS[@]}"
fi

# ── Step 3: provision thin dep-venvs for toolkits with real PyPI deps ─────────
#
# Lean isolation (ADR-260628-6101c5): habitats load **in-process** — no
# subprocess, no UDS, no kernel clone. A dependency-free toolkit
# (banking/news/docker) needs no venv; it runs directly in the kernel process.
#
# A toolkit that declares real deps in its own ``pyproject.toml`` (only
# ``icloud``: caldav + vobject) gets a **thin** ``.venv`` holding *only* those
# deps — never an editable ``marcel-core`` clone. The kernel appends the
# venv's ``site-packages`` to ``sys.path`` at load time
# (``_dep_venv_site_packages`` in ``src/marcel_core/toolkit/__init__.py``), so
# the deps import in-process. This replaces the old ~200 MB-per-habitat
# kernel-clone venvs with one small deps-only venv.
#
# Idempotent: an existing ``.venv`` is reused; deps are re-installed so
# ``pyproject.toml`` changes land (uv's cache makes this fast).

provision_dep_venv() {
  local habitat_dir="$1"
  local habitat_name
  habitat_name="$(basename "$habitat_dir")"
  local habitat_pyproject="$habitat_dir/pyproject.toml"
  local habitat_venv="$habitat_dir/.venv"

  # No pyproject.toml → dependency-free → pure in-process, no venv needed.
  [[ -f "$habitat_pyproject" ]] || return 0

  local habitat_deps
  mapfile -t habitat_deps < <(
    HABITAT_PYPROJECT="$habitat_pyproject" python3 - <<'PY'
import os, tomllib
with open(os.environ["HABITAT_PYPROJECT"], "rb") as fh:
    data = tomllib.load(fh)
for dep in data.get("project", {}).get("dependencies", []) or []:
    print(dep)
PY
  )

  # A pyproject with no deps also needs no venv.
  [[ "${#habitat_deps[@]}" -gt 0 ]] || return 0

  # Rebuild the venv from scratch so an old fat (kernel-clone) venv is
  # replaced by a thin deps-only one.
  if [[ -d "$habitat_venv" ]]; then
    rm -rf "$habitat_venv"
  fi
  info "Creating thin dep-venv for '$habitat_name' at $habitat_venv"
  uv venv "$habitat_venv" --python 3.12 --quiet
  uv pip install --python "$habitat_venv/bin/python" --quiet "${habitat_deps[@]}"
  info "  → installed ${#habitat_deps[@]} dep(s) (no kernel clone): ${habitat_deps[*]}"
  DEP_VENV_COUNT=$((DEP_VENV_COUNT + 1))
}

DEP_VENV_COUNT=0
# toolkit/ parks import deps in-process; connectors/ parks with a stdio server
# spawn their dep-venv interpreter directly (FEAT-260718-c232d9 — the venv IS
# the `command:` entry point, resolved park-relative by the kernel).
for HABITAT_ROOT in "$ZOO_DIR/toolkit" "$ZOO_DIR/connectors"; do
  if [[ -d "$HABITAT_ROOT" ]]; then
    for habitat_dir in "$HABITAT_ROOT"/*/; do
      habitat_dir="${habitat_dir%/}"  # strip trailing slash
      [[ -d "$habitat_dir" ]] || continue
      provision_dep_venv "$habitat_dir"
    done
  fi
done

info "Zoo setup complete."
info "  Zoo dir : $ZOO_DIR"
info "  Deps    : ${ZOO_DEPS[*]}"
info "  Thin dep-venvs : $DEP_VENV_COUNT provisioned (deps-only, in-process)"
