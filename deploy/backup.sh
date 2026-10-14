#!/usr/bin/env bash
# Back up Marcel's state on the NUC into ~/marcel/backups/<date>/, keeping 14 days:
#   marcel.db        the hub's database, through SQLite's online backup (consistent while the hub writes)
#   memory.bundle    the memory repo's history (every branch), as one git bundle
#   memory.tar.gz    the memory files as they are now, including what the brain wrote and nobody committed yet
#   artifacts.tar.gz the artifact versions the database points to
#   every other file in the data folder (hub-secret, adopt-token.sha256, the hub's *.json state), not triage.log
# Run nightly by marcel-backup.timer; by hand: make -C deploy backup. Restore: docs/operating.md, "Backups".
set -euo pipefail
MARCEL_HOME="${MARCEL_HOME:-$HOME/marcel}"
DATA="$MARCEL_HOME/data"
DEST_ROOT="${MARCEL_BACKUPS:-$MARCEL_HOME/backups}"
KEEP_DAYS="${MARCEL_BACKUP_DAYS:-14}"
STAMP="$(date -u +%Y-%m-%d)"
DEST="$DEST_ROOT/$STAMP"

die() { echo "Backup failed: $*" >&2; exit 1; }
[ -f "$DATA/marcel.db" ] || die "no database at $DATA/marcel.db."
[ -d "$DATA/memory.git" ] || die "no memory repo at $DATA/memory.git."
mkdir -p "$DEST"
chmod 700 "$DEST_ROOT" "$DEST"

# One backup at a time: the timer and a run by hand write the same files.
exec 9>"$DEST_ROOT/.lock"
flock -n 9 || die "another backup is running."
trap 'rm -f "$DEST"/*.tmp' EXIT

# 1. The database, integrity-checked before it replaces the day's earlier copy.
/usr/bin/python3 - "$DATA/marcel.db" "$DEST/marcel.db.tmp" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
ok = dst.execute('pragma integrity_check').fetchone()[0]
dst.close(); src.close()
sys.exit(0 if ok == 'ok' else f'The copy failed its integrity check: {ok}')
PY
mv "$DEST/marcel.db.tmp" "$DEST/marcel.db"

# 2. The memory repo's history. Its config names the hub's container path for the work tree, so the
#    host's is passed explicitly.
GIT=(git --git-dir="$DATA/memory.git" --work-tree="$DATA/memory")
if "${GIT[@]}" rev-parse --verify -q HEAD >/dev/null; then
    "${GIT[@]}" bundle create --quiet "$DEST/memory.bundle.tmp" --all
    "${GIT[@]}" bundle verify --quiet "$DEST/memory.bundle.tmp" || die "the memory bundle does not verify."
    mv "$DEST/memory.bundle.tmp" "$DEST/memory.bundle"
else
    echo "The memory repo has no commits yet; no bundle (the files are in memory.tar.gz)."
fi

# 3. The memory files and the artifact versions as they are (links kept).
tar -C "$DATA" -czf "$DEST/memory.tar.gz.tmp" memory
mv "$DEST/memory.tar.gz.tmp" "$DEST/memory.tar.gz"
if [ -d "$DATA/artifacts" ]; then
    tar -C "$DATA" -czf "$DEST/artifacts.tar.gz.tmp" artifacts
    mv "$DEST/artifacts.tar.gz.tmp" "$DEST/artifacts.tar.gz"
fi

# 4. Every other file the hub keeps there (secrets, tokens, its small state files; a half-written .tmp goes at exit).
find "$DATA" -mindepth 1 -maxdepth 1 -type f \
    ! -name 'marcel.db*' ! -name 'triage.log*' -exec cp -p {} "$DEST/" \;
chmod 600 "$DEST"/*

# 5. Keep the newest KEEP_DAYS dates (today and the days before it), by the folder's name.
cutoff="$(date -u -d "-$((KEEP_DAYS - 1)) days" +%Y-%m-%d)"
for old in "$DEST_ROOT"/20??-??-??; do
    [ -d "$old" ] || continue
    [[ "$(basename "$old")" < "$cutoff" ]] && rm -rf "$old"
done

echo "Backed up to $DEST: $(du -sh "$DEST" | cut -f1)."
