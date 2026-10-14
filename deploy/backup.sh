#!/usr/bin/env bash
# Back up Marcel's state on the NUC: a consistent copy of the hub's database and a git bundle of the
# memory repo, into ~/marcel/backups/<date>/, keeping 14 days. Safe while the hub runs (SQLite's
# online backup API). Run nightly by marcel-backup.timer; by hand: deploy/backup.sh
# Restore: docs/operating.md, "Backups".
set -euo pipefail
MARCEL_HOME="${MARCEL_HOME:-$HOME/marcel}"
DATA="$MARCEL_HOME/data"
DEST_ROOT="${MARCEL_BACKUPS:-$MARCEL_HOME/backups}"
KEEP_DAYS="${MARCEL_BACKUP_DAYS:-14}"
STAMP="$(date -u +%Y-%m-%d)"
DEST="$DEST_ROOT/$STAMP"

die() { echo "Backup failed: $*" >&2; exit 1; }
[ -f "$DATA/marcel.db" ] || die "no database at $DATA/marcel.db."
mkdir -p "$DEST"
chmod 700 "$DEST_ROOT" "$DEST"

# 1. The database, through SQLite's online backup (a consistent copy even while the hub writes).
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

# 2. The memory repo as one git bundle (every branch and its history). Its config names the hub's
#    container path for the work tree, so the host's is passed explicitly.
GIT=(git --git-dir="$DATA/memory.git" --work-tree="$DATA/memory")
if "${GIT[@]}" rev-parse --verify -q HEAD >/dev/null; then
    "${GIT[@]}" bundle create "$DEST/memory.bundle" --all 2>/dev/null
    "${GIT[@]}" bundle verify "$DEST/memory.bundle" >/dev/null 2>&1 || die "the memory bundle does not verify."
else
    echo "The memory repo has no commits yet; no bundle."
fi

# 3. The small files the hub keeps beside them (secrets stay owner-only).
for f in hub-secret adopt-token.sha256 digests.json cloud-tokens.json runner-offsets.json; do
    [ -f "$DATA/$f" ] && cp -p "$DATA/$f" "$DEST/"
done
chmod 600 "$DEST"/* 2>/dev/null || true

# 4. Keep 14 days.
find "$DEST_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20??-??-??' -mtime +"$KEEP_DAYS" -exec rm -rf {} +

echo "Backed up to $DEST: $(du -sh "$DEST" | cut -f1)."
