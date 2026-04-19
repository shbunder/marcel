# ISSUE-efbaaa: Wire MARCEL_ZOO_DIR into prod Docker container

**Status:** WIP
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

- [ ] Add `MARCEL_ZOO_DIR=${MARCEL_ZOO_DIR:-${HOME}/.marcel/zoo}` to `docker-compose.yml` `environment:` block, adjacent to `MARCEL_PORT` and `MARCEL_DATA_DIR` — matches the existing `${HOME}/.marcel`-style pattern, defaults to `~/.marcel/zoo`, overridable per-deployment via shell env or `.env.local`
- [ ] Create `~/.marcel/zoo` symlink → `~/projects/marcel-zoo` on this host so the default path resolves to the real checkout (`ln -s ~/projects/marcel-zoo ~/.marcel/zoo`) — the `/home/shbunder` bind mount already makes the target reachable inside the container
- [ ] Call `discover()` in [src/marcel_core/main.py](src/marcel_core/main.py) `lifespan()` before `scheduler.start()`, so `_metadata` is populated when `rebuild_schedule()` runs and habitat jobs are materialized instead of orphan-deleted
- [ ] Add a regression test that fails if `discover()` is not called before `scheduler.start()` in the lifespan (or an equivalent test that verifies habitat `scheduled_jobs` reach the scheduler on cold startup)
- [ ] `docker compose up -d --force-recreate marcel` to apply the compose change without bouncing via `request_restart()` — this is a compose-level config change plus a kernel fix, not a user-data change
- [ ] Verify via `docker inspect marcel --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ZOO` that the env var is set on the running container
- [ ] Verify via `docker logs marcel 2>&1 | grep "Schedule rebuilt"` that N ≥ 3 jobs are scheduled (banking sync + news sync + good morning + icloud if any)
- [ ] Verify via `docker logs marcel 2>&1 | grep "Removing orphan habitat job"` that NO orphan-cleanup entries appear on startup — the habitats should be discovered, not removed
- [ ] Verify a banking handler actually executes in prod (either wait for the cron-scheduled `Bank sync` at the next 8-hour boundary, or invoke via Telegram)
- [ ] `make check` green at ≥90% coverage
- [ ] Implementation Log entry documenting both bugs (env var missing + discover-order) and why this slipped for two migrations (banking-in-kernel masking), and note the lesson for future migrations

## Relationships

- Caused by: ISSUE-e7d127 (icloud migration — first habitat to leave the kernel, regression introduced here and silently masked)
- Caused by: ISSUE-d5f8ab (news migration — second habitat, still masked)
- Unmasked by: ISSUE-13c7f2 (banking migration — third and final, regression became visible)
- Informs: future habitat migration runbook (add "verify MARCEL_ZOO_DIR wired in target environment" as a pre-close step)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
