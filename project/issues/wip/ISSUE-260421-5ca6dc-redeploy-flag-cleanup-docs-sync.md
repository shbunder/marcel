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

- [✓] Add the flag cleanup line (`rm -f "$FLAG_DIR/restart_requested.$ENV_NAME"`, where `FLAG_DIR=${MARCEL_DATA_DIR:-$HOME/.marcel}/watchdog`) at the top of [scripts/redeploy.sh](../../scripts/redeploy.sh) after arg parsing + whitelist.
- [✓] Add `DRY_RUN` short-circuit (env-var) in `redeploy.sh` — if set, exit 0 after the cleanup. Documented as a test-only hook in the script header.
- [✓] New test `tests/core/test_redeploy.py` — invokes `bash scripts/redeploy.sh --env {dev,prod} --force` with `DRY_RUN=1` and tmp `HOME`; asserts the pre-seeded flag is removed and the other-env flag is untouched. Four tests (parametrised dev/prod + isolation + idempotency).
- [✓] Rewrite the "Restart flow" section of [docs/self-modification.md](../../docs/self-modification.md) to describe the actual split. The "dev has no rollback, intentionally" point is now an explicit subsection. Flag-files table updated — `restart_result.dev` now honestly shows *no writer*.
- [✓] Fixed two additional stragglers discovered during the docs grep: [.claude/rules/self-modification.md](../../.claude/rules/self-modification.md) and [project/FEATURE_WORKFLOW.md](../../project/FEATURE_WORKFLOW.md) both attributed the health-check + rollback to `redeploy.sh` — rewritten to attribute those behaviors to the prod in-container watchdog.
- [✓] `make check` green at 91.30% coverage, 1344 tests pass (4 new).

## Relationships

- Fixes: two code-reviewer findings on [[ISSUE-6b02d0]] (flag-file lifecycle + docs divergence).
- Related: [[ISSUE-020]] (the original prod flag-file mechanism this extends).

## Implementation Log

### 2026-04-21 — flag cleanup + test harness + docs rewrite

- **`scripts/redeploy.sh`**: added env-scoped flag cleanup (`rm -f "${MARCEL_DATA_DIR:-$HOME/.marcel}/watchdog/restart_requested.$ENV_NAME"`) immediately after arg parsing + whitelist. Idempotent: safe for prod too (prod's in-container watchdog normally clears the flag first; `rm -f` on a missing file is a no-op). Added `DRY_RUN=1` env-var short-circuit so tests can exercise the cleanup without a working docker runtime — documented as a test-only hook in the script header.
- **`tests/core/test_redeploy.py`** (new): four tests — parametrised dev/prod cleanup, other-env isolation, and idempotency when no flag exists. Uses `bash scripts/redeploy.sh` as a subprocess with `HOME=<tmp>`.
- **`docs/self-modification.md`**: rewrote "Restart flow" into four subsections that honestly describe the split — Agent side / Host side (`redeploy.sh`) / Health-check and rollback (prod-only watchdog) / Dev has no rollback — by design. Flag-files table gained a "Cleared by" column; `restart_result.dev` now honestly shows *(nothing — dev has no in-container watchdog by design)*.
- **Stragglers caught by verifier-driven repo-wide grep** (not just `docs/`): `README.md` and the rules files (`self-modification.md`, `security-auditor.md`) all carried the same two inaccuracies — (a) attributing rollback to `redeploy.sh`, (b) claiming the flag's SHA is *checked out* by `redeploy.sh`. Neither is true. Rewrote all five callsites: rollback lives in the in-container prod watchdog via `git revert HEAD` (not `redeploy.sh`, not `git checkout $SHA`), and the SHA is only used for logging today but remains a safety-critical input because any future code path that treats it as an execution parameter becomes RCE-on-host.
- **Docs opening and watchdog description fixes**: `docs/self-modification.md:5-6` reframed from "redeploy script does rebuild + rollback" to the accurate split. `docs/self-modification.md:126` dropped the incorrect "rebuilds" from the watchdog's rollback description — the watchdog only restarts uvicorn against reverted source (the image is unchanged; `./src` is bind-mounted).
- **`make check` green**: format + lint + typecheck clean, 1344 tests pass (4 new), coverage 91.30%.

**Known limitation (not a regression, documented here for the next reader):** In prod, `redeploy.sh` now clears the flag before the watchdog's 2-second poll inside the old container could consume it. This timing window is pre-existing — `docker compose up -d` tears down the old container regardless of what its watchdog is doing — but the cleanup moves the observable clear slightly earlier. Net effect is the same: the old container's watchdog won't process the flag because the container is about to disappear. If we ever give prod's watchdog post-recreate responsibilities that depend on reading the flag's contents from inside the *new* container, revisit this.

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE (after stragglers landed in a second 🔧 impl commit — verifier's first pass returned REQUEST CHANGES for 5 docs stragglers and 2 doc drifts; all addressed.)
- Coverage: 6/6 tasks addressed
- Shortcuts found: none
- Scope drift: none (the straggler sweep was in-scope — the issue explicitly covers docs-sync)
- Stragglers: caught and fixed — README.md (×2), docs/self-modification.md opening + watchdog description, .claude/rules/self-modification.md, .claude/agents/security-auditor.md

## Lessons Learned

### What worked well

- **Delegating to pre-close-verifier before declaring done.** The verifier caught five docs stragglers and two drifts my own straggler grep had missed — I'd scoped `grep` to `docs/` when the real perimeter was `README.md` + `.claude/` + `docs/`. A fresh pair of eyes with a repo-wide grep is cheap insurance.
- **`DRY_RUN=1` as a test seam in the bash script.** Four subprocess tests, no docker runtime required. The "Alternative considered: source the script" bullet in the issue description correctly flagged that refactoring to functions would have been scope creep.
- **Docs rewrite as four subsections with explicit role attribution.** "Agent side / Host side / Health-check and rollback (prod only) / Dev has no rollback — by design" maps 1:1 to the code; future readers can match any line of `redeploy.sh` or `watchdog/main.py` to exactly one section.

### What to do differently

- **Scope the straggler grep to the repo, not just `docs/`.** The `.claude/rules/` and `README.md` files are part of the authoring surface for every future agent; if they carry a false claim, every future session inherits it. Default grep scope should be `README.md docs/ .claude/ src/marcel_core/defaults/ ~/.marcel/skills/`.
- **Read the code you're documenting before rewriting the docs.** I had to re-read `watchdog/main.py` to confirm it doesn't call `docker compose build` — a two-minute read that would have prevented the "rebuilds" inaccuracy from shipping in the first docs-rewrite pass.

### Patterns to reuse

- **Bash test seam pattern.** `if [[ "${DRY_RUN:-}" == "1" ]]; then echo "..."; exit 0; fi` after the first meaningful side effect, documented in the script header as a test-only hook. Makes subprocess-level tests trivial without refactoring top-level statements into functions.
- **"Cleared by" column in flag-file tables.** When a file is touched by multiple processes, tables should name the writer, cleaner, and reader separately. Ambiguity here was the root cause of the docs divergence this issue fixed.
