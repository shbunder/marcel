# ISSUE-060: Improve Morning Digest Format and Delivery

**Status:** Closed
**Created:** 2026-04-11
**Assignee:** Claude
**Priority:** Medium
**Labels:** feature, jobs, telegram

## Capture
**Original request:** "the morning digest I got is a bit wierd... Marcel said 'your morning digest has been send' but what does it mean, this is the digest no? Marcel said 'this is what is in it', but is this now a summary? I expect Marcel to just say 'goodmorning, here are you updates for saturday: ...' then important events and 5ish articles (with link) and all formatted for Telegram (this is not formatted nicely for telegram)"

**Follow-up Q&A:** None yet.

**Resolved intent:** The morning digest suffers from two problems. First, the executor's `notify: "always"` policy sends `run.output` (the agent's conversational summary) as a second Telegram message on top of the `marcel(action="notify")` call the agent already made — so the user gets a meta-summary instead of (or in addition to) the actual digest. Second, the job system prompt produces the wrong tone and format: it outputs a summary of sections rather than a concise greeting with ~5 linked articles, and the job channel prompt forbids markdown formatting even though Telegram HTML conversion is available.

## Description

### Problem 1: Double notification
The job agent calls `marcel(action="notify", message="...")` to send the digest. Then the executor's `_notify_if_needed` sees `notify: "always"` and sends `run.output` (the agent's text response, which is a meta-summary like "your digest has been sent") as a second Telegram message. The user sees the summary, not the digest.

**Fix:** Track whether the agent already sent a notification during the run. If it did, skip the executor's automatic notification.

### Problem 2: Wrong tone, format, and content
- The agent says "your digest has been sent, here's what was in it" instead of just being the digest
- News items are summarised topic clusters, not individual articles with links
- The job channel prompt (`~/.marcel/channels/job.md`) says "plain text only — no markdown formatting", which conflicts with Telegram's HTML conversion pipeline
- User expects: casual greeting, today's events, ~5 articles with clickable links, Telegram-friendly formatting

**Fix:** Rewrite the job's system prompt for direct, casual output with article links. Update the job channel prompt to allow markdown (the formatting pipeline converts it). Restructure the prompt to produce ~5 curated articles rather than topic-cluster summaries.

### Problem 3: Job notify didn't reach Telegram (discovered during implementation)
The `_notify` tool checked `ctx.deps.channel == 'telegram'` but job agents have `channel='job'`, so notify calls were logged but never sent to Telegram. The actual delivery only happened through the executor's fallback `_notify_if_needed`. Fixed by also routing `channel='job'` to Telegram in the notify handler.

## Tasks
- [✓] ISSUE-060-a: Fix double-send — track in-run notifications and skip executor's `_notify_if_needed` when agent already notified
- [✓] ISSUE-060-b: Update job channel prompt (`~/.marcel/channels/job.md` and default) to allow markdown formatting
- [✓] ISSUE-060-c: Rewrite the morning digest job system prompt in `job.json` for correct tone, format, and article links
- [✓] ISSUE-060-d: Run `make check` to verify all changes pass (690 passed)

## Relationships
None.

## Comments

## Implementation Log

### 2026-04-11 — Implementation
**Action**: Fixed double-send, enabled markdown in job channel, rewrote digest prompt, fixed job notify routing
**Files Modified**:
- `src/marcel_core/harness/context.py` — Added `notified: bool` field to `MarcelDeps`
- `src/marcel_core/tools/marcel.py` — Set `ctx.deps.notified = True` in `_notify`; route `channel='job'` to Telegram; use `markdown_to_telegram_html` instead of `escape_html`
- `src/marcel_core/jobs/models.py` — Added `agent_notified: bool` field to `JobRun`
- `src/marcel_core/jobs/executor.py` — Set `run.agent_notified` from `deps.notified`; skip auto-notify when agent already notified; use `markdown_to_telegram_html` in `_notify_telegram`
- `src/marcel_core/defaults/channels/job.md` — Allow markdown formatting, clarify that notify message IS the user-facing output
- `~/.marcel/channels/job.md` — Same update (user override)
- `~/.marcel/users/shaun/jobs/c1f96e7741ac/job.json` — Rewritten digest prompt: casual Dutch greeting, calendar events, 5-7 articles with links, one-screen format
**Commands Run**: `make check`
**Result**: Success — 690 tests passed
**Reflection**:
- Coverage: 4/4 requirements addressed, plus one necessary bonus fix (job notify routing)
- Shortcuts found: none
- Scope drift: Problem 3 (job notify routing to Telegram) was not in original requirements but was a necessary fix — without it, the double-send fix would have broken delivery entirely since job agents never reached Telegram through notify
