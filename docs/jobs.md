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

### TriggerSpec

| Trigger type | Fields | Example |
|-------------|--------|---------|
| `cron` | `cron: str` | `"0 7 * * *"` (daily at 7 AM) |
| `interval` | `interval_seconds: int` | `28800` (every 8 hours) |
| `event` | `after_job: str`, `only_if_status: RunStatus` | Fires after another job completes |
| `oneshot` | `run_at: datetime` (optional) | Runs once, then disables |

## Scheduler

The scheduler runs as an asyncio task in the FastAPI lifespan, alongside the existing banking sync loop. It:

1. Loads all active jobs on startup
2. Computes `next_run_at` for each job using `croniter` (cron) or interval math
3. Ticks every 30 seconds, dispatching due jobs
4. Listens to an event bus for job-chaining (event triggers)
5. Persists scheduler state to `scheduler_state.json` for restart recovery

## Executor

The executor runs a job as a **headless agent turn**:

1. Creates `MarcelDeps` with `channel="job"` and `role="user"`
2. Builds a system prompt combining the job's own prompt with user profile context
3. Creates a Marcel agent via `create_marcel_agent()`
4. Runs non-streaming: `agent.run(job.task, deps=deps)`
5. Captures output, logs the run, sends notifications, emits events

Jobs get the same integration tools as regular users (banking, iCloud, browser, etc.) but not admin tools (bash, file I/O).

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
| `on_failure` | Only notify on errors |
| `on_output` | Only when the agent produces non-empty output |
| `silent` | Never notify |

Notifications are delivered via the job's configured `channel` (default: Telegram).
