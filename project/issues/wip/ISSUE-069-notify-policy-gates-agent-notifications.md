# ISSUE-069: Notify Policy Gates Agent-Initiated Notifications

**Status:** WIP
**Created:** 2026-04-12
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** bug, jobs

## Capture

**Original request:** *"the jobs give me a signal in telegram when they finish, that's not necessary, can you investigate please?"* — followed by *"option 3 seems to be the future proof way"* after being presented three fix options.

**Follow-up Q&A:**
- Investigated three sources of Telegram signals on job completion:
  - **News sync** (`notify=silent`) — fires anyway because its system prompt explicitly says *"Step 2: Report the summary via marcel(action=\"notify\", ...)"*.
  - **Bank sync** (`notify=on_failure`) — fires anyway because its system prompt says *"Report a brief summary of what was synced"*, which the agent interprets as calling notify.
  - **Good morning digest** (`notify=always`) — intentional; the digest IS the Telegram message.
- Presented three fix options:
  1. Edit the two offending job.json files.
  2. Fix the `sync` template and bootstrap prompts.
  3. Make the `notify` policy actually gate agent-initiated notifications.
- User picked option 3: *"option 3 seems to be the future proof way"*.

**Resolved intent:** The `notify` field on `JobDefinition` currently only gates the scheduler's *automatic* notification at the end of a run. The agent running inside the job can still call `marcel(action="notify")` at will, which sends a Telegram message regardless of the declared policy — and the job channel prompt and several job system prompts encourage it to do so. This is a leaky abstraction: the policy says "silent" but the user sees a message. Fix it so the notify policy is the single source of truth for whether a job can deliver to the user.

## Description

### Current behavior

1. `JobDefinition.notify` is one of `always`, `on_failure`, `on_output`, `silent`.
2. `_notify_if_needed()` in [src/marcel_core/jobs/executor.py:259](src/marcel_core/jobs/executor.py#L259) correctly respects this policy for the scheduler's automatic notification.
3. However, the agent inside the job can also call `marcel(action="notify", message="...")`, which routes straight to Telegram via [src/marcel_core/tools/marcel/notifications.py:33](src/marcel_core/tools/marcel/notifications.py#L33). This path ignores the policy entirely.
4. The job channel prompt at [src/marcel_core/defaults/channels/job.md](src/marcel_core/defaults/channels/job.md) tells every job: *"Use `marcel(action=\"notify\", message=\"...\")` to send results to the user"*, which primes every job to notify on success — regardless of policy.

**Net effect:** jobs with `notify=silent` or `notify=on_failure` still send Telegram messages on success, confusing the user and making the policy field misleading.

### Desired behavior

The `notify` policy is the single source of truth for whether a job can deliver a user-visible message. Semantics:

- `silent` — no delivery at all. Agent calls to `marcel(action="notify")` are suppressed (no-op that tells the agent it was suppressed). Scheduler auto-notify is skipped.
- `on_failure` — no success delivery. Agent calls are suppressed on success runs. Scheduler auto-notify only fires on failure (existing behavior).
- `on_output` — delivery happens when there is output. Agent calls pass through (preferred; they dedupe via the existing `run.agent_notified` flag). If the agent doesn't notify, scheduler sends the output.
- `always` — delivery always happens. Agent calls pass through (preferred for digest-type jobs that compose a rich message). If the agent doesn't notify, scheduler sends the output.

Concretely:

- `suppress_notify = policy in (SILENT, ON_FAILURE)`. When True, `notify()` is a no-op.
- The job system prompt gets a `## Delivery policy` block explaining what the agent is allowed to do, so well-behaved agents don't even try to notify.
- The job channel prompt stops unconditionally instructing the agent to use `marcel(action="notify")`.

### Non-goals

- Not changing the public schema of `JobDefinition` — no new fields on the job.json files; everything derives from `notify`.
- Not changing scheduler auto-notify logic in `_notify_if_needed` — it already handles policy correctly.
- Not touching the double-append-run issue called out in the ISSUE-061 lessons learned.

## Tasks

- [✓] Add `suppress_notify: bool` field to `TurnState` in [src/marcel_core/harness/context.py](src/marcel_core/harness/context.py)
- [✓] Executor sets `deps.turn.suppress_notify = job.notify in (SILENT, ON_FAILURE)` before running the agent
- [✓] `marcel(action="notify")` checks `ctx.deps.turn.suppress_notify` — if True, log and return a "suppressed by policy" string without calling Telegram and without marking `notified=True`
- [✓] Executor injects a `## Delivery policy` block into the job system prompt, tailored to `job.notify`
- [✓] Rewrite the `job.md` channel prompt to defer to the delivery policy block instead of unconditionally telling the agent to notify
- [✓] Add unit test: `notify()` with `suppress_notify=True` returns the suppression string and does not call `_notify_telegram`
- [✓] Add unit test: executor sets `suppress_notify` correctly for each `NotifyPolicy` value
- [✓] Add unit test: `_build_job_context` includes a policy-specific delivery block
- [✓] `make check` passes
- [ ] Update [docs/jobs.md](docs/jobs.md) if it references the notify policy or the agent's notify flow

## Relationships

- Related to: [[ISSUE-060-improve-morning-digest-format-and-delivery]] (introduced the `deps.notified` flag)
- Related to: [[ISSUE-061-harden-job-scheduler]] (introduced `_notify_if_needed` and the policy field)

## Implementation Log

### 2026-04-12 - LLM Implementation
**Action**: Made notify policy the single source of truth for job deliveries.

**Files Modified**:
- `src/marcel_core/harness/context.py` — added `TurnState.suppress_notify`
- `src/marcel_core/jobs/executor.py` — wires `suppress_notify` from `job.notify`, appends a `## Delivery policy` block to the job system prompt
- `src/marcel_core/tools/marcel/notifications.py` — `notify()` early-returns a suppression string when `ctx.deps.turn.suppress_notify` is True, without touching Telegram or setting `notified=True`
- `src/marcel_core/defaults/channels/job.md` — rewrote the delivery-style section to defer to the delivery-policy block
- `~/.marcel/channels/job.md` — synced with the bundled default (data-root copy had drifted, same pattern as ISSUE-067 lesson)
- `tests/tools/test_marcel_tool.py` — added `test_suppressed_by_policy_does_not_send`
- `tests/jobs/test_executor_scenarios.py` — parametric tests for delivery-policy block injection and `suppress_notify` wiring

**Commands Run**: `make check`
**Result**: 1133 passed, coverage 92.75%
**Next**: docs/jobs.md update, closing commit, push + restart
