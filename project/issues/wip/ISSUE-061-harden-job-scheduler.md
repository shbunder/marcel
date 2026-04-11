# ISSUE-061: Harden Job Scheduler

**Status:** WIP
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
- [ ] ISSUE-061-a: Phase 1 — Safety & Reliability (timeout, stuck detection, transient classification, backoff)
- [ ] ISSUE-061-b: Phase 2 — Operational (max concurrent runs, staggering, startup catchup)
- [ ] ISSUE-061-c: Phase 3 — Observability (consecutive errors, schedule auto-disable, alert cooldown, delivery tracking)
- [ ] ISSUE-061-d: Phase 4 — Housekeeping (run retention & cleanup)
- [ ] ISSUE-061-e: Tests for all new functionality
- [ ] ISSUE-061-f: Update tool.py to expose new params
- [ ] ISSUE-061-g: Update docs/jobs.md

## Relationships
- Related to: [[ISSUE-051-continuous-conversation-model]] (circuit breaker pattern reuse)

## Comments

## Implementation Log
