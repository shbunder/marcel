# ISSUE-caf8de: Job storage — flat layout + SKILL.md-style JOB.md

**Status:** WIP
**Created:** 2026-04-15
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, jobs, storage

## Capture
**Original request:** I want to revisit the job-system: 1) put all jobs under .marcel directly, add a field users to indicate for which users it should run 2) the file should look more like the SKILL.md (have a yaml header) and markdown body with the system prompt and task

**Follow-up Q&A:**
- Q: Split mutable runtime state into a separate `state.json`, or keep everything in JOB.md? → **split** — JOB.md is user-authored, state.json is scheduler-managed.
- Q: Per-user run logs (`runs/<user_slug>.jsonl`) or a single run log with `user_slug` field? → **per-user**, with an explicit need for user-agnostic jobs (e.g. `news.sync` benefits everyone without a login) that log to `runs/_system.jsonl`.
- Q: Directory naming — hex ID or slugified name? → **slug**, mirroring skills (`~/.marcel/jobs/news-sync/`).
- Q: Default bank-sync job — auto-collapse into one job with `users: [...]`? → **no**, population stays manual; keep per-user default jobs.

**Resolved intent:** Move Marcel's jobs from a per-user on-disk layout (`~/.marcel/users/<slug>/jobs/<hex>/job.json`) to a flat, SKILL-style layout (`~/.marcel/jobs/<slug>/JOB.md` with YAML frontmatter + markdown body). Jobs become first-class, human-editable documents and can target one, many, or zero users via a `users:` field. Zero users means a system-scope job — one that runs without a user login and whose output (e.g. scraped news) is shared. Mutable runtime state splits into `state.json` so the scheduler's bookkeeping never clobbers hand-authored prompts. Run logs go per-user for clean filtering, with `_system.jsonl` for system-scope jobs. A one-shot migration converts existing jobs on startup.

## Description

### Why this change

The current shape has three problems:
1. **Jobs are buried per-user** even when the work isn't per-user (news scraping benefits everyone). A job that should be shared has to be duplicated.
2. **`job.json` is not human-editable.** Compared to SKILL.md, the JSON blob is painful to hand-write or hand-tune — especially the multi-line `system_prompt` which is the most-edited field.
3. **Runtime state is mixed with user-authored config.** Every job run bumps `consecutive_errors` / `last_error_at` / `updated_at` and rewrites the whole file, risking damage to hand-formatted prompts.

The target layout unifies jobs with the skill pattern the user already understands.

### New on-disk layout

```
~/.marcel/jobs/<slug>/
├── JOB.md                ← YAML frontmatter + "## System Prompt" / "## Task" body
├── state.json            ← mutable runtime state (errors, timestamps, last_alert_at)
└── runs/
    ├── shaun.jsonl       ← per-user run log
    ├── alice.jsonl
    └── _system.jsonl     ← used when job.users is empty (system-scope)
```

### JOB.md format

```markdown
---
id: 341e749bde4b
name: News sync
description: Scrape VRT NWS and De Tijd for latest articles at 6am and 6pm
users: []                 # empty = system-scope; or [shaun], [shaun, alice]
status: active
model: anthropic:claude-haiku-4-5-20251001
skills: [news]
trigger:
  type: cron
  cron: "0 6,18 * * *"
  timezone: null
notify: silent
channel: telegram
timeout_seconds: 600
request_limit: 60
max_retries: 2
retry_delay_seconds: 60
backoff_schedule: [30, 60, 300, 900, 3600]
retention_days: 30
template: scrape
allow_local_fallback: false
allow_fallback_chain: true
alert_after_consecutive_failures: 3
alert_cooldown_seconds: 3600
---

## System Prompt

You are a news scraper for Marcel. Fetch the latest headlines from Belgian news sources.
...

## Task

Fetch latest news from VRT NWS, De Tijd, Knack, Trends, Datanews, De Morgen, and HLN via RSS feeds. Filter duplicates, store new articles with integration(id="news.store").
```

### Schema changes (`src/marcel_core/jobs/models.py`)

- `JobDefinition.user_slug: str` → `users: list[str]` (empty list = system-scope)
- New `JobState` model holding `consecutive_errors`, `schedule_errors`, `last_error_at`, `last_failure_alert_at`, `updated_at`, persisted in `state.json`
- `JobRun` — no `user_slug` field needed; the parent path (`runs/<user>.jsonl` or `runs/_system.jsonl`) already encodes it

### Dispatch semantics

- **`users: [shaun, alice]`** — cron fires once per user per scheduled tick. Each dispatch loads that user's credentials, memories, skills, and notification target. Run record filed at `runs/<user>.jsonl`.
- **`users: []`** — cron fires once per tick. Executor runs with `user_slug=None`:
  - Skills loaded *without* user-scoped requirement checks (only global requirements — env vars, packages — are enforced)
  - No memory injection
  - No per-user credentials; only global / env-based creds
  - No auto-notification (system jobs are silent unless a system channel is configured)
  - Run filed at `runs/_system.jsonl`
- **`users: [shaun]`** — behaves exactly like the current per-user model.

### Migration (one-shot, runs at startup)

Walk `~/.marcel/users/*/jobs/*/job.json`. For each legacy job:
1. Derive `slug` from the job's `name` field (kebab-case, collision-checked against existing `~/.marcel/jobs/<slug>/`)
2. Create `~/.marcel/jobs/<slug>/JOB.md` with the old fields as frontmatter, `users: [<old_user_slug>]`, and the body split into `## System Prompt` / `## Task` sections
3. Create `state.json` from the mutable fields
4. Move `runs.jsonl` → `runs/<old_user_slug>.jsonl`
5. Delete the old directory after successful conversion

The migration runs once — after first successful pass, the legacy `~/.marcel/users/<slug>/jobs/` directories are empty or removed, so subsequent boots short-circuit. No backup by default; git + the old directory tombstone are the recovery path.

### Files touched

- [src/marcel_core/jobs/__init__.py](../../../src/marcel_core/jobs/__init__.py) — CRUD rewritten for flat layout, JOB.md serializer, per-user run file helpers
- [src/marcel_core/jobs/models.py](../../../src/marcel_core/jobs/models.py) — `users` field, `JobState` split
- [src/marcel_core/jobs/executor.py](../../../src/marcel_core/jobs/executor.py) — accept `user_slug: str | None` param, handle system-scope path
- [src/marcel_core/jobs/scheduler.py](../../../src/marcel_core/jobs/scheduler.py) — per-user dispatch loop, default-job helper uses `users=[slug]`, event handling
- [src/marcel_core/jobs/tool.py](../../../src/marcel_core/jobs/tool.py) — agent tool signatures (create/list/get/update/delete), permission check on `ctx.deps.user_slug in job.users`
- [src/marcel_core/jobs/cache.py](../../../src/marcel_core/jobs/cache.py) — keep per-user cache scoping for now (out of scope for this issue unless trivial)
- [tests/jobs/](../../../tests/jobs/) — update all CRUD, scheduler, executor, tool tests; add frontmatter round-trip, multi-user dispatch, system-scope, and migration tests
- [src/marcel_core/main.py](../../../src/marcel_core/main.py) — call migration on startup (idempotent)
- [docs/](../../../docs/) — update any jobs-related docs (jobs.md if exists, local-llm.md references)

### Out of scope

- Changing the job cache layout (`~/.marcel/users/<slug>/job_cache/` stays per-user for now).
- Redesigning integrations or skills to handle `user_slug=None` explicitly — this issue uses the existing skills as-is and simply filters requirement checks at load time for system-scope.
- Auto-collapsing the default bank-sync job into a shared one.

## Tasks
- [✓] Extend `JobDefinition` with `users: list[str]`, remove `user_slug`; split mutable runtime state into ``state.json`` via the CRUD layer (chose field-partitioning over a separate `JobState` model — simpler, no in-memory split)
- [✓] Rewrite `save_job` / `load_job` to read/write `JOB.md` (YAML frontmatter + `## System Prompt` / `## Task` body) and `state.json`
- [✓] Rewrite `_jobs_root()` / `_job_dir_for_id()` to the flat `<data_root>/jobs/<slug>/` layout; derive slug from job name with collision handling
- [✓] Rewrite `list_jobs(user_slug)` to filter by membership in `job.users`; keep `list_all_jobs()` returning every job; add `list_system_jobs()` for `users: []`
- [✓] Move run log helpers to `runs/<user_slug>.jsonl` and `runs/_system.jsonl` for system-scope
- [✓] Update `execute_job` / `_build_job_context` to accept `user_slug: str | None` and handle the system-scope path (no per-user creds, no memories, no auto-notify); reserved slug is `_system` via `SYSTEM_USER` constant
- [✓] Update scheduler `_dispatch` to loop over `job.users` (or run once with `_system` for system-scope) and update `_handle_event` + `_ensure_default_jobs` accordingly
- [✓] Update `tool.py` agent tools — `create_job` defaults `users=[ctx.deps.user_slug]`, `list_jobs` shows user + system-scope, `get_job` / `update_job` / `delete_job` check permission
- [✓] Write one-shot migration: walk `<data_root>/users/*/jobs/`, convert each job to the new layout, move runs, remove legacy directory
- [✓] Wire migration into startup (called from `scheduler.rebuild_schedule`, idempotent — short-circuits when no legacy dirs)
- [✓] Update all tests under `tests/jobs/` for the new layout and API; add tests for frontmatter round-trip, multi-user dispatch, system-scope execution, and migration
- [✓] Update docs referencing the old layout
- [✓] `make check` green before close

## Relationships
_None — this refactor is self-contained. Touches job system only; does not depend on or block other open issues._

## Comments
_None yet._

## Implementation Log

### 2026-04-15 - LLM Implementation
**Action**: Refactored job storage to a flat SKILL.md-style layout.

**Files Modified**:
- `src/marcel_core/jobs/models.py` — replaced `user_slug: str` with `users: list[str]`; documented state-split approach in the docstring.
- `src/marcel_core/jobs/__init__.py` — full rewrite. New flat layout helpers (`_jobs_root`, `_resolve_slug`, `_find_job_dir_by_id`), frontmatter/body parser, JOB.md/state.json serializer, per-user run log helpers, `list_all_jobs` / `list_jobs(user_slug)` / `list_system_jobs()`, and `migrate_legacy_jobs()` one-shot migration. Exposes `SYSTEM_USER = '_system'` sentinel.
- `src/marcel_core/jobs/executor.py` — new `_resolve_run_user` helper; `execute_job`, `_run_with_backoff`, `_execute_chain`, `_execute_pinned_with_legacy_fallback`, `execute_job_with_retries`, `_notify_if_needed` all accept `user_slug: str | None` as keyword arg. System-scope runs skip memory/credential injection and never notify. `append_run` calls updated to new `(job_id, user_slug, run)` signature.
- `src/marcel_core/jobs/scheduler.py` — `_dispatch` loops over `job.users or [SYSTEM_USER]`; new `_latest_run_across_users` helper feeds `schedule_job`; `_handle_event` now walks `list_all_jobs()` and dispatches each event-triggered job once (event iterates its own users internally); `_ensure_default_jobs` uses `users=[slug]`; `rebuild_schedule` calls `migrate_legacy_jobs()` first; `_resolve_stuck_runs` walks per-user run files; `cleanup_old_runs` signature updated.
- `src/marcel_core/jobs/tool.py` — `create_job` takes optional `users: list[str]` (default `[ctx.deps.user_slug]`, pass `[]` for system-scope); all tool operations check membership in `job.users` (system-scope jobs are visible to everyone).
- `scripts/seed_jobs.py` — swapped `user_slug=...` for `users=[...]`.
- `tests/jobs/*.py` — updated every test for the new API: `user_slug=...` → `users=[...]`, `append_run`/`read_runs` argument reordering, `load_job`/`delete_job` take only `job_id`, mock helpers accept the new `user_slug` kwarg. Added round-trip, multi-user, system-scope, and full migration coverage in `test_jobs_crud.py`.
- `docs/jobs.md` — rewrote the storage section, documented the JOB.md format, users field semantics, system-scope behavior, and the legacy migration.
- `docs/local-llm.md`, `docs/model-tiers.md` — updated grep examples to the new `~/.marcel/jobs/<slug>/runs/*.jsonl` path.

**Design decisions**:
- **State split is field-partitioning, not a separate model.** The CRUD layer dumps `JobDefinition` once via `model_dump_json()`, then partitions fields between JOB.md frontmatter (user-authored, stable) and state.json (mutable runtime). Simpler than maintaining two pydantic models that have to re-merge on every read.
- **System-scope sentinel.** `users: []` dispatches with the reserved slug `_system` instead of `None`. This avoids rippling `str | None` through `MarcelDeps.user_slug` (which is required by many downstream tools) — `~/.marcel/users/_system/` is never created, so credential and memory lookups naturally return empty.
- **Slugs are fixed at creation.** `save_job` looks up the existing directory by `id` first, so renaming a job does not move its directory. New jobs get a kebab-case slug derived from `name`, deduplicated against existing directories (`digest`, `digest-2`, ...). Matches the skills directory pattern.
- **Per-user run logs over a single log with a user field.** Cleaner reads (`read_runs(job_id, 'alice')` is an O(one file) scan), simpler filtering, trivial system-scope support (`runs/_system.jsonl`). The extra files cost nothing.
- **Event dispatch ignores the triggering user slug.** A completed job may run under any user (or system-scope). Event-triggered dependents fire once with their own `users` list; the triggering slug is purely informational in the event payload. Preserves test-facing `emit_event(user_slug, job_id, status)` signature for backwards compat.

**Commands Run**: `make check` — all green (lint, typecheck, 1357 tests passing, 92.25% coverage).

**End-to-end verification**: Ran `migrate_legacy_jobs()` against the live `~/.marcel/` data root; all 4 shaun jobs (plus 8 legacy copies in the `.backup-059-*` user directories) converged into 4 flat `~/.marcel/jobs/<slug>/` directories as expected, with `JOB.md`, `state.json`, and `runs/shaun.jsonl` all populated correctly. Legacy `users/*/jobs/` directories were removed.

**Result**: Success — job system now flat, human-editable, and supports system-scope jobs. Ready to close.

**Next**: `/finish-issue` to close and merge.
