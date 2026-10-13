#!/usr/bin/env bash
# Is Marcel up? Checks claude's supervisor, the runner, the hub and the brain, and says what is wrong.
# Exit 0 when the hub answers ok; 1 otherwise. Waits up to HEALTH_WAIT seconds (default 60) for the hub.
set -uo pipefail
MARCEL_HOME="${MARCEL_HOME:-$HOME/marcel}"
WAIT="${HEALTH_WAIT:-60}"
fail=0

for unit in marcel-claude-daemon.service marcel-runner.service; do
    state="$(systemctl --user is-active "$unit" 2>/dev/null)"
    printf '%-30s %s\n' "$unit" "$state"
    [ "$state" = active ] || fail=1
done

sock="$MARCEL_HOME/run/runner.sock"
if runner="$(curl -s -m 5 --unix-socket "$sock" http://runner/health)"; then
    printf '%-30s %s\n' "runner /health" "$runner"
else
    printf '%-30s %s\n' "runner /health" "no answer on $sock"
    fail=1
fi

hub=""
for _ in $(seq "$WAIT"); do
    hub="$(curl -s -m 3 http://127.0.0.1:7420/api/health)" && [ -n "$hub" ] && break
    sleep 1
done
printf '%-30s %s\n' "hub /api/health" "${hub:-no answer on 127.0.0.1:7420}"
case "$hub" in *'"ok":true'* | *'"ok": true'*) ;; *) fail=1 ;; esac

# The brain: a live session whose folder is the brain workspace.
brain="$(curl -s -m 10 --unix-socket "$sock" 'http://runner/sessions?all=false' 2>/dev/null \
    | BRAIN="$MARCEL_HOME/brain" /usr/bin/python3 -c 'import json, os, sys
try:
    rows = json.load(sys.stdin)
except ValueError:
    sys.exit()
if isinstance(rows, list):
    alive = [r for r in rows if isinstance(r, dict) and r.get("cwd") == os.environ["BRAIN"]]
    print(", ".join(f"{r.get("name")} ({r.get("state")})" for r in alive) or "none running")' 2>/dev/null)"
printf '%-30s %s\n' "brain" "${brain:-unknown (runner did not list sessions)}"

exit "$fail"
