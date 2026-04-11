# ISSUE-064: Job Scheduler Timezone Support

**Status:** Open
**Created:** 2026-04-11
**Assignee:** Claude
**Priority:** High
**Labels:** bug

## Capture
**Original request:** "the morning greeting job triggered at 9am, not 7am, there is a discrepancy with server time (probably UTC) and my local time (GMT+1 summer time)"

**Follow-up Q&A:** None needed — root cause is clear from code inspection.

**Resolved intent:** The job scheduler computes all cron times in UTC, so `0 7 * * *` fires at 07:00 UTC = 09:00 CEST. Jobs need timezone-aware scheduling so cron expressions are interpreted in the user's local time, including DST transitions.

## Description

The scheduler's `_compute_next_run` passes UTC-based datetimes to croniter, which means cron expressions like `0 7 * * *` are interpreted as 07:00 UTC rather than 07:00 local time. The user is in Europe/Brussels (UTC+2 during CEST), so the morning greeting fires 2 hours late.

**Affected jobs:**
- `c1f96e7741ac` — Good morning (`0 7 * * *`) → fires at 09:00 local instead of 07:00
- `341e749bde4b` — News sync (`0 6,18 * * *`) → fires at 08:00/20:00 local instead of 06:00/18:00

**Fix:**
1. Add optional `timezone` field to `TriggerSpec` (default: None = UTC for backward compat)
2. In `_compute_next_run`, when timezone is set, compute cron next-run in local time then convert to UTC
3. Update existing cron jobs to set `timezone: "Europe/Brussels"`

## Tasks
- [ ] Add `timezone: str | None` to `TriggerSpec` in `models.py`
- [ ] Update `_compute_next_run` in `scheduler.py` to handle timezone
- [ ] Update existing job JSON files with timezone
- [ ] Run `make check`

## Implementation Log
