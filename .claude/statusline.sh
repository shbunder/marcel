#!/usr/bin/env bash
# Claude Code status line for Marcel.
# Reads session JSON on stdin (per Claude Code statusLine protocol),
# emits a single compact line: branch • uncommitted • wip-issues • active-issue • safety-flag.

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
WIP_ISSUES="$(ls project/issues/wip/ISSUE-*.md 2>/dev/null | wc -l | tr -d ' ')"

# Active issue (if current branch encodes one as issue/<hash>-<slug>)
ACTIVE_ISSUE=""
case "$BRANCH" in
  issue/*)
    HASH="${BRANCH#issue/}"
    HASH="${HASH%%-*}"
    ACTIVE_ISSUE=" • ISSUE-${HASH}"
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

WIP=""
if [ "$WIP_ISSUES" -gt 0 ]; then
  WIP=" • ${WIP_ISSUES} wip"
fi

printf '🦒 %s%s%s%s%s\n' "$BRANCH" "$ACTIVE_ISSUE" "$DIRTY" "$WIP" "$SAFETY"
