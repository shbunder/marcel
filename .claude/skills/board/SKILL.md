---
name: board
description: Look at or maintain the build board — "status" summarises progress and what is ready per machine; "sync" copies new or changed stories from plan/ onto the board; "set S-… <status> [reason]" moves a card by hand (e.g. blocked, or applying a "Board:" line from a PR). Use whenever the owner asks how the build is going.
---

Board: $ARGUMENTS   (`status` if empty)

The URL and fields are in [.claude/rules/board.md](../../rules/board.md). Start every action by exporting the
board: `ArtifactData` `list`, collection `stories`, `query: {limit: 1000}`, `out_dir: .board/export`
(keep the result text — it has each row's version).

## status

```bash
python3 .claude/board/board.py ready --board .board/export --runs-on cloud   # and NUC, Mac
```

Read the exported rows for counts per status and per feature, then the last 10 `log` rows
(`query`, `order_by: {field: "at", direction: "desc"}`, `limit: 10`). Answer in plain words: how far along
(done / total), what is in flight and on which machine, what is blocked and why, what is ready next. Link the board.

## sync  (after any change to `plan/features/` or `plan/03-spikes.md`)

```bash
python3 .claude/board/stories.py --out-dir .board/plan
python3 .claude/board/board.py sync --board .board/export --plan .board/plan --out .board/sync.json
```

Send `.board/sync.json` as one `ArtifactData` `batch` (split above 50). Entries marked `needs_if_version` are
updates: replace that key with `if_version` = the row's version from the export, and add `updated_at`.
If the script reports rows "on the board but not in the plan", ask the owner before deleting them.
Add one `log` row: `{kind: "note", text: "synced from plan @ <short sha>: N new, M updated"}`.

## set <id> <status> [reason]

`get` the row, then `update` with `if_version`: `status`, `updated_at`, and `blocked_reason` (set it for
`blocked`, clear it otherwise). Add a `log` row. Only the lead sets `done`, and only for a merged PR.
