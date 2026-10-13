#!/usr/bin/env bash
# Install the Claude Code version Marcel is tested with (runner/claude-version), with Claude Code's
# own installer (docs: setup § install a specific version). Does nothing when it is installed already.
# Usage: deploy/install-claude.sh   (make -C deploy install runs it first)
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
WANT="$(tr -d '[:space:]' < "$SRC/runner/claude-version")"
CLAUDE="$HOME/.local/bin/claude"
HAVE="$([ -x "$CLAUDE" ] && "$CLAUDE" --version 2>/dev/null | awk '{print $1}' || true)"
if [ "$HAVE" = "$WANT" ]; then
    echo "Claude Code $WANT is installed."
    exit 0
fi
echo "Installing Claude Code $WANT (found: ${HAVE:-none})."
curl -fsSL https://claude.ai/install.sh | bash -s "$WANT"
HAVE="$("$CLAUDE" --version | awk '{print $1}')"
[ "$HAVE" = "$WANT" ] || { echo "Install stopped: Claude Code is $HAVE after installing $WANT." >&2; exit 1; }
# A running supervisor (marcel-claude-daemon.service) keeps the version it started with; restarting it
# stops every session, so that is left to the operator (docs/operating.md, "Upgrading Claude Code").
if systemctl --user is-active --quiet marcel-claude-daemon.service 2>/dev/null; then
    echo "Claude's supervisor still runs the old version. When no work is running:"
    echo "  systemctl --user restart marcel-claude-daemon   (stops every session; the hub starts the brain again)"
fi
