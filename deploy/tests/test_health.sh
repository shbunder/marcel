#!/usr/bin/env bash
# health.sh's exit codes (docs/operating.md): 0 ok, 1 down, 2 only the claude version drifted.
# Runs health.sh with fake `systemctl` and `curl` first on PATH; nothing reaches a real unit, runner or hub.
# Run from deploy/: bash tests/test_health.sh (part of `make -C deploy check`).
set -uo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/home/data" "$work/home/run"
echo 'hs_test_secret_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' > "$work/home/data/hub-secret"

cat > "$work/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
echo "${FAKE_UNIT:-active}"
STUB
cat > "$work/bin/curl" <<'STUB'
#!/usr/bin/env bash
# The runner on its socket, the hub's /api/health, the hub's /internal/health (secret on stdin).
case "$*" in
    *--unix-socket*) out="$FAKE_RUNNER" ;;
    */api/health*) out="$FAKE_HUB" ;;
    */internal/health*) cat >/dev/null; out="$FAKE_PATH" ;;
    *) out="" ;;
esac
[ -n "$out" ] || exit 7
printf '%s' "$out"
STUB
chmod +x "$work/bin/systemctl" "$work/bin/curl"

RUNNER_OK='{"ok":true,"version":"0.1.10","claude_version":"2.1.296 (Claude Code)","claude_version_expected":"2.1.296","supervisor":{"running":true,"own_unit":true},"cap":4,"active_workers":0}'
RUNNER_DRIFT='{"ok":false,"version":"0.1.10","claude_version":"2.1.301 (Claude Code)","claude_version_expected":"2.1.296","supervisor":{"running":true,"own_unit":true},"cap":4,"active_workers":0,"reason":"Claude Code 2.1.301 is installed, but Marcel needs 2.1.296."}'
RUNNER_NO_SUPERVISOR='{"ok":false,"version":"0.1.10","claude_version":"2.1.301 (Claude Code)","claude_version_expected":"2.1.296","supervisor":{"running":false,"own_unit":true},"cap":4,"active_workers":0}'
HUB_OK='{"ok":true,"version":"0.1.0","runner":{"reachable":true}}'
PATH_OK='{"ok":true,"hub":"ok","runner":{"state":"ok","latency_ms":3},"brains":[{"agent_id":"agt_1","state":"ok","last_seen_s":4}]}'
PATH_DRIFT='{"ok":false,"hub":"ok","runner":{"state":"version_drift","reason":"x","latency_ms":3},"brains":[{"agent_id":"agt_1","state":"down","reason":"No brain session yet."}]}'
PATH_DOWN='{"ok":false,"hub":"ok","runner":{"state":"down","reason":"x"},"brains":[]}'

failures=0
expect() {  # expect <code> <name> FAKE_…=… …
    local want="$1" name="$2"
    shift 2
    local out code
    out="$(env PATH="$work/bin:$PATH" MARCEL_HOME="$work/home" HEALTH_WAIT=1 "$@" bash "$here/health.sh" 2>&1)"
    code=$?
    if [ "$code" -ne "$want" ]; then
        echo "FAIL $name: exit $code, expected $want"
        echo "$out" | sed 's/^/    /'
        failures=$((failures + 1))
    else
        echo "ok   $name (exit $code)"
    fi
    LAST="$out"
}

expect 0 'all ok' FAKE_RUNNER="$RUNNER_OK" FAKE_HUB="$HUB_OK" FAKE_PATH="$PATH_OK"
expect 2 'claude version drift only' FAKE_RUNNER="$RUNNER_DRIFT" FAKE_HUB="$HUB_OK" FAKE_PATH="$PATH_DRIFT"
case "$LAST" in *'Run deploy/install-claude.sh'*) ;; *) echo "FAIL drift: no plain message"; failures=$((failures + 1)) ;; esac
started=$SECONDS
expect 2 'drift stops waiting for a brain at once' HEALTH_WAIT=30 FAKE_RUNNER="$RUNNER_DRIFT" FAKE_HUB="$HUB_OK" FAKE_PATH="$PATH_DRIFT"
if [ $((SECONDS - started)) -gt 10 ]; then echo "FAIL drift: waited for the brain"; failures=$((failures + 1)); fi
expect 1 'drift and a unit down' FAKE_UNIT=inactive FAKE_RUNNER="$RUNNER_DRIFT" FAKE_HUB="$HUB_OK" FAKE_PATH="$PATH_DRIFT"
expect 1 'drift and the hub down' FAKE_RUNNER="$RUNNER_DRIFT" FAKE_HUB='' FAKE_PATH=''
expect 1 'drift without a supervisor is down' FAKE_RUNNER="$RUNNER_NO_SUPERVISOR" FAKE_HUB="$HUB_OK" FAKE_PATH="$PATH_DOWN"
expect 1 'runner not answering' FAKE_RUNNER='' FAKE_HUB="$HUB_OK" FAKE_PATH="$PATH_DOWN"
expect 1 'whole path down' FAKE_RUNNER="$RUNNER_OK" FAKE_HUB="$HUB_OK" FAKE_PATH="$PATH_DOWN"

if [ "$failures" -ne 0 ]; then
    echo "$failures health.sh case(s) failed."
    exit 1
fi
echo "health.sh: every exit code as documented."
