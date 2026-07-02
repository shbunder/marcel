#!/usr/bin/env bash
# Claude Code status line for Marcel.
# Reads session JSON on stdin (per Claude Code statusLine protocol),
# emits a single compact line: branch • active-feature • uncommitted • safety-flag.

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

# Active work id parsed from the branch name:
#   feat/FEAT-YYMMDD-hash-slug → FEAT-YYMMDD-hash   (marcel-admin board flow)
#   issue/<hash>-slug          → ISSUE-<hash>        (retired flow; lingering branches only)
ACTIVE=""
case "$BRANCH" in
  feat/FEAT-*)
    ACTIVE=" • $(printf '%s' "${BRANCH#feat/}" | cut -d- -f1-3)"
    ;;
  issue/*)
    HASH="${BRANCH#issue/}"
    HASH="${HASH%%-*}"
    ACTIVE=" • ISSUE-${HASH}"
    ;;
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
