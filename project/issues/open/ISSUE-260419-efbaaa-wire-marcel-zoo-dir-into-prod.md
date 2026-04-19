# ISSUE-efbaaa: Wire MARCEL_ZOO_DIR into prod Docker container

**Status:** Open
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

After ISSUE-13c7f2 (banking→zoo migration, merged today), prod Marcel has `Schedule rebuilt: 1 jobs scheduled` on startup — the hardcoded `Good morning` — because `MARCEL_ZOO_DIR` is unset in the container environment. `docker inspect marcel` confirms no `MARCEL_ZOO_DIR` in `Config.Env`. The zoo checkout (`/home/shbunder/projects/marcel-zoo`) is *already accessible* inside the container via the `/home/shbunder` bind mount declared in `docker-compose.yml:23`; the missing piece is the env var telling `discover()` where to look.

This was masked for two prior migrations because banking was still in-kernel. The first-time user-visible signal was "banking stopped working in prod this morning" after today's merge.

The fix is small and structural — compose-level config, not code. Adjacent entries (`MARCEL_PORT`, `MARCEL_DATA_DIR`) are already set the same way, so the pattern exists.

## Tasks

- [ ] Add `MARCEL_ZOO_DIR=/home/shbunder/projects/marcel-zoo` to `docker-compose.yml` `environment:` block, adjacent to the existing `MARCEL_PORT` and `MARCEL_DATA_DIR` entries
- [ ] `docker compose up -d --force-recreate marcel` (or equivalent) to apply the change without bouncing via `request_restart()` — this is a compose-level config change, not a code change
- [ ] Verify via `docker inspect marcel --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ZOO` that the env var is set on the running container
- [ ] Verify via `docker logs marcel 2>&1 | grep "Schedule rebuilt"` that N ≥ 3 jobs are scheduled (banking sync + news sync + good morning + icloud if any)
- [ ] Verify via `docker logs marcel 2>&1 | grep "Removing orphan habitat job"` that NO orphan-cleanup entries appear on startup — the habitats should be discovered, not removed
- [ ] Verify a banking handler actually executes in prod (either wait for the cron-scheduled `Bank sync` at the next 8-hour boundary, or invoke via Telegram)
- [ ] Implementation Log entry documenting why this slipped for two migrations (banking-in-kernel masking), and note the lesson for future migrations

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
