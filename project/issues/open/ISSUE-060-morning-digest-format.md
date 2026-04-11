# ISSUE-060: Improve Morning Digest Format and Delivery

**Status:** Open
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

## Tasks
- [ ] ISSUE-060-a: Fix double-send — track in-run notifications and skip executor's `_notify_if_needed` when agent already notified
- [ ] ISSUE-060-b: Update job channel prompt (`~/.marcel/channels/job.md` and default) to allow markdown formatting
- [ ] ISSUE-060-c: Rewrite the morning digest job system prompt in `job.json` for correct tone, format, and article links
- [ ] ISSUE-060-d: Run `make check` to verify all changes pass

## Relationships
None.

## Comments

## Implementation Log
