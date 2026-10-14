#!/usr/bin/env bash
# backup.sh against a scratch MARCEL_HOME: what it keeps, what makes it fail, and what it prunes.
# Run from deploy/: bash tests/test_backup.sh (part of `make -C deploy check`).
set -uo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
today="$(date -u +%Y-%m-%d)"
failures=0
fail() { echo "FAIL $*"; failures=$((failures + 1)); }
ok() { echo "ok   $*"; }

home() {  # a fresh MARCEL_HOME with a database, a memory repo (one commit and one uncommitted note) and artifacts
    rm -rf "$work/h"
    local d="$work/h/data"
    mkdir -p "$d/memory/agents/marcel" "$d/artifacts"
    python3 -c 'import sqlite3, sys; c = sqlite3.connect(sys.argv[1]); c.execute("create table t(x)"); c.execute("insert into t values (42)"); c.commit()' "$d/marcel.db"
    git --git-dir="$d/memory.git" init -q -b main
    echo committed >"$d/memory/agents/marcel/MEMORY.md"
    git --git-dir="$d/memory.git" --work-tree="$d/memory" add -A
    git --git-dir="$d/memory.git" --work-tree="$d/memory" -c user.name=t -c user.email=t@t commit -q -m one
    echo 'not committed' >"$d/memory/agents/marcel/brain-note.md"
    ln -s agents/marcel/MEMORY.md "$d/memory/MEMORY.md"
    echo v1 >"$d/artifacts/art_1.v1"
    for f in hub-secret adopt-token.sha256 usage-guard.json digests.json triage.log triage.log.1 x.json.tmp; do echo "$f" >"$d/$f"; done
}
run() { env MARCEL_HOME="$work/h" bash "$here/backup.sh" >"$work/out" 2>&1; }
# The result file a failed run leaves for the hub: result <field> prints that field.
result() { python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$work/h/state/backup.result" "$1"; }

# A full backup.
home
if run; then ok 'backs up'; else fail "backup failed: $(cat "$work/out")"; fi
B="$work/h/backups/$today"
[ "$(python3 -c 'import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute("select x from t").fetchone()[0])' "$B/marcel.db")" = 42 ] \
    && ok 'database copied' || fail 'database not copied'
git --git-dir="$work/h/data/memory.git" bundle verify --quiet "$B/memory.bundle" 2>/dev/null && ok 'bundle verifies' || fail 'no verified bundle'
tar -tzf "$B/memory.tar.gz" | grep -q 'memory/agents/marcel/brain-note.md' && ok 'uncommitted memory kept' || fail 'uncommitted memory missing'
tar -tvzf "$B/memory.tar.gz" | grep -q '^l.*memory/MEMORY.md -> ' && ok 'memory links kept' || fail 'memory link lost'
tar -tzf "$B/artifacts.tar.gz" | grep -q 'artifacts/art_1.v1' && ok 'artifacts kept' || fail 'artifacts missing'
for f in hub-secret adopt-token.sha256 usage-guard.json digests.json; do
    [ -f "$B/$f" ] || fail "$f not backed up"
done
[ ! -e "$B/triage.log" ] && [ ! -e "$B/triage.log.1" ] && ok 'triage log left out' || fail 'triage log backed up'
[ ! -e "$B/x.json.tmp" ] && ok 'half-written files left out' || fail 'a .tmp file was backed up'
[ "$(stat -c %a "$work/h/backups")$(stat -c %a "$B")" = 700700 ] && ok 'folders 0700' || fail 'folder modes'
bad="$(find "$B" -type f ! -perm 600)"
[ -z "$bad" ] && ok 'files 0600' || fail "files not 0600: $bad"
ls "$B"/*.tmp >/dev/null 2>&1 && fail 'left a .tmp' || ok 'no .tmp left'
[ ! -e "$work/h/state/backup.result" ] && ok 'a good run tells nobody' || fail 'a good run wrote a result'

# A corrupt database (it copies, but fails its integrity check) fails and keeps the day's earlier copy.
python3 - "$work/h/data/marcel.db" <<'PY2'
import os, sqlite3, sys
path = sys.argv[1]
os.remove(path)
c = sqlite3.connect(path)
c.execute('pragma page_size=1024')
c.execute('create table t(x text)')
c.execute('create index i on t(x)')
c.executemany('insert into t values (?)', [(f'row{n:05d}' * 5,) for n in range(2000)])
c.commit()
root = c.execute("select rootpage from sqlite_master where name='i'").fetchone()[0]
c.close()
data = bytearray(open(path, 'rb').read())
for page in range(root + 1, root + 4):
    data[(page - 1) * 1024 + 200:(page - 1) * 1024 + 300] = b'Z' * 100
open(path, 'wb').write(data)
PY2
if run; then fail 'corrupt database passed'; else ok 'corrupt database fails'; fi
[ "$(result outcome)" = failed ] && case "$(result reason)" in *'stopped while copying the database (exit '*) true ;; *) false ;; esac \
    && ok 'result: failed, at which step' || fail "result after a corrupt database: $(cat "$work/h/state/backup.result")"
[ "$(stat -c %a "$work/h/state/backup.result")" = 600 ] && ok 'result 0600' || fail 'result mode'
ls "$work/h/state"/*.tmp >/dev/null 2>&1 && fail 'result left a .tmp' || ok 'result written whole'
[ "$(python3 -c 'import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute("select x from t").fetchone()[0])' "$B/marcel.db")" = 42 ] \
    && ok 'earlier copy kept' || fail 'earlier copy lost'
ls "$B"/*.tmp >/dev/null 2>&1 && fail 'corrupt run left a .tmp' || ok 'corrupt run left no .tmp'

# A bundle that does not verify fails and keeps the day's earlier one (git answers `bundle verify` with an error).
home; run
cp "$B/memory.bundle" "$work/good.bundle"
mkdir -p "$work/badgit"
cat >"$work/badgit/git" <<STUB
#!/usr/bin/env bash
case "\$*" in *'bundle verify'*) exit 1 ;; esac
exec $(command -v git) "\$@"
STUB
chmod +x "$work/badgit/git"
echo more >"$work/h/data/memory/agents/marcel/MEMORY.md"
git --git-dir="$work/h/data/memory.git" --work-tree="$work/h/data/memory" -c user.name=t -c user.email=t@t commit -q -am two
if env PATH="$work/badgit:$PATH" MARCEL_HOME="$work/h" bash "$here/backup.sh" >"$work/out" 2>&1; then
    fail 'a bundle that does not verify passed'
else
    cmp -s "$B/memory.bundle" "$work/good.bundle" && ok 'bad bundle fails, earlier one kept' || fail 'earlier bundle lost'
fi

# No database, no memory repo: fail.
home; rm "$work/h/data/marcel.db"
if run; then fail 'missing database passed'; else ok 'missing database fails'; fi
case "$(result reason)" in 'no database at '*) ok 'result: the reason in plain words' ;; *) fail "result: $(result reason)" ;; esac
home; rm -rf "$work/h/data/memory.git"
if run; then fail 'missing memory repo passed'; else ok 'missing memory repo fails'; fi

# A repo with no commits yet: no bundle, the files still in the tarball.
home; rm -rf "$work/h/data/memory.git"; git --git-dir="$work/h/data/memory.git" init -q
if run && [ ! -e "$B/memory.bundle" ] && tar -tzf "$B/memory.tar.gz" | grep -q MEMORY.md; then
    ok 'empty repo: files without a bundle'
else
    fail "empty repo: $(cat "$work/out")"
fi

# Another backup running: refused.
home; mkdir -p "$work/h/backups"
exec 8>"$work/h/backups/.lock"; flock 8
if run; then fail 'ran beside another backup'; else ok 'second backup refused'; fi
exec 8>&-

# Prune: keep today and the 13 days before it, by name; leave other folders alone.
home
for n in $(seq 1 20); do mkdir -p "$work/h/backups/$(date -u -d "-$n days" +%Y-%m-%d)"; done
mkdir -p "$work/h/backups/keepme"
run
kept="$(find "$work/h/backups" -mindepth 1 -maxdepth 1 -type d -name '20??-??-??' | wc -l)"
oldest="$(date -u -d '-13 days' +%Y-%m-%d)"
if [ "$kept" -eq 14 ] && [ -d "$work/h/backups/$oldest" ] && [ -d "$work/h/backups/keepme" ]; then
    ok 'prune keeps 14 dated folders'
else
    fail "prune kept $kept dated folders"
fi

if [ "$failures" -ne 0 ]; then
    echo "$failures backup.sh case(s) failed."
    exit 1
fi
echo "backup.sh: every case as documented."
