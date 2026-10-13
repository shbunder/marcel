#!/usr/bin/env bash
# Is Marcel up? Checks claude's supervisor, the runner, the hub and the brain, and says what is wrong.
# Exit codes (docs/operating.md):
#   0  everything is ok: both units active, the runner and the hub ok, and the hub's whole-path check
#      (hub, runner and a live brain) ok;
#   1  something is down;
#   2  only the claude version drifted: everything runs, but the installed claude is not the pinned one,
#      so the runner refuses new sessions. Not a broken deploy: run deploy/install-claude.sh. A redeploy
#      watchdog must not roll back on 2.
# Waits up to HEALTH_WAIT seconds (default 60) for the hub and the brain.
set -uo pipefail
MARCEL_HOME="${MARCEL_HOME:-$HOME/marcel}"
WAIT="${HEALTH_WAIT:-60}"
fail=0
drift=0

# The runner's /health answer: ok, drift (up, but claude is not the pinned version, and its supervisor
# runs on its own) or down. The same rule as the hub's /internal/health (hub/src/marcel_hub/path_health.py).
runner_state() {
    python3 -c 'import json, sys
try:
    h = json.loads(sys.argv[1])
except ValueError:
    print("down"); sys.exit()
if not isinstance(h, dict):
    print("down"); sys.exit()
if h.get("ok") is True:
    print("ok"); sys.exit()
installed = str(h.get("claude_version") or "").split()
expected = str(h.get("claude_version_expected") or "").strip()
sup = h.get("supervisor") or {}
up = sup.get("running") is True and sup.get("own_unit") is True
print("drift" if installed and expected and installed[0] != expected and up else "down")' "$1"
}

for unit in marcel-claude-daemon.service marcel-runner.service; do
    state="$(systemctl --user is-active "$unit" 2>/dev/null)"
    printf '%-30s %s\n' "$unit" "$state"
    [ "$state" = active ] || fail=1
done

sock="$MARCEL_HOME/run/runner.sock"
if runner="$(curl -s -m 5 --unix-socket "$sock" http://runner/health)"; then
    printf '%-30s %s\n' "runner /health" "$runner"
    case "$(runner_state "$runner")" in
        ok) ;;
        drift) drift=1 ;;
        *) fail=1 ;;
    esac
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
# The route takes the hub secret, which the hub keeps in its data folder. It goes to curl on stdin,
# never on the command line, so it never shows in the process list. Each try takes up to 10 s.
secret_file="$MARCEL_HOME/data/hub-secret"
path=""
if [ ! -r "$secret_file" ]; then
    path="no hub secret at $secret_file (make -C deploy install writes it)"
else
    for _ in $(seq "$WAIT"); do
        path="$(printf 'X-Marcel-Secret: %s\n' "$(cat "$secret_file")" \
            | curl -s -m 10 -H @- http://127.0.0.1:7420/internal/health)"
        case "$path" in *'"ok":true'* | *'"ok": true'*) break ;; esac
        # A drifted runner starts no brain, so waiting longer cannot help.
        case "$path" in *'"state":"version_drift"'*) break ;; esac
        sleep 1
    done
fi
printf '%-30s %s\n' "hub /internal/health" "${path:-no answer on 127.0.0.1:7420}"
case "$path" in
    *'"ok":true'* | *'"ok": true'*) ;;
    *'"state":"version_drift"'*) drift=1 ;;  # the brain may be down for the same reason
    *) fail=1 ;;
esac

if [ "$fail" -ne 0 ]; then
    exit 1
fi
if [ "$drift" -ne 0 ]; then
    echo "Claude Code is not the version Marcel is tested with; everything else is up."
    echo "Run deploy/install-claude.sh, then make -C deploy health again. Nothing needs rolling back."
    exit 2
fi
exit 0
