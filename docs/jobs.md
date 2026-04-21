# Background Jobs

Marcel's job system lets users create background tasks that run automatically on schedules or in response to events. Jobs execute as headless agent turns — they get AI reasoning and access to all integration skills.

## Architecture

```
src/marcel_core/jobs/
    __init__.py       # CRUD + file storage
    models.py         # Pydantic data models
    executor.py       # Headless agent execution
    scheduler.py      # Tick loop + event bus
    templates.py      # Thin accessor over the habitat loader (no hardcoded content)
    tool.py           # Agent-facing tools
```

!!! info "Templates ship as zoo habitats"
    Since ISSUE-a7d69a the built-in templates (`sync`, `check`, `scrape`,
    `digest`) live at `<MARCEL_ZOO_DIR>/jobs/<name>/template.yaml` — not
    in the kernel. Discovery walks the zoo on every call via
    [`marcel_core.plugin.jobs.discover_templates`](plugins.md#job-habitat).
    The kernel ships **no** fallback: if `MARCEL_ZOO_DIR` is unset and
    the user has not authored local templates, the template list is
    empty and `job_templates` tells the user to configure the zoo.

Jobs live in a flat directory at `~/.marcel/jobs/{slug}/`, mirroring the skill layout:

```
~/.marcel/jobs/{slug}/
├── JOB.md                  # YAML frontmatter + "## System Prompt" / "## Task" body
├── state.json              # mutable runtime state (errors, timestamps)
└── runs/
    ├── {user_slug}.jsonl   # per-user run log
    └── _system.jsonl       # used for system-scope jobs (users: [])
```

`{slug}` is derived from `job.name` (kebab-case, collision-safe). Renaming a job does **not** move the directory — the slug is fixed at creation.

A job targets one or more users via the `users:` frontmatter field:

- `users: [alice]` — user-scoped (per-user credentials, memories, notifications)
- `users: [alice, bob]` — runs once per user each tick; each run gets its own credentials/memories
- `users: []` — **system-scope**: runs once per tick without a user context, useful for shared work like news scraping. No per-user credentials or memories are injected, and no automatic notifications are sent.

### JOB.md format

```markdown
---
id: 341e749bde4b
name: News sync
description: Scrape VRT NWS for latest articles at 6am and 6pm
users: []
status: active
trigger:
  type: cron
  cron: "0 6,18 * * *"
  timezone: null
model: anthropic:claude-haiku-4-5-20251001
skills: [news]
notify: silent
channel: telegram
timeout_seconds: 600
---

## System Prompt

You are a news scraper for Marcel. Fetch the latest headlines from Belgian news sources...

## Task

Fetch latest news from VRT NWS via RSS. Filter duplicates, store new articles.
```

`state.json` holds only the mutable runtime fields — `consecutive_errors`, `schedule_errors`, `last_error_at`, `last_failure_alert_at`, `updated_at` — so scheduler bookkeeping never clobbers hand-authored prompts.

### Legacy layout migration

The legacy layout (`~/.marcel/users/{slug}/jobs/{id}/job.json`) is migrated automatically on first startup. The migration:

1. Walks `~/.marcel/users/*/jobs/*/job.json`
2. Rewrites each as `~/.marcel/jobs/{slug}/JOB.md` + `state.json` with `users: [<old_user_slug>]`
3. Moves `runs.jsonl` → `runs/{old_user_slug}.jsonl`
4. Removes the legacy `~/.marcel/users/{slug}/jobs/` directory

It is idempotent — subsequent boots short-circuit at zero cost once the legacy directories are gone.

## Data models

### JobDefinition

The core model. Defines what a job does, who it targets, and when it runs.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Auto-generated 12-char hex ID |
| `name` | `str` | Human-readable name (used to derive the slug at creation) |
| `users` | `list[str]` | Target users. Empty list = system-scope |
| `trigger` | `TriggerSpec` | When/how the job fires |
| `system_prompt` | `str` | Instructions for the job agent (body of JOB.md) |
| `task` | `str` | The "user message" the agent receives (body of JOB.md) |
| `model` | `str` | Fully-qualified pydantic-ai model (default: `anthropic:claude-haiku-4-5-20251001`) |
| `notify` | `NotifyPolicy` | When to notify the user |
| `status` | `JobStatus` | `active`, `paused`, or `disabled` |
| `timeout_seconds` | `int` | Max seconds before the job is killed (default: 600) |
| `backoff_schedule` | `list[int]` | Retry delays in seconds (default: `[30, 60, 300, 900, 3600]`) |
| `retention_days` | `int` | Days to keep run records (default: 30) |
| `alert_after_consecutive_failures` | `int` | Failures before ON_FAILURE alert fires (default: 3) |
| `alert_cooldown_seconds` | `int` | Min seconds between failure alerts (default: 3600) |

### TriggerSpec

| Trigger type | Fields | Example |
|-------------|--------|---------|
| `cron` | `cron: str`, `timezone: str \| None` | `"0 7 * * *"` with `"Europe/Brussels"` (daily at 7 AM local time) |
| `interval` | `interval_seconds: int` | `28800` (every 8 hours) |
| `event` | `after_job: str`, `only_if_status: RunStatus` | Fires after another job completes |
| `oneshot` | `run_at: datetime` (optional) | Runs once, then disables |

**Timezone handling.** Cron triggers accept an optional `timezone` field as an [IANA timezone name](https://www.iana.org/time-zones) (e.g. `"Europe/Brussels"`, `"America/New_York"`). When set, the cron expression is interpreted in that timezone — so `"0 7 * * *"` with `timezone="Europe/Brussels"` fires at 7 AM Brussels time year-round, automatically adjusting for DST. When `timezone` is `None` or omitted, the cron expression is interpreted in UTC. Non-cron triggers ignore the field.

## Scheduler

The scheduler runs as an asyncio task in the FastAPI lifespan. It:

1. Loads all active jobs on startup, resolving orphaned RUNNING records from previous crashes
2. Computes `next_run_at` for each job using `croniter` (cron) or interval math, with deterministic per-job stagger offsets to avoid thundering herd
3. Ticks every 30 seconds, dispatching due jobs through a concurrency semaphore (max 3 parallel)
4. Sweeps for stuck jobs (running > 2 hours) and clears them automatically
5. Listens to an event bus for job-chaining (event triggers)
6. Persists scheduler state to `scheduler_state.json` for restart recovery
7. On startup, staggers overdue missed jobs (max 3, 30s apart) instead of firing all at once
8. Runs a daily cleanup loop, removing run records older than `retention_days`
9. Auto-disables jobs after 3 consecutive schedule computation errors

## Executor

The executor runs a job as a **headless agent turn**:

1. Picks the concrete run user — the sole entry in `job.users`, the explicitly-passed `user_slug` (for multi-user jobs), or the reserved `_system` slug for system-scope jobs
2. Creates `MarcelDeps` with `channel="job"` and `role="user"`
3. Builds a system prompt combining the job's own prompt with user profile context (skills, credentials, preference/feedback memories)
4. Creates a Marcel agent via `create_marcel_agent()`
5. Runs with timeout enforcement: `asyncio.wait_for(agent.run(...), timeout=job.timeout_seconds)`
6. On failure, classifies the error as transient (rate limit, network, timeout, 5xx) or permanent
7. Retries only transient errors with exponential backoff from `backoff_schedule`
8. Tracks `consecutive_errors` on the job definition across runs
9. Applies failure alert cooldown: ON_FAILURE notifications only fire after N consecutive failures, then respect a cooldown period
10. Records `delivery_status` and `delivery_error` on each run for observability

Jobs get the same integration tools as regular users (banking, iCloud, browser, etc.) but not admin tools (bash, file I/O).

### System-scope runs

When a job has `users: []`, the executor runs with `user_slug=_system`:

- **No memories** are injected — `_system` has no user profile
- **No per-user credentials** — only env-var or package-level skill requirements are satisfied; skills with credential requirements fall back to their SETUP.md
- **No auto-notify** — system jobs never deliver to a user channel. The output is logged for inspection only.
- **Run log** is filed at `runs/_system.jsonl`

Use system-scope for shared background work that benefits every user — news scraping, price checks, public API syncs — where there is no single user context to charge the run against.

### Error classification

Errors are classified by pattern matching against the error message:

| Category | Retryable | Pattern examples |
|----------|-----------|-----------------|
| `rate_limit` | Yes | 429, "rate limit", "too many requests" |
| `timeout` | Yes | "timed out", "timeout" |
| `network` | Yes | "connection refused", "DNS", "ECONNRESET" |
| `server_error` | Yes | 500-504, "internal error", "overloaded" |
| `permanent` | No | Everything else (auth errors, validation, etc.) |

## Templates

Templates are habitat-provided defaults for common job patterns. The zoo
ships four out of the box:

| Template | Default trigger | Model | Notify | Use case |
|----------|----------------|-------|--------|----------|
| `sync` | interval (8h) | haiku | on_failure | Periodic data sync |
| `check` | event | haiku | on_output | Monitor and alert |
| `scrape` | interval (1h) | haiku | silent | Web content scraping |
| `digest` | cron (daily 7 AM) | sonnet | always | Summary messages |

Each one is a ``template.yaml`` file under
``<MARCEL_ZOO_DIR>/jobs/<name>/`` — see
[Job habitat](plugins.md#job-habitat) for the schema.

## Agent tools

Users create and manage jobs conversationally through these tools:

| Tool | Description |
|------|-------------|
| `create_job` | Create a new background job |
| `list_jobs` | List all jobs with status and next run |
| `get_job` | Detailed view with recent run history |
| `update_job` | Modify job configuration |
| `delete_job` | Remove a job |
| `run_job_now` | Trigger immediate execution |
| `job_templates` | List available templates |

## Adding a new template

Create a habitat directory and drop in a ``template.yaml``. The zoo is
the usual home; per-install overrides can live at
``<data_root>/jobs/<name>/template.yaml`` — a data-root entry with the
same name wins over the zoo.

```yaml
# <MARCEL_ZOO_DIR>/jobs/my_template/template.yaml
description: What this template does.
default_trigger:
  type: interval
  interval_seconds: 3600
system_prompt: Instructions for the job agent...
task_template: 'Do {thing} and report results.'
notify: on_output
model: anthropic:claude-haiku-4-5-20251001
```

Discovery is a cold read on every call, so editing the YAML takes
effect without a restart. Required keys are `description`,
`system_prompt`, `notify`, and `model`; a habitat missing any of them
is skipped with a logged error.

Then update `~/.marcel/skills/jobs/SKILL.md` to document the new
template.

## Notification policies

| Policy | Behavior |
|--------|----------|
| `always` | Send output after every run |
| `on_failure` | Notify after N consecutive failures (default: 3), then cooldown (default: 1h) |
| `on_output` | Only when the agent produces non-empty output |
| `silent` | Never notify |

Notifications are delivered via the job's configured `channel` (default: Telegram).

The policy is the **single source of truth** for whether a job can reach the user. It gates both the scheduler's automatic post-run notification **and** any mid-run `marcel(action="notify")` calls the agent tries to make:

- `silent` / `on_failure` — `TurnState.suppress_notify` is set to `True` before the agent runs. Calls to `marcel(action="notify")` short-circuit to a suppression notice without touching Telegram. For `on_failure`, the scheduler still sends its own alert when a run fails (after the consecutive-failure threshold and cooldown).
- `on_output` / `always` — agent-initiated notify calls pass through. If the agent notifies, `run.agent_notified` is set and the scheduler skips its own send to avoid double-delivery.

The job executor also injects a `## Delivery policy` block into the job's system prompt describing what the agent is allowed to do, so well-behaved agents don't even attempt suppressed notifications.

## Run status values

| Status | Meaning |
|--------|---------|
| `completed` | Job finished successfully |
| `failed` | Job hit an error |
| `timed_out` | Job exceeded `timeout_seconds` and was killed |
| `running` | Job is currently executing |
| `pending` | Job is queued but hasn't started |
