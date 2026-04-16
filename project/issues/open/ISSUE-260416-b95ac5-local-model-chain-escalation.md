# ISSUE-b95ac5: Local-pinned jobs silently escalate to cloud via fallback chain

**Status:** Open
**Created:** 2026-04-16
**Assignee:** Unassigned
**Priority:** High
**Labels:** bug, jobs, model-chain

## Capture
**Original request:** "I notice from the logs that the last syncs ran using gpt4.1, not the local qwen model (even with allow fallback chain = false). Can you investigate and fix."

**Follow-up Q&A:** None

**Resolved intent:** When a job is pinned to a local model (`model: local:<tag>`), the fallback chain should never silently escalate to cloud providers. The current code allows this when `allow_fallback_chain` defaults to `true`, and the docstring warns users to always set it to `false` manually — but this is a footgun that should be enforced automatically. A secondary issue is that the scheduler tick loop silently died, so the user's manual fix (`allow_fallback_chain: false`) was never exercised.

## Description

The bank-sync job was configured with `model: local:qwen3.5:4b` and `allow_fallback_chain: false`, but the run logs show it using `anthropic:claude-haiku-4-5-20251001` (failing due to exhausted Anthropic credits) then falling back to `openai:gpt-4.1` (the `MARCEL_BACKUP_MODEL`).

Investigation revealed three issues:

1. **The runs predate the config edit.** The user changed `model` and `allow_fallback_chain` at 20:14 on April 15, but the last scheduler dispatch was at 13:49. No runs happened after the edit to test the new config.

2. **The scheduler tick loop silently died** after April 15 ~18:01. No dispatches for any job since then. The container is healthy, the old hardcoded banking sync loop still runs, but the job scheduler's tick loop stopped without a crash log. The `_cleanup_loop` (daily memory consolidation) is still alive.

3. **Code-level footgun:** `execute_job_with_retries` checks `job.allow_fallback_chain` to decide between `_execute_chain` (with cloud tiers) and `_execute_pinned_with_legacy_fallback` (pinned). When a job uses `model: local:<tag>` but `allow_fallback_chain` defaults to `true`, the chain silently includes cloud backup tiers — defeating the purpose of pinning to local. The `JobDefinition.allow_fallback_chain` docstring warns about this, but the guard should be automatic.

## Tasks
- [ ] ISSUE-b95ac5-a: In `execute_job_with_retries`, auto-force `allow_fallback_chain=False` when `job.model.startswith('local:')` with a log warning
- [ ] ISSUE-b95ac5-b: Add regression test verifying local-pinned jobs never escalate to cloud tiers
- [ ] ISSUE-b95ac5-c: Add periodic heartbeat logging to the scheduler tick loop so silent deaths are detectable
- [ ] ISSUE-b95ac5-d: Add a catch-all guard in `_tick_loop` that restarts the loop on unexpected exit (with backoff)

## Relationships
- Related to: ISSUE-076 (four-tier model fallback chain)
- Related to: ISSUE-070 (local LLM fallback)

## Comments

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
