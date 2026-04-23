# Jobs

A **job** runs Marcel work in the background — a periodic data sync, a
morning digest, a post-completion health check — without a user sitting
at a keyboard. Jobs can fire on a cron expression, a fixed interval, an
event from another job, or a one-shot scheduled time.

Unlike a typical cron task, a job can run its work three different ways
(`agent`, `tool`, `subagent`) so the cost model matches the shape of the
work: a deterministic RSS fetch pays no LLM cost, while a prompt-driven
digest gets the full harness. See [Dispatch types](#dispatch-types).

See [Habitats](habitats.md) for how jobs fit alongside the other four
kinds.

## Concept

A job has a name, a *trigger* (when to fire), a *dispatch type* (what
shape of work to run), and a *notification policy* (when to ping the
user). The scheduler fires due jobs on a 30-second tick; each run is
recorded to a per-user JSONL log for later inspection.

A job targets one or more users via the `users:` frontmatter field:

- `users: [alice]` — user-scoped (per-user credentials, memories,
  notifications).
- `users: [alice, bob]` — runs once per user each tick; each run gets
  its own credentials/memories.
- `users: []` — **system-scope**: runs once per tick without a user
  context, useful for shared work like news scraping. No per-user
  credentials or memories are injected, and no automatic notifications
  are sent.

## Storage layout

Jobs live in a flat directory at `~/.marcel/jobs/{slug}/`, mirroring
the skill layout:

```text
~/.marcel/jobs/{slug}/
├── JOB.md                  # YAML frontmatter + "## System Prompt" / "## Task" body
├── state.json              # mutable runtime state (errors, timestamps)
└── runs/
    ├── {user_slug}.jsonl   # per-user run log
    └── _system.jsonl       # used for system-scope jobs (users: [])
```

`{slug}` is derived from `job.name` (kebab-case, collision-safe).
Renaming a job does **not** move the directory — the slug is fixed at
creation.

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

`state.json` holds only the mutable runtime fields —
`consecutive_errors`, `schedule_errors`, `last_error_at`,
`last_failure_alert_at`, `updated_at` — so scheduler bookkeeping never
clobbers hand-authored prompts.

## Data models

### JobDefinition

The core model. Defines what a job does, who it targets, and when and
how it runs.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Auto-generated 12-char hex ID |
| `name` | `str` | Human-readable name (used to derive the slug at creation) |
| `users` | `list[str]` | Target users. Empty list = system-scope |
| `trigger` | `TriggerSpec` | When/how the job fires |
| `dispatch_type` | `JobDispatchType` | `agent` (default), `tool`, or `subagent` — see [Dispatch types](#dispatch-types) |
| `system_prompt` | `str` | Instructions for the job agent (body of JOB.md; required for `agent`, ignored for `tool`) |
| `task` | `str` | The "user message" the agent receives (body of JOB.md) |
| `model` | `str` | Fully-qualified pydantic-ai model (default: `anthropic:claude-haiku-4-5-20251001`) |
| `notify` | `NotifyPolicy` | When to notify the user |
| `status` | `JobStatus` | `active`, `paused`, or `disabled` |
| `timeout_seconds` | `int` | Max seconds before the job is killed (default: 600) |
| `backoff_schedule` | `list[int]` | Retry delays in seconds (default: `[30, 60, 300, 900, 3600]`) |
| `retention_days` | `int` | Days to keep run records (default: 30) |
| `alert_after_consecutive_failures` | `int` | Failures before ON_FAILURE alert fires (default: 3) |
| `alert_cooldown_seconds` | `int` | Min seconds between failure alerts (default: 3600) |

Dispatch-type-specific fields (`tool`, `tool_params`, `subagent`,
`subagent_task`) are documented under [Dispatch types](#dispatch-types).

## Triggers

A job's `TriggerSpec` declares *when* it fires.

| Trigger type | Fields | Example |
|-------------|--------|---------|
| `cron` | `cron: str`, `timezone: str \| None` | `"0 7 * * *"` with `"Europe/Brussels"` (daily at 7 AM local time) |
| `interval` | `interval_seconds: int` | `28800` (every 8 hours) |
| `event` | `after_job: str`, `only_if_status: RunStatus` | Fires after another job completes |
| `oneshot` | `run_at: datetime` (optional) | Runs once, then disables |

**Timezone handling.** Cron triggers accept an optional `timezone`
field as an [IANA timezone name](https://www.iana.org/time-zones)
(e.g. `"Europe/Brussels"`, `"America/New_York"`). When set, the cron
expression is interpreted in that timezone — so `"0 7 * * *"` with
`timezone="Europe/Brussels"` fires at 7 AM Brussels time year-round,
automatically adjusting for DST. When `timezone` is `None` or omitted,
the cron expression is interpreted in UTC. Non-cron triggers ignore
the field.

## Dispatch types

Every job declares **how** its work runs via `dispatch_type`
([ISSUE-ea6d47](https://github.com/shbunder/marcel/blob/main/project/issues/closed/ISSUE-260422-ea6d47-jobs-trigger-type.md)).
The field defaults to `agent`, so every pre-existing `JOB.md` /
`template.yaml` keeps working unchanged.

| Value | What runs | When to use it | Extra fields on `JobDefinition` / `template.yaml` |
|---|---|---|---|
| `agent` (default) | Full main-agent turn: system prompt, skills, memories, the whole model-fallback chain | Anything conversational, anything that needs the agent to reason about its output | `system_prompt`, `task`, `model`, `skills` (existing fields) |
| `tool` | One toolkit handler called directly — **no LLM, no retries** | Deterministic periodic work (RSS fetch, health poll, bank sync) where the handler already owns its own idempotency | `tool: <family>.<action>`, `tool_params: {...}` |
| `subagent` | Scoped subagent run (fresh `MarcelDeps`, tool filter + model from the subagent's frontmatter) — no chain retries | Bounded focused work: morning digest, weekly review — cheap context, no full skill set | `subagent: <name>`, `subagent_task: "..."` (supports `{user_slug}` placeholder) |

`dispatch_type: tool` is the real cost saver. A daily `news.sync` or
`docker_health_sweep` that doesn't need the agent can skip the whole
LLM cost — the executor calls the handler, captures its return string,
writes a `JobRun` with `status=COMPLETED`. Failures still classify
errors the same way (`timeout`, `network`, `rate_limit`, …) so existing
telemetry remains uniform across dispatch types.

### Shape validation

A `model_validator(mode='after')` on `JobDefinition` enforces shape
consistency. The following constraints are all enforced at load time
(and by the template loader — see [Declaring jobs](#declaring-jobs)):

- `dispatch_type: tool` requires a `tool:` field; `tool_params:` is
  optional and defaults to `{}`.
- `dispatch_type: subagent` requires a `subagent:` field;
  `subagent_task:` is optional.
- A job cannot mix `tool:` and `subagent:` — the fields are mutually
  exclusive.
- `dispatch_type: agent` forbids both `tool:` and `subagent:`.

A template or `JOB.md` that declares a bad shape is logged and skipped
— the rest of the jobs keep loading. See
[`src/marcel_core/plugin/jobs.py`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/plugin/jobs.py)
for the template-loader validation.

### Retry semantics by dispatch type

- **`agent`** — full retry chain with exponential backoff from
  `backoff_schedule` for transient errors (`rate_limit`, `timeout`,
  `network`, `server_error`). Permanent errors stop immediately. See
  [Executor](#executor).
- **`tool`** — **no retries**. The tool owns its own idempotency. A
  failure records one run with `status=FAILED`; the next trigger
  fires normally. This is deliberate — deterministic handlers that
  want retries should implement them internally where they have the
  best context for what's safe to retry.
- **`subagent`** — **no chain retries**. The subagent runs with its
  own `max_requests` / `timeout_seconds` budget. A failure records
  one run; the next trigger fires normally.

## Executor

The executor (`src/marcel_core/jobs/executor.py`) selects one of three
fire functions based on `dispatch_type`:

- `_fire_tool_job` — deterministic handler dispatch; no retries, no
  chain machinery. The tool owns its own idempotency.
- `_fire_subagent_job` — loads the subagent via the agents loader,
  builds a fresh pydantic-ai agent with the subagent's tool filter +
  timeout, runs it against the templated task.
- `_fire_agent_job` (default) — the **headless agent turn** described
  below. This is the only path that uses the full model-fallback chain
  ([ISSUE-076](https://github.com/shbunder/marcel/blob/main/project/issues/closed/))
  with per-tier backoff and local-LLM fallback.

Post-run bookkeeping (`consecutive_errors`, `save_job`,
`_notify_if_needed`, `append_run`) is shared across all three paths so
telemetry and notify behaviour stay uniform.

### The `agent` path

1. Picks the concrete run user — the sole entry in `job.users`, the
   explicitly-passed `user_slug` (for multi-user jobs), or the
   reserved `_system` slug for system-scope jobs.
2. Creates `MarcelDeps` with `channel="job"` and `role="user"`.
3. Builds a system prompt combining the job's own prompt with user
   profile context (skills, credentials, preference/feedback
   memories).
4. Creates a Marcel agent via `create_marcel_agent()`.
5. Runs with timeout enforcement:
   `asyncio.wait_for(agent.run(...), timeout=job.timeout_seconds)`.
6. On failure, classifies the error as transient (rate limit,
   network, timeout, 5xx) or permanent.
7. Retries only transient errors with exponential backoff from
   `backoff_schedule`.
8. Tracks `consecutive_errors` on the job definition across runs.
9. Applies failure alert cooldown: `on_failure` notifications only
   fire after `alert_after_consecutive_failures` consecutive failures,
   then respect `alert_cooldown_seconds`.
10. Records `delivery_status` and `delivery_error` on each run for
    observability.

Jobs get the same toolkit handlers as regular users (banking, iCloud,
browser, etc.) but not admin tools (bash, file I/O).

### System-scope runs

When a job has `users: []`, the executor runs with `user_slug=_system`:

- **No memories** are injected — `_system` has no user profile.
- **No per-user credentials** — only env-var or package-level skill
  requirements are satisfied; skills with credential requirements
  fall back to their SETUP.md.
- **No auto-notify** — system jobs never deliver to a user channel.
  The output is logged for inspection only.
- **Run log** is filed at `runs/_system.jsonl`.

Use system-scope for shared background work that benefits every user —
news scraping, price checks, public API syncs — where there is no
single user context to charge the run against.

### Error classification

Errors are classified by pattern matching against the error message:

| Category | Retryable | Pattern examples |
|----------|-----------|-----------------|
| `rate_limit` | Yes | 429, "rate limit", "too many requests" |
| `timeout` | Yes | "timed out", "timeout" |
| `network` | Yes | "connection refused", "DNS", "ECONNRESET" |
| `server_error` | Yes | 500-504, "internal error", "overloaded" |
| `permanent` | No | Everything else (auth errors, validation, etc.) |

Classification applies to all three dispatch types for telemetry, but
only `agent` jobs actually retry transient classes.

## Scheduler

The scheduler runs as an asyncio task in the FastAPI lifespan. It:

1. Loads all active jobs on startup, resolving orphaned RUNNING
   records from previous crashes.
2. Computes `next_run_at` for each job using `croniter` (cron) or
   interval math, with deterministic per-job stagger offsets to avoid
   thundering-herd dispatch.
3. Ticks every 30 seconds, dispatching due jobs through a concurrency
   semaphore (max 3 parallel).
4. Sweeps for stuck jobs (running > 2 hours) and clears them
   automatically.
5. Listens to an event bus for job-chaining (event triggers).
6. Persists scheduler state to `scheduler_state.json` for restart
   recovery.
7. On startup, staggers overdue missed jobs (max 3, 30s apart) instead
   of firing all at once.
8. Runs a daily cleanup loop, removing run records older than
   `retention_days`.
9. Auto-disables jobs after 3 consecutive schedule-computation errors.

## Declaring jobs

There are two ways a job can be declared, each with a different
lifecycle:

### 1. Templates (conversational)

A template is a reusable set of defaults the agent drops into a new
`JobDefinition` during conversational job creation — "sync my bank
every 8 hours", "check the weather and alert if it rains". The zoo
ships four out of the box:

| Template | Default trigger | Model | Notify | Use case |
|----------|----------------|-------|--------|----------|
| `sync` | interval (8h) | haiku | on_failure | Periodic data sync |
| `check` | event | haiku | on_output | Monitor and alert |
| `scrape` | interval (1h) | haiku | silent | Web content scraping |
| `digest` | cron (daily 7 AM) | sonnet | always | Summary messages |

Each one is a `template.yaml` file under
`<MARCEL_ZOO_DIR>/jobs/<name>/`. Two sources are scanned:

1. `<MARCEL_ZOO_DIR>/jobs/<name>/template.yaml` — zoo-provided defaults.
2. `<data_root>/jobs/<name>/template.yaml` — per-install override.
   A template with the same name wins over the zoo version.

A `<data_root>/jobs/<slug>/` directory without a `template.yaml` is a
*job instance* (it has `JOB.md` + `state.json` + `runs/`) and is
ignored by the template loader — template and instance directories
coexist in the same tree without conflict.

Templates are the **conversational starting point**: the user says
"sync my bank every 8 hours", the agent picks the `sync` template,
fills in `skill: banking`, and calls `create_job` — which writes a
fresh `JOB.md` from the template defaults. The user edits from there.

**No kernel fallback.** If `MARCEL_ZOO_DIR` is unset and no local
templates exist, the template set is empty and `job_templates` returns
an empty list. Consistent with the other habitat kinds, the kernel is
content-free.

#### Template schema

| Key | Required | Notes |
|---|---|---|
| `description` | yes | One-line human description; surfaced by `job_templates`. |
| `default_trigger` | no | `TriggerSpec` dict — `{type: interval\|cron\|event\|oneshot, interval_seconds?, cron?, timezone?, run_at?}`. |
| `system_prompt` | yes | System prompt injected into the job agent's turn (required for `agent` dispatch; ignored for `tool`). |
| `task_template` | no | Optional `str.format`-style template with placeholders the agent fills at job-creation time. |
| `notify` | yes | `always \| on_failure \| on_output \| silent`. |
| `model` | yes | Fully-qualified pydantic-ai model id (e.g. `anthropic:claude-haiku-4-5-20251001`). |
| `dispatch_type` | no | `agent` (default), `tool`, or `subagent`. |
| `tool`, `tool_params`, `subagent`, `subagent_task` | when applicable | See [Dispatch types](#dispatch-types). |

Extra keys are preserved and returned to the caller untouched. A
habitat missing any required key is skipped with a logged error — one
broken habitat never aborts discovery of its siblings.

#### Adding a new template

Create a habitat directory and drop in a `template.yaml`:

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
effect without a restart. Then update
`<MARCEL_ZOO_DIR>/skills/jobs/SKILL.md` to document the new template.

### 2. `scheduled_jobs:` in a toolkit (declarative)

A toolkit habitat can declare always-on background work inline in its
`toolkit.yaml`:

```yaml
# <MARCEL_ZOO_DIR>/toolkit/news/toolkit.yaml
name: news
provides:
  - news.sync

scheduled_jobs:
  - name: "News digest"
    handler: news.sync
    cron: "0 7 * * *"
    notify: on_failure
```

Each entry becomes a system-scope `JobDefinition` at scheduler
startup. Full schema + validation rules: see
[Plugins → Scheduled jobs](plugins.md#scheduled-jobs).

### Templates vs. `scheduled_jobs:` — which to use?

| If your work is… | Use… |
|---|---|
| Conversational — the user decides when and for whom | A **template**, invoked via `create_job`. |
| Always on — fires on its own whenever the toolkit is loaded, no user opt-in | A **`scheduled_jobs:`** entry in the toolkit. |

A toolkit can ship both: a `scheduled_jobs:` entry for the default
behaviour operators get out of the box, and a `template.yaml` for
users who want to spin up their own variants with different cadences
or notification policies.

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

## Notification policies

| Policy | Behavior |
|--------|----------|
| `always` | Send output after every run |
| `on_failure` | Notify after N consecutive failures (default: 3), then cooldown (default: 1h) |
| `on_output` | Only when the agent produces non-empty output |
| `silent` | Never notify |

Notifications are delivered via the job's configured `channel`
(default: Telegram).

The policy is the **single source of truth** for whether a job can
reach the user. It gates both the scheduler's automatic post-run
notification **and** any mid-run `marcel(action="notify")` calls the
agent tries to make:

- `silent` / `on_failure` — `TurnState.suppress_notify` is set to
  `True` before the agent runs. Calls to `marcel(action="notify")`
  short-circuit to a suppression notice without touching Telegram.
  For `on_failure`, the scheduler still sends its own alert when a
  run fails (after the consecutive-failure threshold and cooldown).
- `on_output` / `always` — agent-initiated notify calls pass through.
  If the agent notifies, `run.agent_notified` is set and the
  scheduler skips its own send to avoid double-delivery.

The job executor also injects a `## Delivery policy` block into the
job's system prompt describing what the agent is allowed to do, so
well-behaved agents don't even attempt suppressed notifications.

## Run status values

| Status | Meaning |
|--------|---------|
| `completed` | Job finished successfully |
| `failed` | Job hit an error |
| `timed_out` | Job exceeded `timeout_seconds` and was killed |
| `running` | Job is currently executing |
| `pending` | Job is queued but hasn't started |

## Legacy layout migration

The legacy layout (`~/.marcel/users/{slug}/jobs/{id}/job.json`) is
migrated automatically on first startup. The migration:

1. Walks `~/.marcel/users/*/jobs/*/job.json`.
2. Rewrites each as `~/.marcel/jobs/{slug}/JOB.md` + `state.json`
   with `users: [<old_user_slug>]`.
3. Moves `runs.jsonl` → `runs/{old_user_slug}.jsonl`.
4. Removes the legacy `~/.marcel/users/{slug}/jobs/` directory.

It is idempotent — subsequent boots short-circuit at zero cost once
the legacy directories are gone.

## See also

- [Habitats](habitats.md) — the five-kind taxonomy.
- [Plugins (toolkit)](plugins.md) — how `scheduled_jobs:` ties into a
  toolkit's `toolkit.yaml`.
- [Agents](agents.md) — the subagent kind referenced by
  `dispatch_type: subagent`.
- [Skills](skills.md) — how skill requirements flow into job runs via
  `depends_on:`.
