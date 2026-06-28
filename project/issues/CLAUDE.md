# Issue tracking has moved to the marcel-admin board

Marcel's project management no longer lives in this repo. It moved to the **marcel-admin board** — a
separate repo (`$MARCEL_ADMIN_DIR`, default `~/projects/marcel-admin`) that is an
[Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
bundle. Work is tracked there as **Feature → Story → Subtask**, with a requirements page and ADRs per
feature.

## Start work

- `/new-feature "<title>"` — allocates the feature + requirements page on the board, prints the
  `feat/FEAT-…-slug` branch to create here.
- `/new-story FEAT-… "<title>"` — a unit of work under a feature.
- `/new-adr FEAT-… "<decision>"` — record an architecture decision.
- `/finish-feature` — verify, `make check`, merge `--no-ff`, flip the feature to `Done` on the board.

Full conventions: `$MARCEL_ADMIN_DIR/JIRA/CLAUDE.md` and `$MARCEL_ADMIN_DIR/WIKI/CLAUDE.md`.
Procedure: [../FEATURE_WORKFLOW.md](../FEATURE_WORKFLOW.md).

## What's left in this directory

- **`closed/`** — the frozen archive of the 130 legacy issues (`ISSUE-…`, old sequential + date+hash
  schemes). **Read-only history.** `scripts/query_lessons.py` still mines it for `## Lessons Learned`.
- **`open/ISSUE-…f5796b-…`** — one issue was mid-flight at the cutover (the kernel/channels refactor).
  It finishes under the **old** flow on its own branch (which still carries the retired skills), then
  this file and that residue can be removed.

## Retired (do not reintroduce)

The `/new-issue` · `/finish-issue` · `/parallel-issue` skills, the `open/`→`wip/`→`closed/` lifecycle,
the `issue-task` CLI, the `📝`/`🔧`/`✅`/`🩹` commit emojis, and `TEMPLATE.md` / `GIT_CONVENTIONS.md`
are all retired. The board's `jira.py` (`check` / `note` / `set`) replaces `issue-task`; the cross-repo
git conventions live in `$MARCEL_ADMIN_DIR/JIRA/CLAUDE.md`.
