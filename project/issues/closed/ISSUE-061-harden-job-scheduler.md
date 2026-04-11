# ISSUE-061: Harden Job Scheduler

**Status:** Closed
**Created:** 2026-04-11
**Assignee:** Shaun
**Priority:** Medium
**Labels:** feature, reliability

## Capture
**Original request:** "do a deep analysis, how does our job system compare to ~/repos/openclaw, is there something we can learn from that repo and improve upon?"

**Follow-up Q&A:** None — plan was approved as-is.

**Resolved intent:** Bring production-hardening patterns from OpenClaw's mature cron/job system into Marcel's job scheduler. The key gaps are: no job timeout (hanging agents block forever), no stuck job detection, fixed retry delay instead of exponential backoff, no transient vs permanent error distinction, no concurrent execution limit, no staggering, no failure alert cooldown, no run retention/cleanup. All improvements are additive schema changes with defaults, preserving backward compatibility.

## Description
Marcel's job system is clean and well-structured but lacks several reliability and operational features that OpenClaw has battle-tested. This issue brings the most impactful patterns over, adapted for Marcel's Python/asyncio/file-based architecture.

See plan file for full comparison and design: `.claude/plans/synthetic-tinkering-penguin.md`

## Tasks
- [✓] ISSUE-061-a: Phase 1 — Safety & Reliability (timeout, stuck detection, transient classification, backoff)
- [✓] ISSUE-061-b: Phase 2 — Operational (max concurrent runs, staggering, startup catchup)
- [✓] ISSUE-061-c: Phase 3 — Observability (consecutive errors, schedule auto-disable, alert cooldown, delivery tracking)
- [✓] ISSUE-061-d: Phase 4 — Housekeeping (run retention & cleanup)
- [✓] ISSUE-061-e: Tests for all new functionality
- [✓] ISSUE-061-f: Update tool.py to expose new params
- [✓] ISSUE-061-g: Update docs/jobs.md

## Relationships
- Related to: [[ISSUE-051-continuous-conversation-model]] (circuit breaker pattern reuse)

## Comments

## Implementation Log

### 2026-04-11 - LLM Implementation
**Action**: Hardened job scheduler with 12 production reliability features inspired by OpenClaw
**Files Modified**:
- `src/marcel_core/jobs/models.py` — Added `TIMED_OUT` status; added `timeout_seconds`, `backoff_schedule`, `consecutive_errors`, `last_error_at`, `schedule_errors`, `alert_after_consecutive_failures`, `alert_cooldown_seconds`, `last_failure_alert_at`, `retention_days` to JobDefinition; added `error_category`, `delivery_status`, `delivery_error` to JobRun
- `src/marcel_core/jobs/executor.py` — Added `classify_error()` for transient detection; wrapped `agent.run()` in `asyncio.wait_for` for timeout; rewrote retry loop with exponential backoff and transient-only retries; added consecutive error tracking; refactored `_notify_if_needed` with alert cooldown and delivery tracking
- `src/marcel_core/jobs/scheduler.py` — Added `_stagger_offset()` deterministic hash; added semaphore for max concurrent runs; added stuck job detection in tick loop; added `_resolve_stuck_runs()` on startup; added startup catchup with staggered overdue jobs; added schedule error auto-disable; added `_cleanup_loop()` for daily run retention; added `_load_state()` helper
- `src/marcel_core/jobs/__init__.py` — Added `cleanup_old_runs()` for run retention
- `src/marcel_core/jobs/tool.py` — Added `timeout_minutes` param to `create_job`/`update_job`; added consecutive error display in `get_job`; added `timed_out` status icon
- `docs/jobs.md` — Updated scheduler/executor docs, added error classification table, run status table, new JobDefinition fields
- `tests/jobs/test_executor.py` — Tests for `classify_error`, backoff schedule, model defaults
- `tests/jobs/test_scheduler.py` — Tests for `_stagger_offset`, `_compute_next_run` with stagger, schedule error auto-disable, startup catchup
- `tests/jobs/test_cleanup.py` — Tests for `cleanup_old_runs` (old runs, no finished_at, no file, all recent)
**Commands Run**: `make check`
**Result**: 691 tests passing (31 new), all linting/typecheck/format clean

**Reflection**:
- Coverage: 12/12 improvements from the plan addressed (timeout, stuck detection, transient classification, backoff, max concurrent, staggering, startup catchup, consecutive errors, schedule auto-disable, alert cooldown, delivery tracking, run cleanup)
- Shortcuts found: none — no TODOs, no bare excepts, no magic numbers without constants
- Scope drift: none — all changes match the approved plan
