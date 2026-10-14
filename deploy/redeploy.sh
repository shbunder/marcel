#!/usr/bin/env bash
# Redeploy an approved, merged self-change, and roll it back if Marcel does not come up healthy (S-11.2).
#
# Run ONLY by marcel-redeploy.service, which marcel-redeploy.path starts when the hub writes
# ~/marcel/state/redeploy.flag (.claude/rules/self-modification.md). Never call it from Marcel's code.
#
#   1. The flag must hold exactly one full commit id (40 lowercase hex characters), nothing else, and that
#      commit must be on origin/<branch>. Anything else is refused and nothing changes.
#   2. ~/marcel/src fast-forwards to it and `make -C deploy install` deploys it (hub image, runner, plugin, brain).
#   3. The health check from before the change (a change cannot judge itself) is polled for up to 120 s: 0 healthy,
#      2 a Claude Code version drift (not this change: no rollback, the owner is told to reinstall), anything else
#      until the time is up, or a failed install: roll back. The rollback reverts the commit, pushes the revert to
#      origin/<branch>, deploys again and checks again.
#   4. Every outcome is written to ~/marcel/state/redeploy.result; the hub tells the owner (hub/src/marcel_hub/watchdog).
#   5. A flag written while this runs (a second merge) is deployed before it ends: systemd starts no second run.
set -uo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
MARCEL_HOME="${MARCEL_HOME:-$HOME/marcel}"
STATE="$MARCEL_HOME/state"
FLAG="$STATE/redeploy.flag"
RESULT="$STATE/redeploy.result"
LOG="$STATE/redeploy.log"
# DRILL ONLY: the branch the deploy follows. Production is main; a drill points it at a throwaway branch
# (docs/operating.md, "Self-changes"), so a deliberately broken commit never lands on main.
BRANCH="${MARCEL_REDEPLOY_BRANCH:-main}"
WAIT="${MARCEL_REDEPLOY_WAIT:-120}"
GIT=(git -C "$SRC")

mkdir -p "$STATE"
exec 9>"$STATE/redeploy.lock"
flock -n 9 || { echo "A redeploy is already running; it reads the flag again before it ends."; exit 0; }
exec >>"$LOG" 2>&1
echo "== $(date -u +%FT%TZ) redeploy"

# The result the hub reads: one JSON object, written whole (temp file, then rename). One the hub has not told yet
# is never replaced: a second outcome in the same run goes to redeploy.<time>.result, which the hub tells after it.
result() {  # outcome, reason, [sha], [previous], [revert]
    local out="$RESULT"
    [ -e "$out" ] && out="$STATE/redeploy.$(date -u +%s%N).result"
    /usr/bin/python3 - "$out" "$@" <<'PY'
import json, os, subprocess, sys, datetime
path, outcome, reason, sha, previous, revert = (sys.argv[1:] + ['', '', '', ''])[:6]
def subject(ref):
    if not ref:
        return ''
    out = subprocess.run(['git', '-C', os.environ['SRC'], 'log', '-1', '--format=%s', ref],
                         capture_output=True, text=True)
    return out.stdout.strip()
data = {'outcome': outcome, 'reason': reason, 'sha': sha, 'subject': subject(sha),
        'previous': previous, 'revert': revert,
        'at': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')}
tmp = f'{path}.tmp'
with open(tmp, 'w') as f:
    json.dump(data, f)
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
    echo "$1: $2"
}
export SRC

# The fast-forward below replaces this very file while bash reads it. git writes a new file rather than editing this
# one, so the run keeps reading what it started with; tests/test_redeploy.sh checks that.
HEALTH="$STATE/redeploy.health.sh"  # the health check from before the change
sha=""
previous=""
# systemd stops the run after TimeoutStartSec: say so instead of leaving no result.
trap 'result rollback_failed "The redeploy of ${sha:0:7} was stopped halfway (its 30 minutes ran out, or the NUC stopped or restarted it). Check the NUC: make -C deploy health, and ~/marcel/state/redeploy.log." "$sha" "$previous"; exit 1' TERM

# install's own health check is skipped: watch() does the waiting, with the health check from before the change.
deploy() { MARCEL_SKIP_HEALTH=1 make -C "$SRC/deploy" install; }
watch() {  # 0 healthy, 2 version drift, 1 not healthy within WAIT seconds (checked at least once)
    local end=$((SECONDS + WAIT)) rc
    while true; do
        HEALTH_WAIT=5 bash "$HEALTH" >/dev/null 2>&1
        rc=$?
        [ "$rc" -eq 0 ] && return 0
        [ "$rc" -eq 2 ] && return 2
        [ "$SECONDS" -lt "$end" ] || return 1
        sleep 5
    done
}

one() {  # deploy the commit the flag names
    # 1. The flag: exactly one commit id, on origin/<branch>.
    local size why rc reverted revert pushed
    size="$(stat -c %s "$FLAG" 2>/dev/null || echo 0)"
    sha="$(head -c 41 "$FLAG" 2>/dev/null | tr -d '\n')"
    if [ "$size" -gt 41 ] || ! [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
        sha=""
        result refused "The redeploy flag does not hold one full commit id, so nothing was changed."
        return 0
    fi
    if ! "${GIT[@]}" fetch -q origin "$BRANCH"; then
        result refused "Marcel could not fetch $BRANCH from GitHub, so nothing was changed. Upgrade by hand when GitHub answers (docs/operating.md, Upgrades)." "$sha"
        return 0
    fi
    if ! "${GIT[@]}" merge-base --is-ancestor "$sha" "origin/$BRANCH" 2>/dev/null; then
        result refused "Commit ${sha:0:7} is not on $BRANCH on GitHub, so nothing was changed." "$sha"
        return 0
    fi

    # 2. Deploy it (a commit the deployed copy already has needs nothing).
    previous="$("${GIT[@]}" rev-parse HEAD)"
    if "${GIT[@]}" merge-base --is-ancestor "$sha" "$previous"; then
        echo "${sha:0:7} is already in the deployed copy (${previous:0:7})."
        return 0
    fi
    if ! { "${GIT[@]}" checkout -q "$BRANCH" 2>/dev/null || "${GIT[@]}" checkout -q -b "$BRANCH" "origin/$BRANCH"; }; then
        result refused "The deployed copy could not switch to $BRANCH, so ${sha:0:7} was not deployed. Look at git -C ~/marcel/src status." "$sha" "$previous"
        return 0
    fi
    cp "$SRC/deploy/health.sh" "$HEALTH"
    if ! "${GIT[@]}" merge -q --ff-only "$sha"; then
        result refused "The deployed copy could not fast-forward to ${sha:0:7}: it has local changes or commits GitHub does not. Look at git -C ~/marcel/src status." "$sha" "$previous"
        return 0
    fi

    why=""
    if deploy; then
        watch
        rc=$?
    else
        why="Its install failed. "
        rc=1
    fi
    case $rc in
        0)
            result deployed "Marcel is up on the new version." "$sha" "$previous"
            return 0
            ;;
        2)
            result version_drift "Marcel runs the new version, but Claude Code is not the version Marcel is tested with. Run make -C deploy install." "$sha" "$previous"
            return 0
            ;;
    esac

    # 3. Not healthy: revert the change, push the revert, deploy again.
    echo "Not healthy (${why:-after ${WAIT} s}): rolling back ${sha:0:7}."
    if "${GIT[@]}" rev-parse -q --verify "$sha^2" >/dev/null; then
        reverted=("${GIT[@]}" revert --no-edit -m 1 "$sha")
    else
        reverted=("${GIT[@]}" revert --no-edit "$sha")
    fi
    if ! "${reverted[@]}"; then
        "${GIT[@]}" revert --abort 2>/dev/null
        "${GIT[@]}" reset -q --hard "$previous"
        deploy
        result rollback_failed "${why}It could not be reverted cleanly, so Marcel went back to ${previous:0:7} on the NUC only. GitHub still has ${sha:0:7} on $BRANCH." "$sha" "$previous"
        return 1
    fi
    revert="$("${GIT[@]}" rev-parse HEAD)"
    pushed="The revert is on GitHub."
    "${GIT[@]}" push -q origin "HEAD:$BRANCH" \
        || pushed="The revert could not be pushed to GitHub, so it is only on the NUC: git -C ~/marcel/src push origin $BRANCH."
    if deploy; then
        watch
        rc=$?
    else
        rc=1
    fi
    if [ "$rc" -eq 0 ]; then
        result rolled_back "${why}Marcel is up again. $pushed" "$sha" "$previous" "$revert"
        return 0
    fi
    if [ "$rc" -eq 2 ]; then
        result rolled_back "${why}Marcel runs again, but Claude Code is not the version Marcel is tested with: run make -C deploy install. $pushed" "$sha" "$previous" "$revert"
        return 0
    fi
    result rollback_failed "${why}Marcel is still not healthy after reverting it. $pushed Check the NUC: make -C deploy health." "$sha" "$previous" "$revert"
    return 1
}

while true; do
    seen="$(cat "$FLAG" 2>/dev/null)"
    one
    rc=$?
    [ "$(cat "$FLAG" 2>/dev/null)" = "$seen" ] && exit "$rc"
    echo "The flag changed during the redeploy: deploying what it names now."
done
