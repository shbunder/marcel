# ISSUE-048: Background Job System

**Status:** WIP
**Created:** 2026-04-10
**Assignee:** Claude
**Priority:** High
**Labels:** feature

## Capture
**Original request:** "I want to create a job system in Marcel where Marcel can run jobs in the background for a user. The user should also be able to create new jobs by talking to Marcel: 1) jobs should act as much as possible like isolated apps, I want to prevent job logic being scattered around the system and code-base 2) user should be able to create jobs by talking to marcel. The job system should be templated as much as possible"

**Examples provided:**
- a) Run a job to sync bank transactions every 8 hours
- b) Run a job after every bank sync to check if balance < 100 EUR, one-time warning via preferred channel
- c) Sync new articles by scraping VRT NWS and deTijd (deTijd needs login/pwd)
- d) Every morning at 7h send a news digest via preferred channel
- e) Every morning at 7h send a calendar digest (combine with d as single "good morning" message)

**Resolved intent:** Build a general-purpose background job system where jobs are self-contained units (definition + state in their own directory), run on schedules or triggers, and execute as headless agent turns — giving them AI reasoning and access to all Marcel skills. Users create jobs conversationally, aided by built-in templates for common patterns (sync, check, scrape, digest).

## Description
Marcel needs a job scheduler that runs alongside the existing chat handling. Jobs are defined as Pydantic models stored per-user, executed by spinning up a headless pydantic-ai agent turn with the job's own system prompt and task. The scheduler manages cron/interval/event triggers and an event bus for job chaining.

## Tasks
- [✓] ISSUE-048-a: Data models (JobDefinition, JobRun, TriggerSpec)
- [✓] ISSUE-048-b: CRUD + storage operations
- [✓] ISSUE-048-c: Headless agent executor
- [✓] ISSUE-048-d: Scheduler (tick loop + event bus)
- [✓] ISSUE-048-e: Built-in templates (sync, check, scrape, digest)
- [✓] ISSUE-048-f: Agent tools for conversational job management
- [✓] ISSUE-048-g: Wire up (main.py, agent.py, context.py)
- [✓] ISSUE-048-h: Skill document (.marcel/skills/jobs/SKILL.md)
- [✓] ISSUE-048-i: Documentation
- [✓] ISSUE-048-j: Add croniter dependency

## Implementation Log
### 2026-04-10 — LLM Implementation
**Action:** Implemented complete background job system
**Files Created:**
- `src/marcel_core/jobs/__init__.py` — CRUD operations + file-based storage
- `src/marcel_core/jobs/models.py` — Pydantic models (JobDefinition, JobRun, TriggerSpec)
- `src/marcel_core/jobs/executor.py` — Headless agent turn execution with retries + notifications
- `src/marcel_core/jobs/scheduler.py` — Async tick loop (30s), event bus for job chaining, croniter integration
- `src/marcel_core/jobs/templates.py` — Built-in templates (sync, check, scrape, digest)
- `src/marcel_core/jobs/tool.py` — Agent tools (create_job, list_jobs, get_job, update_job, delete_job, run_job_now, job_templates)
- `.marcel/skills/jobs/SKILL.md` — Skill document teaching Marcel how to create/manage jobs
- `docs/jobs.md` — Developer documentation
**Files Modified:**
- `src/marcel_core/main.py` — Wire scheduler start/stop into lifespan
- `src/marcel_core/harness/agent.py` — Register job tools on the agent
- `src/marcel_core/harness/context.py` — Add 'job' channel format hint
- `pyproject.toml` — Add croniter>=2.0.0 dependency
- `mkdocs.yml` — Register docs/jobs.md in nav
**Commands Run:** `uv run pyright`, `uv run ruff check`, `uv run ruff format`
**Result:** All type checks and lint pass
