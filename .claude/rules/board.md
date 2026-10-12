# Rule — the build board holds status; git holds definitions

**Board:** https://claude.ai/artifact/1V2swuomSbb9r1cB23Y2eh — a claude.ai artifact with a shared database.
Read and write it with the `ArtifactData` tool (load it with ToolSearch `select:ArtifactData`).

## What lives where

| Thing | Home | Why |
|---|---|---|
| Features, stories, `depends:`, `touches:`, "done when" | `plan/features/*.md`, `plan/03-spikes.md` (git) | Reviewed in PRs; the story is the contract |
| Decisions (ADRs) | `project/decisions/ADR-NNN-slug.md` (git) — `/adr` | They explain the code; they age with it |
| Lessons for the next agent | `project/lessons/<topic>.md` (git) | Same |
| Status, owner, branch, PR link, blocked reason | **board**, collection `stories` | Changes many times a day; never worth a commit |
| What happened (claims, hand-overs, contract requests) | **board**, collection `log` | An audit trail without commit noise |

Never write status into a tracked file: no `status:` lines in the plan, no `lanes.md`, no "mark done" commits.

## The collections

`stories/<id>` — one row per story or spike (`S-04.9`, `SP1`). Definition fields are copied from the plan by
`/board sync`: `feature, feature_title, wave, title, runs_on, size, depends, touches, behaviours, source`.
Board-only fields: `status` (`todo | doing | review | blocked | done`), `owner`, `branch`, `pr`,
`blocked_reason`, `updated_at` (ISO time).

`log/<NNNN-slug>` — `{at, story, kind, text, by}`; `kind` is `status | note | contract-request | merge`.
Ids sort by time: use the current UTC time, e.g. `20261012T1405-S-04.9-claim`.

## Status flow

```
todo ──/story──► doing ──/finish-story──► review ──/merge-story──► done
                   └──── blocked (blocked_reason set) ────┘
```

- Only `/story` moves `todo → doing`, and only for a story `board.py ready` lists (deps done, no file clash).
- `done` means **merged to main**. Only `/merge-story` (the lead) sets it.
- Every status change also adds a `log` row. Pin every update with `if_version` from the row you just read; if
  it fails, someone else moved the story — re-read and stop if it is no longer yours.

## When the board is out of reach

If a session has no `ArtifactData` tool (it has not been confirmed in claude.ai cloud sessions), it does the work
anyway and writes the status change it *would* have made as the last line of its PR description, e.g.
`Board: S-04.9 → review`. The lead applies it with `/board`.

## Why

Status in git meant a commit per flip, merge conflicts on a shared status file between parallel agents, and a
board you could only see with a checkout. The artifact is live on the owner's phone, conflict-safe through
`if_version`, and costs no commits.
