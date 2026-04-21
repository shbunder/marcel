# ISSUE-5ca6dc: `redeploy.sh` clears the restart flag + sync docs to actual behavior

**Status:** Open
**Created:** 2026-04-21
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** bug, dev-environment, self-modification, docs

## Capture

**Original request (code-review finding on ISSUE-6b02d0):**

> Dev `restart_requested.dev` flag is never cleared. In prod, the in-container watchdog clears the flag after consuming it. In dev there is no watchdog (uvicorn is PID 1) and `redeploy.sh` does not touch the flag file either. Because `systemd.path`'s `PathExists=` triggers on path *existence* at unit (re)start, any `systemctl --user restart marcel-dev-redeploy.path`, host reboot, or user logout/login cycle after the first dev self-mod will spuriously re-trigger `redeploy.sh --env dev --force`.

Plus:

> `docs/self-modification.md:95-107` describes behaviors `redeploy.sh` does not implement — claims `redeploy.sh` clears the flag, records a known-good commit, polls `/health`, writes `restart_result`, and rolls back. None of that lives in `redeploy.sh`; it all lives in `watchdog/main.py`, which only runs in the prod container. Diff from 6b02d0 extended the lie rather than fixing it.

**Resolved intent:** Fix the dev-only lifecycle bug by making `redeploy.sh` clear the env-scoped `restart_requested.{env}` flag itself (since dev has no in-container watchdog to do it). Keep scope minimal — don't extend `redeploy.sh` into health-check / rollback territory; instead rewrite the offending docs section to describe the actual split of responsibilities (host-side `redeploy.sh` handles build + recreate + flag-clear; the in-container prod watchdog handles health-check + rollback + `restart_result`; dev has no in-container watchdog, so those steps are absent in dev and that is deliberate).

## Description

### The bug

`marcel-dev-redeploy.path` is a systemd path unit with `PathExists=$HOME/.marcel/watchdog/restart_requested.dev`. systemd re-evaluates `PathExists=` when the unit is (re)started. After the first dev self-mod:

1. `request_restart(sha)` writes `restart_requested.dev`.
2. `marcel-dev-redeploy.path` fires → `redeploy.sh --env dev --force`.
3. `redeploy.sh` rebuilds + recreates the dev container and exits. **The flag file still exists.**
4. On the next host reboot, `systemctl --user daemon-reload`, or `systemctl --user restart marcel-dev-redeploy.path`, systemd sees the file still exists and fires the service again. Spurious redeploy.

Prod is unaffected: the prod container has an in-container watchdog ([src/marcel_core/watchdog/main.py:96](../../src/marcel_core/watchdog/main.py#L96)) that calls `clear_restart_request()` after consuming the flag.

### The fix

Add one line at the top of [scripts/redeploy.sh](../../scripts/redeploy.sh) (after arg parsing, before the running-check):

```bash
# Clear the env-scoped restart flag — dev has no in-container watchdog to
# do this, and leaving the flag in place re-triggers marcel-dev-redeploy.path
# on any subsequent systemd restart / host reboot.
rm -f "$HOME/.marcel/watchdog/restart_requested.$ENV_NAME"
```

This is safe for prod too: prod's in-container watchdog usually clears the flag first, but the operation is idempotent — `rm -f` on a missing file is a no-op. Net effect: **one shared mechanism for flag cleanup**, reducing the dev-vs-prod implementation surface we set out to unify in ISSUE-6b02d0.

### The regression test

Pytest-level, in `tests/scripts/test_redeploy.py` (new file). Use a new `DRY_RUN=1` env-var short-circuit in `redeploy.sh` that exits immediately after the flag cleanup (before invoking `docker compose`). Test: write a fake flag file in a tmp dir, invoke `bash scripts/redeploy.sh --env dev --force` with `DRY_RUN=1` and `HOME=<tmp>`, assert flag gone.

The `DRY_RUN` hook is explicitly marked as a testing aid in the script — not a user-facing flag. Alternative considered: source the script inside the test and invoke a single function. Rejected because `redeploy.sh` is currently top-level statements, not functions — refactoring it into functions just to test it is scope creep.

### Docs sync

Rewrite the "Restart flow" section of [docs/self-modification.md](../../docs/self-modification.md) so it matches the actual split:

- **Host-side `redeploy.sh`** — rebuilds image, recreates container, clears the env-scoped flag.
- **In-container prod watchdog** (`src/marcel_core/watchdog/main.py`) — polls `/health`, writes `restart_result.prod`, triggers `rollback.py` on unhealthy.
- **Dev** — has no in-container watchdog (uvicorn is PID 1 in the dev container for `--reload`). Dev therefore has no automatic health-check or rollback. This is deliberate: dev self-mod is exercised by an attentive developer who can read logs, not by a production incident response.

Keep the explicit "why dev has no rollback" note so a future reader doesn't re-introduce a redundant watchdog in dev.

## Tasks

- [ ] Add the `rm -f "$HOME/.marcel/watchdog/restart_requested.$ENV_NAME"` cleanup line at the top of [scripts/redeploy.sh](../../scripts/redeploy.sh) (after arg parsing + whitelist, before running-check).
- [ ] Add `DRY_RUN` short-circuit (env-var) in `redeploy.sh` — if set, exit 0 after the cleanup. Document it as a test-only hook in the script's header comment.
- [ ] New test `tests/scripts/test_redeploy.py` — invokes `bash scripts/redeploy.sh --env dev --force` with `DRY_RUN=1` and tmp `HOME`; asserts the pre-seeded flag file is removed. Include a case for `--env prod` too.
- [ ] Rewrite the "Restart flow" section of [docs/self-modification.md](../../docs/self-modification.md) to describe the actual split. Make the "dev has no rollback, intentionally" point explicit.
- [ ] `grep -rn "clears the flag\|writes restart_result\|polls /health" docs/` returns no stragglers saying these behaviors live in `redeploy.sh`.
- [ ] `make check` green.

## Relationships

- Fixes: two code-reviewer findings on [[ISSUE-6b02d0]] (flag-file lifecycle + docs divergence).
- Related: [[ISSUE-020]] (the original prod flag-file mechanism this extends).

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
