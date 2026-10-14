#!/usr/bin/env bash
# redeploy.sh against a scratch "GitHub" (a bare repo), a scratch ~/marcel/src cloned from it, a fake `make` and a
# health check that fails while the deployed copy has a file called BROKEN. Nothing reaches GitHub, a unit or the hub.
# Run from deploy/: bash tests/test_redeploy.sh (part of `make -C deploy check`).
set -uo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
failures=0
fail() { echo "FAIL $*"; failures=$((failures + 1)); }
ok() { echo "ok   $*"; }

mkdir -p "$work/bin" "$work/home/state"
cat >"$work/bin/make" <<'STUB'
#!/usr/bin/env bash
echo "$*" >>"$FAKE_MAKES"
STUB
chmod +x "$work/bin/make"
export PATH="$work/bin:$PATH" FAKE_MAKES="$work/makes" MARCEL_HOME="$work/home" MARCEL_REDEPLOY_WAIT=0
STATE="$work/home/state"

git init -q --bare -b main "$work/origin.git"
git clone -q "$work/origin.git" "$work/seed" 2>/dev/null
seed=(git -C "$work/seed")
mkdir -p "$work/seed/deploy"
cp "$here/redeploy.sh" "$work/seed/deploy/redeploy.sh"
cat >"$work/seed/deploy/health.sh" <<'STUB'
#!/usr/bin/env bash
src="$(cd "$(dirname "$0")/.." && pwd)"
[ -n "${FAKE_DIRTY:-}" ] && echo dirty >"$src/app.txt"  # a local edit that stops a revert
[ -n "${FAKE_HEALTH:-}" ] && exit "$FAKE_HEALTH"
[ -e "$src/BROKEN" ] && exit 1
exit 0
STUB
chmod +x "$work/seed/deploy/redeploy.sh" "$work/seed/deploy/health.sh"
echo v1 >"$work/seed/app.txt"
"${seed[@]}" add -A && "${seed[@]}" commit -q -m 'first' && "${seed[@]}" push -q origin main
git clone -q "$work/origin.git" "$work/src" 2>/dev/null
src=(git -C "$work/src")

commit() {  # commit <message> <file> <content>: a commit on GitHub's main; prints its id
    echo "$3" >"$work/seed/$2"
    "${seed[@]}" add -A && "${seed[@]}" commit -q -m "$1" && "${seed[@]}" push -q origin main
    "${seed[@]}" rev-parse HEAD
}
redeploy() {  # redeploy <flag content> [env…]: write the flag the way the hub does, run the script
    local flag="$1"; shift
    printf '%s' "$flag" >"$STATE/redeploy.flag"
    rm -f "$STATE/redeploy.result" "$work/makes"
    env "$@" bash "$work/src/deploy/redeploy.sh"
}
field() { python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "$STATE/redeploy.result" "$1" 2>/dev/null; }
head_of() { git -C "$1" rev-parse "${2:-HEAD}"; }
makes() { [ -f "$work/makes" ] && grep -c install "$work/makes" || echo 0; }

# The flag must hold one full commit id that is on main.
before="$(head_of "$work/src")"
redeploy 'not a commit'
[ "$(field outcome)" = refused ] && [ "$(head_of "$work/src")" = "$before" ] && [ "$(makes)" = 0 ] \
    && [[ "$(field reason)" == *'does not hold one full commit id'* ]] && ok 'a flag without a commit id is refused' || fail 'bad flag'
redeploy "$(printf '%s\nrm -rf /\n' "$before")"
[ "$(field outcome)" = refused ] && [ "$(makes)" = 0 ] && ok 'a flag with more than an id is refused' || fail 'long flag'
echo local >"$work/seed/app.txt"; "${seed[@]}" commit -q -am 'not pushed'
redeploy "$("${seed[@]}" rev-parse HEAD)"
[ "$(field outcome)" = refused ] && [ "$(head_of "$work/src")" = "$before" ] && [ "$(makes)" = 0 ] \
    && [[ "$(field reason)" == *'is not on main on GitHub'* ]] && ok 'a commit that is not on main is refused' || fail "unpushed commit: $(field outcome)"
"${seed[@]}" reset -q --hard origin/main

# A healthy change goes in.
good="$(commit 'Hub: answer faster' app.txt v2)"
redeploy "$good$(printf '\n')"
[ "$(field outcome)" = deployed ] && [ "$(head_of "$work/src")" = "$good" ] && [ "$(makes)" = 1 ] \
    && [ "$(field subject)" = 'Hub: answer faster' ] && [ "$(stat -c %a "$STATE/redeploy.result")" = 600 ] \
    && ok 'a healthy change is deployed' \
    || fail "healthy: $(field outcome) $(field reason)"
redeploy "$good"
[ ! -e "$STATE/redeploy.result" ] && [ "$(makes)" = 0 ] && ok 'the same flag twice deploys once' || fail 'deployed twice'
older="$(head_of "$work/src" HEAD~1)"
redeploy "$older"
[ ! -e "$STATE/redeploy.result" ] && [ "$(head_of "$work/src")" = "$good" ] && [ "$(makes)" = 0 ] \
    && ok 'a commit already deployed changes nothing' || fail 'older commit redeployed'

# A broken change (a merge commit) is reverted on GitHub and deployed again.
"${seed[@]}" checkout -q -b feature
echo yes >"$work/seed/BROKEN"; "${seed[@]}" add BROKEN; "${seed[@]}" commit -q -m 'Break it'
"${seed[@]}" checkout -q main
"${seed[@]}" merge -q --no-ff -m 'Merge: break it' feature && "${seed[@]}" push -q origin main
broken="$("${seed[@]}" rev-parse HEAD)"
redeploy "$broken"
revert="$(field revert)"
if [ "$(field outcome)" = rolled_back ] && [ -n "$revert" ] && [ "$(head_of "$work/origin.git" main)" = "$revert" ] \
    && [ "$(head_of "$work/src")" = "$revert" ] && [ ! -e "$work/src/BROKEN" ] && [ "$(makes)" = 2 ] \
    && [ "$(git -C "$work/origin.git" rev-parse "$revert~1")" = "$broken" ]; then
    ok 'a broken change is reverted, pushed and deployed again'
else
    fail "rollback: $(field outcome) $(field reason)"
fi
"${seed[@]}" pull -q origin main 2>/dev/null

# A Claude Code version drift is not this change: no rollback.
drift="$(commit 'Runner: tidy' app.txt v3)"
redeploy "$drift" FAKE_HEALTH=2
[ "$(field outcome)" = version_drift ] && [ "$(head_of "$work/origin.git" main)" = "$drift" ] && [ "$(makes)" = 1 ] \
    && ok 'a version drift deploys and reverts nothing' || fail "drift: $(field outcome)"

# Still down after the revert: said plainly, the revert stays.
down="$(commit 'Hub: something' app.txt v4)"
redeploy "$down" FAKE_HEALTH=1; rc=$?
[ "$(field outcome)" = rollback_failed ] && [ "$rc" -ne 0 ] && [ "$(head_of "$work/origin.git" main~1)" = "$down" ] \
    && ok 'still down after the revert: rollback_failed' || fail "still down: $(field outcome)"
"${seed[@]}" pull -q origin main 2>/dev/null

# The revert itself fails (here a local edit to the file it touches): back to the previous version on the NUC only.
deployed="$(head_of "$work/src")"
stuck="$(commit 'Hub: stuck' app.txt v5)"
redeploy "$stuck" FAKE_HEALTH=1 FAKE_DIRTY=1; rc=$?
[ "$(field outcome)" = rollback_failed ] && [ "$rc" -ne 0 ] && [ "$(head_of "$work/src")" = "$deployed" ] \
    && [ "$(head_of "$work/origin.git" main)" = "$stuck" ] && [ "$(makes)" = 2 ] \
    && ok 'a revert that fails goes back to the previous version' || fail "revert fails: $(field outcome) $(field reason)"

# DRILL ONLY: another branch.
"${seed[@]}" checkout -q -b drill/x && echo d >"$work/seed/drill.txt" && "${seed[@]}" add drill.txt \
    && "${seed[@]}" commit -q -m 'Drill' && "${seed[@]}" push -q origin drill/x
onbranch="$("${seed[@]}" rev-parse HEAD)"
redeploy "$onbranch"
git -C "$work/src" fetch -q origin drill/x  # the deployed copy may well have the commit; main does not
[ "$(field outcome)" = refused ] && [[ "$(field reason)" == *'is not on main on GitHub'* ]] \
    && ok 'a drill commit is refused on main' || fail "drill on main: $(field outcome)"
redeploy "$onbranch" MARCEL_REDEPLOY_BRANCH=drill/x
[ "$(field outcome)" = deployed ] && [ "$(git -C "$work/src" branch --show-current)" = drill/x ] \
    && ok 'MARCEL_REDEPLOY_BRANCH deploys from that branch' || fail "drill branch: $(field outcome) $(field reason)"
"${seed[@]}" checkout -q main

# One redeploy at a time.
exec 8>"$STATE/redeploy.lock"; flock 8
redeploy "$(commit 'Later' app.txt v6)"
[ ! -e "$STATE/redeploy.result" ] && [ "$(makes)" = 0 ] && ok 'a second redeploy waits its turn' || fail 'ran beside another'
exec 8>&-

if [ "$failures" -ne 0 ]; then
    echo "$failures redeploy.sh case(s) failed."
    exit 1
fi
echo "redeploy.sh: every case as documented."
