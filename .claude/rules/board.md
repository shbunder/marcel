# Rule — git holds what a story is; the build board holds where it is

**Build board:** https://claude.ai/artifact/1V2swuomSbb9r1cB23Y2eh — a claude.ai artifact with a shared
database, live on the owner's phone. Sessions read and write it with the `ArtifactData` tool (load it with
ToolSearch `select:ArtifactData`).

| What | Where |
|---|---|
| A story's definition: title, depends, touches, do, done when | `project/stories/<id>.md` (git, reviewed) |
| Decisions, lessons, contract requests | `project/decisions/`, `project/lessons/`, `project/contract-requests/` (git) |
| Status, owner, branch, PR, blocked reason | board, collection `stories` (row id = story id) |
| What happened: claims, hand-overs, merges | board, collection `log` (`{at, story, kind, text, by}`) |

Statuses: `Backlog → In Progress → Review → Done`, or `Blocked` (with `blocked_reason`).
`Done` means on `main`: `/finish-story` sets it when it lands a story, `/merge-story` when it merges a PR.
`Review` is only for a PR that waits for the owner (ADR-012).

## Never
- A `status:` line in a story file (`scripts/board.py` refuses to load one).
- A commit whose only purpose is a status change.
- A board write without `if_version` from the row you just read. If the pin fails, someone else moved
  it: re-read, and stop if it is no longer yours.

## Always
- Export before deciding: ArtifactData `list`, collection `stories`, `query: {limit: 1000}`,
  `out_dir: .board/export`. `scripts/board.py` (`make lanes`, `make start`) reads that export.
- One `log` row per status change. Ids sort by time: `20261012T1405-S-04.9-claim`.
- After editing `project/stories/`, run `/board sync` so the board matches.

## If the board is out of reach
A session without `ArtifactData` (not yet confirmed in claude.ai cloud sessions) does the work and ends its
PR description, or the merge commit message when it lands without a PR, with the move it could not make,
e.g. `Board: S-04.9 → Done`. The lead applies it.

## Why
Status in git meant a commit per move, conflicts between parallel agents on the same files, and a board you
could only see with a checkout.
