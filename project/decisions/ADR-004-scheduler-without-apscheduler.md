---
id: ADR-004
title: The hub schedules from its own schedule table, without APScheduler
status: Accepted
date: 2026-10-12
decided_by: implementer (S-09.1), for the lead to confirm at review
evidence: S-09.1; hub/tests/test_schedule_cron.py, hub/tests/test_schedule.py
---

# ADR-004 — The hub schedules from its own schedule table, without APScheduler

## Context

`plan/02-architecture.md` names "APScheduler with a SQLite job store" for the scheduler, and S-09.1 repeats it.
The hub already has a `schedule` table (migration 0001) with `cron`, `tz`, `paused`, `last_run_at` and
`next_run_at`, and it already runs small loops of its own on its event loop (the usage guard, the board keeper).
S-09.1 needs: wall-clock times in the owner's time zone across clock changes, missed fires skipped and reported
once, a "did not run" deadline, and tests with a frozen clock.

Checked on APScheduler 3.11.3 with `zoneinfo` (the hub's time zones): a daily `30 2 * * *` in Europe/Brussels
fires **twice** on the autumn clock change, and the trigger then keeps returning the same instant (a loop that
never moves on). Its SQLAlchemy job store also pickles each job (the function's import path and arguments), a
second copy of what the `schedule` table already holds.

## Options

| | Option | Verdict |
|---|---|---|
| A | APScheduler 3 with its SQLAlchemy job store, as planned | Two stores that can disagree; pickled jobs break when a function is renamed; its clock cannot be frozen in tests without a time-faking library; the autumn double fire above |
| B | APScheduler's `CronTrigger` only, with our own loop and table | Keeps the dependency for its weakest part (the clock-change bug) |
| **C** | **Our own loop over the `schedule` table, and a 120-line five-field cron reader (`schedule/cron.py`)** | **Chosen.** One store, no new dependency, the clock injected like the usage guard's, each wall-clock time fires once |

## Decision

Option C. `schedule.Scheduler` looks every 30 seconds (sooner when a fire is due), fires through the task registry or
the channel outbox, and keeps `next_run_at` in the table; deadline watches live in `<data_dir>/scheduler.json`, as
the usage guard keeps its state, so no migration was needed. `cron.py` reads five fields (`*`, numbers, ranges,
steps, lists, three-letter names) and resolves each wall-clock time once: a time skipped in spring fires when the
clocks jump past it, a time that happens twice in autumn fires at its first occurrence.

## Consequences

- `plan/02-architecture.md` (Scheduler row and the tech list) is updated to match.
- Unlike standard cron, a repeating cron such as `*/15 * * * *` does **not** fire during the hour that repeats when
  the clocks go back: each wall-clock time fires at its first occurrence only, so 02:00–02:45 run once, not twice,
  and the second pass through that hour is quiet (an hour with no fires). Vixie cron would run them again.
- No "interval" schedules: the contract's `Schedule` has only `cron`. One can be added to `cron.py`'s caller later.
- If the hub ever needs many kinds of timed jobs (watchdogs, digests), they are rows in `schedule` or loops of
  their own; reconsider a library then.
