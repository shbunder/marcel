#!/usr/bin/env bash
# Claude Code status line for Marcel.
# Reads session JSON on stdin (per Claude Code statusLine protocol),
# emits a single compact line: branch • active-story • uncommitted • safety-flag.

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO_ROOT" ]; then
  printf '%s\n' "marcel (not a git repo)"
  exit 0
fi

cd "$REPO_ROOT" || exit 0

BRANCH="$(git branch --show-current 2>/dev/null)"
BRANCH="${BRANCH:-detached}"

UNCOMMITTED="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"

# Active story parsed from the branch name: story/S-04.9 → S-04.9
ACTIVE=""
case "$BRANCH" in
  story/*) ACTIVE=" • ${BRANCH#story/}" ;;
esac

# Safety unlock flag — visible warning when present
SAFETY=""
if [ -f ".claude/.unlock-safety" ]; then
  SAFETY=" • 🔓 unlocked"
fi

DIRTY=""
if [ "$UNCOMMITTED" -gt 0 ]; then
  DIRTY=" • ${UNCOMMITTED}✎"
fi

printf '🦒 %s%s%s%s\n' "$BRANCH" "$ACTIVE" "$DIRTY" "$SAFETY"
