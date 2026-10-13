#!/usr/bin/env bash
# Is Marcel up? Checks claude's supervisor, the runner, the hub and the brain, and says what is wrong.
# Exit 0 when both units are active, the runner and the hub answer ok, and the hub's whole-path check
# (hub, runner and a live brain) is ok; 1 otherwise. Waits up to HEALTH_WAIT seconds (default 60) for the hub.
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
    case "$runner" in *'"ok":true'* | *'"ok": true'*) ;; *) fail=1 ;; esac
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

# The whole path: hub -> runner -> a brain whose channel is connected and pinging (hub/README.md
# "Whole-path health"). A hub that is up while the brain is dead or stuck fails here.
# Right after a deploy the brain may still be starting: wait up to HEALTH_WAIT seconds for it.
# The route takes the hub secret, which the hub keeps in its data folder.
secret="$(cat "$MARCEL_HOME/data/hub-secret" 2>/dev/null)"
path=""
for _ in $(seq "$WAIT"); do
    path="$(curl -s -m 10 -H "X-Marcel-Secret: $secret" http://127.0.0.1:7420/internal/health)"
    case "$path" in *'"ok":true'* | *'"ok": true'*) break ;; esac
    sleep 1
done
printf '%-30s %s\n' "hub /internal/health" "${path:-no answer on 127.0.0.1:7420}"
case "$path" in *'"ok":true'* | *'"ok": true'*) ;; *) fail=1 ;; esac

exit "$fail"
