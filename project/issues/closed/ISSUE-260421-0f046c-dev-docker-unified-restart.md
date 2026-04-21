# ISSUE-0f046c: Containerize the dev server + unify the restart path

**Status:** Closed
**Created:** 2026-04-21
**Closed:** 2026-04-21
**Assignee:** Unassigned
**Priority:** High
**Labels:** infra, dev-environment, self-modification, docker, reliability, duplicate

## Closed — duplicate of ISSUE-6b02d0

This issue duplicates [ISSUE-6b02d0](../closed/ISSUE-260419-6b02d0-containerize-dev-unified-restart.md), which shipped the identical scope two days earlier (2026-04-19) with all 22 tasks complete and `make check` green at 91.96% coverage.

Verification against the current tree:

| Artifact from the task list | State at close |
|---|---|
| `MARCEL_ENV` in `config.py` | Present — [config.py:37](../../src/marcel_core/config.py#L37) |
| `restart_requested.{env}` in `flags.py` | Present — [flags.py:90](../../src/marcel_core/watchdog/flags.py#L90) |
| `os.execv` in `src/` | Absent — grep returns zero matches |
| `docker-compose.dev.yml` | Present |
| `scripts/redeploy.sh --env` | Present |
| `deploy/marcel-dev-redeploy.{path,service}.tmpl` | Both present |
| `.claude/rules/self-modification.md` updated | Already reflects unified path |

No implementation work was performed on this branch — the task list below remains unchecked because those tasks were discharged by ISSUE-6b02d0, not by this issue.

## Capture

**Original request:**

> Run the dev server as a Docker container on port 7421 instead of on the host via `make serve`, so dev and prod share one runtime shape and the 29-hour "rogue host uvicorn on 7420" class of outage becomes impossible.
>
> ## Motivation
>
> We just paid for a "works in dev, breaks in prod" bug (ISSUE-efbaaa: discover() ordering) where dev only worked because `make serve` is a long-running process that had `discover()` triggered by prior interactive use. A cold-started dev container would have caught it. Separately, the prior outage root cause was an orphan host-side `uv run uvicorn ... --port 7420` left running from an old dev session — impossible if dev is containerized and the host never binds ports directly.
>
> ## Scope — option (a) unified restart path
>
> Per the self-modification rule, the restart mechanism is a safety-critical boundary and should be a single code path. Today dev uses `os.execv` in-place (src/marcel_core/main.py:83) while prod uses the flag-file + host-side `marcel-redeploy.path` systemd unit. That divergence is the exact shape of bug we want to stop paying for.
>
> The fix:
> 1. **`docker-compose.dev.yml`** overlay: same image as prod, binds `:7421` (from `MARCEL_DEV_PORT`), bind-mounts `./src` for live code edits, `command: uvicorn ... --reload`. Reuses the existing `Dockerfile`. Kept separate from `docker-compose.yml` so `docker compose up` still means "prod".
> 2. **Unified flag-file restart**: `redeploy.sh` takes `--env dev|prod` (default `prod`). A second systemd path unit `marcel-dev-redeploy.path` watches a second flag file (e.g. `restart_requested.dev`) and triggers `redeploy.sh --env dev` which rebuilds + recreates the dev container.
> 3. **`request_restart()` becomes env-aware**: reads `MARCEL_ENV` (set by each compose file) and writes the matching flag file. One call site, one mechanism, two flag paths.
> 4. **Delete the `os.execv` dev carve-out** in src/marcel_core/main.py:83 and update the dev-mode docstring. The `_restart_watcher` loop stays — it still reads the flag and writes the result — but the "outside Docker: exec-replace" branch goes away, because dev is now always Docker.
> 5. **Update `.claude/rules/self-modification.md`** to reflect that there is no longer a dev exception: one restart path, same mechanism in both environments.
> 6. **`make serve`** is either deleted or rewritten to `docker compose -f docker-compose.dev.yml up --build`. Prefer rewrite so muscle memory still works.
>
> ## Non-scope
>
> - Telegram routing is unaffected. The cloudflared tunnel on the NUC points at `localhost:7420` (prod), stays there, and dev never receives Telegram webhooks unless the operator temporarily re-points it with an ad-hoc `cloudflared tunnel --url http://localhost:7421`. No code changes for this — documented in telegram.md as a manual op.
> - `MARCEL_PUBLIC_URL` is a prod-only setting (Mini App buttons) and stays unset in dev.
> - Host-level port conflict detection (ISSUE-efbaaa follow-up option) is still worth doing but is a separate issue — this one supersedes it for the "rogue host uvicorn" case specifically, but a pre-flight `ss -lntp` check in `redeploy.sh` would still catch cases where an operator manually binds the port.
>
> ## Verification
>
> - `docker compose -f docker-compose.dev.yml up -d --build` produces a healthy container on :7421 with `--reload` working against `./src` edits.
> - `request_restart(sha)` from inside the dev container flips the dev flag, `marcel-dev-redeploy.path` fires, `redeploy.sh --env dev` rebuilds, container comes back healthy.
> - `request_restart(sha)` from inside the prod container still does the prod thing (unchanged behavior).
> - `src/marcel_core/main.py` no longer contains `os.execv`.
> - `grep -rn "os.execv" src/ tests/` returns nothing.
> - Regression test: a minimal test that imports `request_restart` and asserts it writes to the correct flag file based on `MARCEL_ENV`.
> - `make check` green.
>
> ## Tasks (rough)
>
> - Add `MARCEL_ENV` to `marcel_core/config.py` (typed, defaults `prod`).
> - Write `docker-compose.dev.yml` with `MARCEL_ENV=dev`, `MARCEL_PORT=7421`, bind-mount `./src`, `--reload`.
> - Rewrite `make serve` target to drive the dev compose file (or delete and update CLAUDE.md).
> - Split `watchdog/flags.py` restart flag names into `restart_requested.{env}` and read/write by `MARCEL_ENV`.
> - Update `request_restart()` to pick the right flag.
> - Render a second systemd template: `marcel-dev-redeploy.path` + `marcel-dev-redeploy.service` (reuse the existing template with an env substitution).
> - Update `scripts/setup.sh` to install the dev systemd units too.
> - Update `redeploy.sh` to take `--env dev|prod` and drive the correct compose file.
> - Delete the `os.execv` branch in `main.py`'s `_restart_watcher`.
> - Update `.claude/rules/self-modification.md` (remove the dev-exception carve-out).
> - Update `docs/self-modification.md` and `CLAUDE.md` (the ports/ports table and the "make serve" reference).
> - Regression test for env-aware flag-file write.
> - Verify all of the above, then switch my own dev loop to the new mechanism.

**Resolved intent:** Erase the dev/prod runtime-shape divergence by running the dev server in Docker on :7421 instead of on the host, and collapsing the two restart mechanisms (`os.execv` in dev, flag file + systemd in prod) into one env-aware flag-file path. The motivation is concrete: the last two bad days on Marcel (ISSUE-efbaaa and the 29-hour rogue-uvicorn outage) both trace back to dev behaving differently than prod. After this issue there is exactly one runtime shape and one restart code path; the only difference between dev and prod is the port number, the compose file, and an `--env dev|prod` flag suffix on the flag file.

## Description

**What changes:**

- **New file** `docker-compose.dev.yml` — overlay compose that reuses `Dockerfile`, exports `MARCEL_ENV=dev` and `MARCEL_PORT=7421`, bind-mounts `./src` for live edits, and runs `uvicorn ... --reload`. `docker-compose.yml` remains the prod definition so `docker compose up` unchanged means "prod".
- **Env-aware flag files.** `watchdog/flags.py` uses `restart_requested.{env}` where `{env}` comes from `MARCEL_ENV` (defaults to `prod`). `request_restart()` writes the right file; the watcher reads the right file. One function, two destinations.
- **Env-aware redeploy.** `redeploy.sh` grows `--env dev|prod` (default `prod`). A second systemd path unit `marcel-dev-redeploy.path` watches `restart_requested.dev` and fires `redeploy.sh --env dev`. `scripts/setup.sh` installs both unit pairs.
- **Remove the `os.execv` branch.** `src/marcel_core/main.py:83` currently has a non-Docker carve-out that calls `os.execv` to exec-replace the process. That code path is deleted. Dev is always Docker; there is no "outside Docker" branch to maintain.
- **Rule + docs update.** `.claude/rules/self-modification.md` drops the implicit dev exception and states plainly: one restart path, flag file + systemd, dev and prod identical except for flag suffix.
- **`make serve`** is rewritten to drive `docker-compose.dev.yml` so muscle memory still works (`make serve-logs`, `make serve-down` already exist per CLAUDE.md and remain valid).

**What does not change:**

- Telegram tunnel still points at prod on :7420. Dev does not receive Telegram webhooks by default.
- `MARCEL_PUBLIC_URL` stays prod-only.
- Host-level port conflict detection is out of scope (tracked separately). Containerizing dev removes the specific rogue-uvicorn-on-7420 failure mode; it does not replace a general pre-flight check.

**Why option (a) and not a lighter fix:** per `.claude/rules/self-modification.md`, the restart is a safety-critical boundary and must be one code path. Leaving `os.execv` in place "just for dev" is the exact pattern we want to delete — it is a silent divergence that will mask the next dev-only bug.

## Tasks

- [ ] Add `MARCEL_ENV` (`dev` | `prod`, default `prod`) to `src/marcel_core/config.py` with a typed field.
- [ ] Create `docker-compose.dev.yml` in the repo root: `MARCEL_ENV=dev`, `MARCEL_PORT=7421`, bind-mount `./src`, `command: uvicorn ... --reload`, reuses `Dockerfile`.
- [ ] Update `watchdog/flags.py` so restart flag paths are `restart_requested.{env}` and both the writer (`request_restart`) and the reader (the watcher in `main.py`) use `MARCEL_ENV`.
- [ ] Remove the `os.execv` branch in `src/marcel_core/main.py` (`_restart_watcher`) and update the surrounding docstring to reflect the unified path.
- [ ] `grep -rn "os.execv" src/ tests/` returns no matches after the change.
- [ ] Teach `redeploy.sh` the `--env dev|prod` flag (default `prod`), select the right compose file, rebuild + recreate the matching container.
- [ ] Add systemd units `marcel-dev-redeploy.path` + `marcel-dev-redeploy.service` (template-based, env substitution on top of the existing prod templates).
- [ ] Update `scripts/setup.sh` to install both the prod and dev unit pairs.
- [ ] Rewrite the `make serve` target to drive `docker-compose.dev.yml` (`docker compose -f docker-compose.dev.yml up --build` or equivalent). Verify `make serve-logs` and `make serve-down` still work against the new container name.
- [ ] Update `.claude/rules/self-modification.md` — strip the dev-mode exception; one restart path, dev and prod differ only in flag suffix + compose file.
- [ ] Update `docs/self-modification.md`, `CLAUDE.md`, and any port table / "make serve" reference to the new two-port, one-mechanism shape.
- [ ] Regression test: `request_restart(sha)` writes to `restart_requested.dev` when `MARCEL_ENV=dev` and `restart_requested.prod` (or the current flag name) when `MARCEL_ENV=prod`.
- [ ] Manual verification: dev container healthy on :7421 with `--reload`; `request_restart` from inside the dev container triggers `redeploy.sh --env dev` and the container comes back healthy; prod restart path unchanged.
- [ ] `make check` green.
- [ ] Switch the operator's own dev loop to the new mechanism (last — after verification).

## Relationships

- Motivated by: ISSUE-efbaaa (discover() ordering dev-vs-prod divergence).
- Supersedes the "rogue host uvicorn" failure mode discussed in the 29-hour outage postmortem.
- Does **not** depend on ISSUE-63a946 (marcel-zoo repo extraction) — they touch different surfaces.

## Implementation Log

### 2026-04-21 — Closed as duplicate

**Action:** Closed without implementation. Discovery happened during the first audit of the target surfaces (config, flags, main.py, Dockerfile), which surfaced existing env-aware code and an `ISSUE-6b02d0` reference in the `marcel_env` docstring. A grep of `project/issues/closed/` for "unified restart" + "7421" then located the duplicate.

**Files Modified:** only this issue file (status + Closed-as-duplicate section + log entry). No source code, no tests, no docs.

**Commands Run:** none against the code tree. The issue-creation `📝` commit remains on main as the audit marker that the duplicate was filed.

**Result:** Duplicate documented; branch merged back; audit trail preserved.

**Reflection** (no verifier — trivial close with zero source-diff):
- Verdict: n/a. The pre-close-verifier is meant to hunt for shortcuts and scope drift in an implementation diff. This branch has no implementation diff (the closing commit touches only the issue file, same as any other close). Running the verifier on an empty body would return no findings by construction — noted here instead of delegated.
- Coverage: 0/14 tasks in this issue addressed (all pre-discharged by ISSUE-6b02d0).
- Shortcuts found: none.
- Scope drift: none.
- Stragglers: none.

## Lessons Learned

### What worked well
- **Stopping at the audit step instead of plowing into implementation.** The 14-task list looked real, but the first three `Read`s revealed the work was already done. That's the value of the "read the current shape of the code I'm changing" habit — it catches duplicates before they become duplicate *commits*.

### What to do differently
- **Grep `project/issues/closed/` for motivating keywords before calling `/new-issue`.** The `/new-issue` skill's step 1 ("ensure clean main") does not include a duplicate check, and this is the exact pathology that produces it: a long verbatim request, pasted from a conversation where the earlier issue was already closed, gets treated as novel. The duplicate-detection that should have fired was `grep -rn "7421\|os.execv\|unified restart" project/issues/closed/`. Consider promoting that grep into the `/new-issue` skill itself so future pasted requests are checked automatically.
- **When the request body contains concrete symbols** (`src/marcel_core/main.py:83`, `MARCEL_DEV_PORT`, `marcel-dev-redeploy.path`), run a codebase grep for those symbols as part of `/new-issue` before committing the issue file. Any hit in the closed/ tree is a near-certain duplicate.

### Patterns to reuse
- **"Closed-as-duplicate" with explanatory table.** When closing a duplicate, leave a `Closed — duplicate of ISSUE-xxxxxx` section at the top of the file with a verification table proving each claimed deliverable already exists. Keeps the audit trail self-documenting — a future reader does not need to compare the two issue files side-by-side to understand what happened.
