# ISSUE-efbaaa: Wire MARCEL_ZOO_DIR into prod Docker container

**Status:** Closed
**Created:** 2026-04-19
**Assignee:** Unassigned
**Priority:** High
**Labels:** bug, ops

## Capture
**Original request:** Wire MARCEL_ZOO_DIR into the prod Docker container so zoo habitats actually load in production. Discovered after the banking→zoo migration (ISSUE-13c7f2) shipped: the prod container env has no MARCEL_ZOO_DIR set (confirmed via `docker inspect marcel --format '{{.Config.Env}}'`), so `_marcel_ext_integrations` discovery returns zero habitats. This has been silently broken since icloud (ISSUE-e7d127) — icloud's calendar handlers stopped working in prod then, news handlers stopped in prod when news migrated (ISSUE-d5f8ab), and banking handlers stopped now that banking migrated. Banking was masking the regression because it was still in-kernel. Confirmed by post-restart log line `Schedule rebuilt: 1 jobs scheduled` (only `Good morning` — the hardcoded one) and `Removing orphan habitat job 6af525725b45 (News sync) — habitat habitat:news no longer declares it`.

Fix: add `MARCEL_ZOO_DIR=/home/shbunder/projects/marcel-zoo` to `docker-compose.yml` under the existing `environment:` block (the `/home/shbunder` bind mount already exposes the path to the container — verified via `docker exec marcel ls /home/shbunder/projects/marcel-zoo/integrations/` which returns `banking docker icloud news`). After the compose change and container recreate, verify: (1) `docker inspect marcel --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ZOO` shows the var; (2) container logs show `Schedule rebuilt: N jobs scheduled` with N ≥ 3 (banking sync + news sync + good morning + whatever icloud ships); (3) `docker logs marcel 2>&1 | grep "habitat:"` shows no orphan-cleanup entries on startup; (4) a banking handler call via the chat or API succeeds.

Do NOT put this in `.env` or `.env.local` — compose already loads those but they are per-deployment secret files. The zoo path is deployment-structural config (like `MARCEL_PORT` and `MARCEL_DATA_DIR` already in the `environment:` block), so it belongs there. Keeping it next to those makes the compose file the single source of truth for "where does the zoo live".

Document in the Implementation Log: the reason this went unnoticed for two migrations is that banking was still in-kernel and therefore prod had working banking + non-working icloud/news without a loud signal. Lesson for future kernel→zoo migrations: verify the prod container can discover the zoo *as part of the migration*, not afterwards. Add a step to the habitat-migration runbook (if one exists, otherwise note it in the ISSUE-2ccc10 umbrella lessons): confirm `MARCEL_ZOO_DIR` is wired in the target environment before declaring the migration done.

Scope: only `docker-compose.yml`. No code changes. No docs (aside from the issue file itself). The habitat loader in `marcel_core/skills/plugins.py` (or wherever discover() lives) already reads `os.environ['MARCEL_ZOO_DIR']` correctly — it just gets an empty string/None in prod today.

**Follow-up Q&A:** None — user authorised the restart (`docker restart marcel`) and the rogue-process kill (`kill 2068790`) that preceded this discovery.

**Resolved intent:** Prod Docker has been silently running without any zoo-habitat integrations since the first habitat migration (icloud, ISSUE-e7d127). The bug was latent because banking stayed in-kernel and kept serving requests; now that banking has migrated too (ISSUE-13c7f2, merged 2026-04-18 / today), prod has zero integration handlers — no banking, news, or icloud. The fix is a one-line addition to `docker-compose.yml` pointing `MARCEL_ZOO_DIR` at the zoo checkout, which is already mounted into the container via the existing `/home/shbunder` bind volume. After recreate, the orphan-cleanup log line should stop and the 4 scheduled jobs (banking sync + news sync + good morning + icloud if any) should re-register.

## Description

After ISSUE-13c7f2 (banking→zoo migration, merged today), prod Marcel has `Schedule rebuilt: 1 jobs scheduled` on startup — the hardcoded `Good morning` — because (a) `MARCEL_ZOO_DIR` was unset in the container environment and (b) even after fixing (a), `discover()` is not called in the kernel lifespan before `scheduler.rebuild_schedule()` → `_ensure_habitat_jobs()`, so `_metadata` is empty and every habitat-sourced job is treated as an orphan and deleted on startup.

### Bug 1 — `MARCEL_ZOO_DIR` missing from compose env

`docker inspect marcel` confirmed no `MARCEL_ZOO_DIR` in `Config.Env`. The zoo checkout is *already accessible* inside the container via the `/home/shbunder` bind mount declared in `docker-compose.yml:23`; the missing piece was the env var telling `discover()` where to look.

### Bug 2 — lifespan startup order: `rebuild_schedule()` runs before `discover()`

Empirically confirmed in the running container (with `MARCEL_ZOO_DIR` wired and zoo symlink in place):

```
before discover: _metadata = {}
after discover: _metadata keys = ['banking', 'docker', 'icloud', 'news']
  banking: 1 scheduled_jobs
  news: 1 scheduled_jobs
```

Nothing in the kernel lifespan or in routers' module-level imports triggers `discover()` before `scheduler.start()` calls `rebuild_schedule()` → `_ensure_habitat_jobs()`. Result: `_metadata` is empty, every `habitat:*` job on disk is declared orphan and deleted, no new habitat jobs are materialized. On first chat request, `registry._load_registry()` fires `discover()` and populates `_metadata`, but by then the scheduler has already committed its "zero habitat jobs" view.

This bug was latent since ISSUE-82f52b (scheduled_jobs feature) and/or ISSUE-e7d127 (first kernel→zoo migration), masked because banking was still in-kernel and `_ensure_default_jobs` (now deleted in ISSUE-13c7f2) created the `Bank sync` job unconditionally.

This was masked for two prior migrations because banking was still in-kernel. The first-time user-visible signal was "banking stopped working in prod this morning" after today's merge.

Fix: (1) compose-level config for the env var; (2) call `discover()` explicitly in the kernel lifespan before `scheduler.start()` — smallest-possible change, one import + one call. Both fixes ship in this issue because either alone leaves prod broken.

## Tasks

- [✓] Add `MARCEL_ZOO_DIR=${MARCEL_ZOO_DIR:-${HOME}/.marcel/zoo}` to `docker-compose.yml` `environment:` block, adjacent to `MARCEL_PORT` and `MARCEL_DATA_DIR` — matches the existing `${HOME}/.marcel`-style pattern, defaults to `~/.marcel/zoo`, overridable per-deployment via shell env or `.env.local`
- [✓] Create `~/.marcel/zoo` symlink → `~/projects/marcel-zoo` on this host so the default path resolves to the real checkout (`ln -s ~/projects/marcel-zoo ~/.marcel/zoo`) — the `/home/shbunder` bind mount already makes the target reachable inside the container
- [✓] Call `discover()` in [src/marcel_core/main.py](src/marcel_core/main.py) `lifespan()` before `scheduler.start()`, so `_metadata` is populated when `rebuild_schedule()` runs and habitat jobs are materialized instead of orphan-deleted
- [✓] Add a regression test that fails if `discover()` is not called before `scheduler.start()` in the lifespan (or an equivalent test that verifies habitat `scheduled_jobs` reach the scheduler on cold startup)
- [✓] `docker compose up -d --force-recreate marcel` to apply the compose change without bouncing via `request_restart()` — this is a compose-level config change plus a kernel fix, not a user-data change
- [✓] Verify via `docker inspect marcel --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ZOO` that the env var is set on the running container
- [✓] Verify via `docker logs marcel 2>&1 | grep "Schedule rebuilt"` that N ≥ 3 jobs are scheduled (banking sync + news sync + good morning + icloud if any) — observed `Schedule rebuilt: 3 jobs scheduled (0 overdue catchup)` with Bank sync + News sync + Good morning
- [✓] Verify via `docker logs marcel 2>&1 | grep "Removing orphan habitat job"` that NO orphan-cleanup entries appear on startup — the habitats should be discovered, not removed
- [⚒] Verify a banking handler actually executes in prod (either wait for the cron-scheduled `Bank sync` at the next 8-hour boundary, or invoke via Telegram) — cron-scheduled run at 2026-04-20T00:00:18+00:00 UTC will confirm; scheduler pickup + env var + discover ordering all verified, so end-to-end is high-confidence but not yet observed
- [✓] `make check` green at ≥90% coverage — 1509 passed, 91.95% coverage
- [✓] Implementation Log entry documenting both bugs (env var missing + discover-order) and why this slipped for two migrations (banking-in-kernel masking), and note the lesson for future migrations

## Relationships

- Caused by: ISSUE-e7d127 (icloud migration — first habitat to leave the kernel, regression introduced here and silently masked)
- Caused by: ISSUE-d5f8ab (news migration — second habitat, still masked)
- Unmasked by: ISSUE-13c7f2 (banking migration — third and final, regression became visible)
- Informs: future habitat migration runbook (add "verify MARCEL_ZOO_DIR wired in target environment" as a pre-close step)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

### 2026-04-19 19:05 - LLM Implementation
**Action**: Wired `MARCEL_ZOO_DIR` into the prod container and fixed the kernel startup order so habitat scheduled jobs are no longer orphan-deleted at cold start.
**Files Modified**:
- `docker-compose.yml` — added `MARCEL_ZOO_DIR=${MARCEL_ZOO_DIR:-${HOME}/.marcel/zoo}` under `environment:`.
- `src/marcel_core/main.py` — `lifespan()` now imports and calls `discover()` from `marcel_core.skills.integrations` immediately after `seed_defaults()` and before `scheduler.start()`, with a comment explaining the contract with `_ensure_habitat_jobs()`.
- `tests/core/test_main_lifespan.py` (new) — regression test that mocks `seed_defaults`, `discover`, and `scheduler`, runs the real `lifespan()`, and asserts `call_order.index('discover') < call_order.index('scheduler.start')`. Verified it fails (`ValueError: 'discover' is not in list`) when the fix is reverted.
**Host-side setup**: `ln -s ~/projects/marcel-zoo ~/.marcel/zoo` (one-time, per-deployment — kept out of compose so the file stays deployment-agnostic).
**Commands Run**:
- `make check` → 1509 passed, 91.95% coverage (above 90% gate)
- `docker compose up -d --force-recreate marcel` → container recreated cleanly
- `docker inspect marcel ... | grep ZOO` → `MARCEL_ZOO_DIR=/home/shbunder/.marcel/zoo` ✓
- `docker logs marcel | grep "Schedule rebuilt"` → `Schedule rebuilt: 3 jobs scheduled (0 overdue catchup)` ✓
- `docker logs marcel | grep orphan` → no output on this startup ✓
**Result**: Prod is up on :7420 with Bank sync (id `0dea8f65d244`, cron `0 */8 * * *`), News sync (id `6af525725b45`), and Good morning (id `c1f96e7741ac`) all registered. End-to-end banking handler execution will be observed at the next cron fire (2026-04-20T00:00:18+00:00).

**Why this slipped for two prior migrations** (documented here for the next habitat work):

- icloud (ISSUE-e7d127) and news (ISSUE-d5f8ab) both moved code out of the kernel. Neither verified the prod container could *discover* the zoo — the regression was silent because prod's only surviving visible integration was banking (still in-kernel), and banking's jobs were created unconditionally by `_ensure_default_jobs()` in the scheduler, independent of habitat metadata.
- The discover-order bug (bug #2) has existed since ISSUE-82f52b (the `scheduled_jobs:` feature). Before the kernel-to-zoo migrations, habitat discovery was effectively dead code in prod — nothing hit it — so the ordering didn't matter.
- Banking's migration (ISSUE-13c7f2) deleted `_ensure_default_jobs()`, which was the last piece making integrations appear in prod without habitat discovery. That change unmasked both latent issues simultaneously.

**Lesson for future habitat migrations**: before declaring a migration done, recreate the prod container with the migration applied and confirm `Schedule rebuilt: N jobs scheduled` reflects the expected count (no orphan-deletion entries). The migration-of-the-moment's handlers will appear to work in dev (where `discover()` has likely been triggered by prior interactive use in the same process) while silently failing in prod's cold-start order.

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 11/11 tasks addressed (one `[⚒]` for post-cron handler observation is time-bound, not a blocker)
- Shortcuts found: none
- Scope drift: scope expanded from "docker-compose.yml only" to include `main.py` lifespan fix + regression test — justified and called out in Description (bug 1 alone would still leave prod at 0 habitat jobs)
- Stragglers: `docs/plugins.md` describes `MARCEL_ZOO_DIR` as "no default — opt-in"; still true at the code level (`config.py:56` unchanged), but the bundled `docker-compose.yml` now provides a deployment-level default. Informational only — no contract change.

## Lessons Learned

### What worked well
- **Probing the running container with `docker exec … python -c`** to inspect `_metadata` before and after a manual `discover()` call is what turned "jobs not scheduled" from a symptom into a root cause. The ordering bug was invisible from logs alone — `Schedule rebuilt: 1 jobs scheduled` looks like "config problem, not code problem" until you see the metadata dict is empty at scheduler-start time.
- **Shipping both fixes in one issue** instead of splitting into "env var" + "lifespan order" was correct: either alone still leaves prod broken. The verifier confirmed the scope expansion was justified rather than drift.
- **Regression test via `mock.patch` on the call path** (not on the actual integrations/scheduler) keeps the test fast, deterministic, and focused strictly on the lifespan contract — reverting the fix raises a clean `ValueError` with a helpful message.

### What to do differently
- **Habitat migrations need a prod smoke-step before close.** Three migrations (icloud, news, banking) shipped before this regression surfaced. The missing step: after merging a kernel→zoo migration, recreate the prod container and verify `Schedule rebuilt: N jobs scheduled` matches the expected habitat count with no `Removing orphan habitat job` entries. Add this to the umbrella (ISSUE-2ccc10) lessons and to any future migration issue's task list.
- **Env var defaults belong at the deployment layer, not the code layer.** Original instinct was to hardcode `/home/shbunder/projects/marcel-zoo` in compose. The better pattern — `${MARCEL_ZOO_DIR:-${HOME}/.marcel/zoo}` + a host-side symlink — keeps the compose file deployment-agnostic and mirrors how `MARCEL_DATA_DIR` already treats `~/.marcel`. The evolution from ISSUE-6ad5c7's "no default, opt-in" to "compose-level default" is not a contradiction: the code still has no default (so non-docker deployments must opt in explicitly), but the bundled docker deployment is a complete, working product.

### Patterns to reuse
- **"Why this slipped" block in the Implementation Log.** When a latent bug is unmasked by unrelated work, write a short timeline — when the bug became possible, why it stayed invisible, what triggered the reveal — directly in the issue. Future readers hit the same confusion ("wait, how did this work at all before?") and this turns the answer into a five-line read instead of a git-archaeology session.
- **Lifespan-ordering regression test via mocked `side_effect` → `call_order.append(...)`.** Generalises beyond this issue — any FastAPI lifespan with an ordering contract between startup hooks can be covered this way without spinning up the real subsystems.
