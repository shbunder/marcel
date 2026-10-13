---
id: ADR-010
title: Claude Code first — Marcel uses what Claude Code already does
status: Accepted
date: 2026-10-13
decided_by: owner, lead
evidence: Owner, 2026-10-13 (chat: "almost only an app pointing to a configured claude workflow"); Claude Code docs (hooks, hooks-guide § re-inject context after compaction, settings, agent-view, scheduled tasks, routines, remote control), read by the lead 2026-10-13
---

# ADR-010 — Claude Code first

## Context

Marcel is a shell around the unmodified `claude` CLI (02-architecture.md). Over the first waves some parts grew
their own version of something Claude Code already does: the brain rolls itself over at 70 % of its context
(S-06.7), and the hub only learns that a worker ended without `report()` from the runner's 2-second poll. The owner
wants Marcel to be "almost only an app pointing to a configured claude workflow". Every part Marcel builds itself is
code to keep up when Claude Code changes.

## Decision

**If Claude Code does it, Marcel uses it. Otherwise one line says why not.**

| Need | Decision | Claude Code feature, or why not |
|---|---|---|
| Brain context | **Switch** (S-06.8) | Built-in auto-compact (on by default; tuned with `autoCompactWindow` in settings or `CLAUDE_CODE_AUTO_COMPACT_WINDOW`), plus a `SessionStart` hook with matcher `compact` whose output re-adds `MEMORY.md`, the open tasks and today's hub summary (hooks-guide § re-inject context after compaction). S-06.7's 70 % rollover is retired; crash respawn and the manual "Fresh start" stay, a nightly rollover only as a safety net. |
| Session end, hand-off | **Switch** (S-05.8) | Plugin hooks `SessionEnd` (and `Stop` for workers) tell the hub at once, with the end reason. An instruction to call `report()` alone is unreliable. `SessionEnd` gets 1.5 s unless `timeout` is set (60 s at most). |
| Session list | Keep (built in) | `claude agents --json`. A research preview: the runner pins its output with fixtures. |
| Forks | Keep (built in) | `--fork-session`, `/branch`. |
| Cloud work | Keep (built in) | `claude --cloud`. |
| Approvals | Keep (built in) | The channel's permission relay. Alternative if it falls short: a `PermissionRequest` hook. |
| Live progress | Keep, own code | Tailing the raw transcript is the only way to stream a session live. Its `.jsonl` format is internal and may change; fixtures guard it. After the fact, prefer `claude -p --resume <id>`. |
| Scheduler | Keep, own code | Claude Code's `/loop` and cron need an open session and expire after 7 days; routines run only in the cloud. The hub's scheduler (ADR-004) stays. |
| Memory writes | Keep, own code | Through the hub: git history, and an ETag against edits from the app. Reads are plain files through the `MEMORY.md` symlink. |
| The iPhone app | Keep, own code | Remote Control has no continuous family conversation, no family members and no living artifacts. |

## Consequences

- New work starts by asking "does Claude Code do this?". A story that builds its own version says why in one line,
  in the story or in this table.
- S-06.8 replaces the context-size rollover. The S-06.7 code stays behind a setting that is off by default, so it
  can come back if auto-compact falls short on the NUC.
- S-05.8 adds plugin hooks. Both stories need small hub routes the contracts do not have yet (contract requests
  S-05.8 and S-06.8); the lead adds them in contract story S-02.9. S-05.8 waits for it; S-06.8 does not.
- `claude agents --json` and the transcript format can change under us: their fixtures are the alarm.
- CLAUDE.md lists "Claude Code first" among the core principles (owner-approved 2026-10-13) and points here.
