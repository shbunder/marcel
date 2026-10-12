---
name: board
description: The build board — "status" (default) summarises progress for the owner; "sync" copies story definitions from project/stories onto the board; "set <id> <status> [reason]" moves a card by hand (Blocked, or a "Board:" line from a PR). Use whenever the owner asks how the build is going.
---

Board: $ARGUMENTS

Always export first (`.claude/rules/board.md`); keep the result text, it has each row's version.

**status** — `make lanes`, plus the last 10 `log` rows (`query`, `order_by: {field: "at", direction:
"desc"}`, `limit: 10`). Answer in plain words: done / total, what is in flight and where, what is blocked
and why, what is ready next per machine. Link the board.

**sync** — after any change in `project/stories/`:
`python3 scripts/board.py sync .board/sync.json`. Send it as ArtifactData `batch` (≤ 50 per call). For
entries with `needs_if_version`, replace that key with `if_version` = the row's version from the export,
and add `updated_at`. Rows reported as "on the board but not in project/stories": ask the owner before
deleting. Add one `log` row: `synced from <short sha>: N new, M updated`.

**set <id> <status> [reason]** — `get`, then `update` with `if_version`: `status`, `updated_at`, and
`blocked_reason` (set for Blocked, `""` otherwise). One `log` row. Only `/merge-story` sets Done.
