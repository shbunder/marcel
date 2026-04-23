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
  info "Zoo declares no root [project].dependencies (all UDS-isolated or stdlib-only)."
else
  info "Installing ${#ZOO_DEPS[@]} dep(s) into kernel venv: ${ZOO_DEPS[*]}"
  cd "$REPO_ROOT"
  uv pip install "${ZOO_DEPS[@]}"
fi

# ── Step 3: provision per-habitat venvs for UDS-isolated toolkits ─────────────
#
# A toolkit habitat with ``isolation: uds`` in its ``toolkit.yaml`` runs in a
# separate subprocess with its own ``.venv``. The kernel's ``_bridge_command``
# (in ``src/marcel_core/toolkit/__init__.py``) prefers ``<habitat>/.venv/bin/python``
# when present. Each such venv needs:
#
#   - The habitat's own deps from ``<habitat>/pyproject.toml``
#   - ``marcel-core`` installed so ``python -m marcel_core.plugin._uds_bridge``
#     resolves from inside the habitat venv.
#
# Inprocess habitats (no ``isolation: uds`` declaration) remain covered by the
# root-zoo install in Step 2 — this loop is a no-op for them.
#
# Idempotent: if the habitat's ``.venv`` already exists, we still re-run the
# install so dep changes in ``pyproject.toml`` land. uv's cache makes this fast.

provision_uds_habitat() {
  local habitat_dir="$1"
  local habitat_name
  habitat_name="$(basename "$habitat_dir")"
  local habitat_pyproject="$habitat_dir/pyproject.toml"
  local habitat_venv="$habitat_dir/.venv"

  # Prefer toolkit.yaml over the legacy integration.yaml (ISSUE-3c1534).
  local yaml_path=""
  if [[ -f "$habitat_dir/toolkit.yaml" ]]; then
    yaml_path="$habitat_dir/toolkit.yaml"
  elif [[ -f "$habitat_dir/integration.yaml" ]]; then
    yaml_path="$habitat_dir/integration.yaml"
  else
    return 0
  fi

  local is_uds
  is_uds=$(YAML_PATH="$yaml_path" python3 - <<'PY'
import os, sys
try:
    import yaml
except ImportError:
    # Kernel venv must have pyyaml — it's a transitive dep. Bail loudly if
    # that's ever not true so the operator sees why UDS provisioning broke.
    sys.stderr.write("zoo-setup: pyyaml missing from kernel venv — cannot parse toolkit.yaml\n")
    sys.exit(2)
with open(os.environ["YAML_PATH"], "r", encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}
print("yes" if data.get("isolation") == "uds" else "no")
PY
  )

  [[ "$is_uds" == "yes" ]] || return 0

  if [[ ! -f "$habitat_pyproject" ]]; then
    warn "UDS habitat '$habitat_name' declares isolation: uds but has no pyproject.toml — skipping"
    return 0
  fi

  if [[ ! -d "$habitat_venv" ]]; then
    info "Creating per-habitat venv for '$habitat_name' at $habitat_venv"
    uv venv "$habitat_venv" --python 3.12 --quiet
  fi

  # Install marcel-core from the kernel checkout so `python -m marcel_core...`
  # resolves inside the habitat venv. Editable install means kernel upgrades
  # on the host are picked up without a re-provision.
  info "Installing marcel-core + habitat deps into '$habitat_name' venv"
  uv pip install --python "$habitat_venv/bin/python" --quiet -e "$REPO_ROOT"

  # Read the habitat's own deps from its pyproject.toml.
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

  if [[ "${#habitat_deps[@]}" -gt 0 ]]; then
    uv pip install --python "$habitat_venv/bin/python" --quiet "${habitat_deps[@]}"
    info "  → installed ${#habitat_deps[@]} dep(s): ${habitat_deps[*]}"
  else
    info "  → habitat declares no extra deps (failure-isolation only)"
  fi
}

# Walk both the new ``toolkit/`` and legacy ``integrations/`` layouts so
# ISSUE-3c1534's in-flight rename doesn't gate this provisioning.
UDS_COUNT=0
for dir_layout in toolkit integrations; do
  layout_root="$ZOO_DIR/$dir_layout"
  [[ -d "$layout_root" ]] || continue
  for habitat_dir in "$layout_root"/*/; do
    habitat_dir="${habitat_dir%/}"  # strip trailing slash
    [[ -d "$habitat_dir" ]] || continue
    if [[ -f "$habitat_dir/toolkit.yaml" || -f "$habitat_dir/integration.yaml" ]]; then
      # Canonicalise on toolkit.yaml for the isolation check but honour
      # integration.yaml as a fallback.
      provision_uds_habitat "$habitat_dir"
      # Count only habitats that actually ended up with a .venv (best-effort).
      if [[ -d "$habitat_dir/.venv" ]]; then
        UDS_COUNT=$((UDS_COUNT + 1))
      fi
    fi
  done
done

info "Zoo setup complete."
info "  Zoo dir : $ZOO_DIR"
info "  Deps    : ${ZOO_DEPS[*]}"
info "  UDS habitats : $UDS_COUNT per-habitat .venv(s) provisioned"
