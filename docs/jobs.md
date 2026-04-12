# Background Jobs

Marcel's job system lets users create background tasks that run automatically on schedules or in response to events. Jobs execute as headless agent turns — they get AI reasoning and access to all integration skills.

## Architecture

```
src/marcel_core/jobs/
    __init__.py       # CRUD + file storage
    models.py         # Pydantic data models
    executor.py       # Headless agent execution
    scheduler.py      # Tick loop + event bus
    templates.py      # Built-in templates
    tool.py           # Agent-facing tools
```

Jobs are stored per-user at `~/.marcel/users/{slug}/jobs/{job_id}/`:

- `job.json` — serialized `JobDefinition`
- `runs.jsonl` — append-only log of `JobRun` entries

## Data models

### JobDefinition

The core model. Defines what a job does and when it runs.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Auto-generated 12-char hex ID |
| `name` | `str` | Human-readable name |
| `trigger` | `TriggerSpec` | When/how the job fires |
| `system_prompt` | `str` | Instructions for the job agent |
| `task` | `str` | The "user message" the agent receives |
| `model` | `str` | Model to use (default: `claude-haiku-4-5-20251001`) |
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

1. Creates `MarcelDeps` with `channel="job"` and `role="user"`
2. Builds a system prompt combining the job's own prompt with user profile context
3. Creates a Marcel agent via `create_marcel_agent()`
4. Runs with timeout enforcement: `asyncio.wait_for(agent.run(...), timeout=job.timeout_seconds)`
5. On failure, classifies the error as transient (rate limit, network, timeout, 5xx) or permanent
6. Retries only transient errors with exponential backoff from `backoff_schedule`
7. Tracks `consecutive_errors` on the job definition across runs
8. Applies failure alert cooldown: ON_FAILURE notifications only fire after N consecutive failures, then respect a cooldown period
9. Records `delivery_status` and `delivery_error` on each run for observability

Jobs get the same integration tools as regular users (banking, iCloud, browser, etc.) but not admin tools (bash, file I/O).

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

Built-in templates provide sensible defaults for common patterns:

| Template | Default trigger | Model | Notify | Use case |
|----------|----------------|-------|--------|----------|
| `sync` | interval (8h) | haiku | on_failure | Periodic data sync |
| `check` | event | haiku | on_output | Monitor and alert |
| `scrape` | interval (1h) | haiku | silent | Web content scraping |
| `digest` | cron (daily 7 AM) | sonnet | always | Summary messages |

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

Add an entry to `TEMPLATES` in `src/marcel_core/jobs/templates.py`:

```python
TEMPLATES['my_template'] = {
    'description': 'What this template does.',
    'default_trigger': {'type': 'interval', 'interval_seconds': 3600},
    'system_prompt': 'Instructions for the job agent...',
    'task_template': 'Do {thing} and report results.',
    'notify': 'on_output',
    'model': 'claude-haiku-4-5-20251001',
}
```

Then update `.marcel/skills/jobs/SKILL.md` to document the new template.

## Notification policies

| Policy | Behavior |
|--------|----------|
| `always` | Send output after every run |
| `on_failure` | Notify after N consecutive failures (default: 3), then cooldown (default: 1h) |
| `on_output` | Only when the agent produces non-empty output |
| `silent` | Never notify |

Notifications are delivered via the job's configured `channel` (default: Telegram).

## Run status values

| Status | Meaning |
|--------|---------|
| `completed` | Job finished successfully |
| `failed` | Job hit an error |
| `timed_out` | Job exceeded `timeout_seconds` and was killed |
| `running` | Job is currently executing |
| `pending` | Job is queued but hasn't started |
