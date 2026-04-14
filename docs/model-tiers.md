# Model Fallback Tiers

Marcel routes every model call through a **four-tier fallback chain** so a
transient failure on the primary provider doesn't surface as a raw error
to the user. The same chain covers both interactive conversation turns
(Telegram, CLI, web) and scheduled background jobs, with the only
difference being how tier 3 behaves when both cloud tiers have failed.

## The four tiers

| Tier | Env var                   | Purpose                                                      |
|------|---------------------------|--------------------------------------------------------------|
| 1    | `MARCEL_STANDARD_MODEL`   | Normal calls. Default: `anthropic:claude-sonnet-4-6`.        |
| 2    | `MARCEL_BACKUP_MODEL`     | Different-cloud-provider backup. Skipped when unset.         |
| 3    | `MARCEL_FALLBACK_MODEL`   | Last-ditch local model — **explains the failure** (turns) or retries the task (jobs). Skipped when unset. |
| 4    | `MARCEL_POWER_MODEL`      | Heavyweight model for the `power` subagent. Default: `anthropic:claude-opus-4-6`. Not part of the failure chain. |

Tier 2 and tier 3 are opt-in — a fresh install with only `MARCEL_STANDARD_MODEL`
set behaves identically to pre-ISSUE-076 Marcel (single model, no fallback).

## Turn behaviour (Telegram / CLI / web)

The runner iterates through the chain. Each tier gets one streaming
attempt. If an attempt raises an error:

- **Pre-stream failure** (no tokens yielded yet) on an eligible category →
  silently advance to the next tier. The user never sees the failed
  attempt. This is the common Anthropic `overloaded_error` path.
- **Mid-stream failure** (tokens already yielded) → keep the partial
  output, append an `[Error mid-response: ...]` tail, do not retry.
  Retrying would either duplicate work on tier 2 or discard output the
  user already saw.
- **Permanent error** (validation failure, unknown skill) → surface the
  raw error text immediately. There's no point in a canned apology when
  the input itself is malformed.

### Tier 3 in turn mode: the "explain the failure" voice

When both cloud tiers have failed, the local model runs with a
**synthesised system prompt** whose only job is to tell the user, in one
short paragraph, that cloud models are temporarily unavailable. It runs
with:

- An empty message history — no conversation context leak.
- An empty tool filter — no tools registered at all.
- A hard cap of `request_limit=1` — a small local model cannot start an
  accidental tool loop.
- The first line of the original error (truncated to 240 chars) and its
  category tag as the *only* grounding.

This is a deliberate reliability trade-off. A 4B-class local model is
not going to usefully answer "Can you restart my Plex?" when Anthropic is
down — but it *can* reliably produce "Sorry, Marcel's main models are
having a hiccup. Please try again in a minute." That's the contract.

### Eligible error categories

Classification happens through the shared `classify_error()` helper in
`marcel_core.jobs.executor`. These categories advance the chain:

- `rate_limit` — 429, "too many requests"
- `timeout` — ETIMEDOUT, "timed out"
- `network` — DNS, connection refused, socket errors
- `server_error` — 500/502/503/504, "overloaded", "internal error"
- `auth_or_quota` — 401/403, "invalid API key", "insufficient_quota"

Anything else is classified `permanent` and short-circuits the chain.

## Job behaviour (scheduled background runs)

The job executor composes the chain with its existing per-tier backoff
retry loop. The sequence for each tier is:

1. `execute_job()` on the tier's model
2. If transient, exponential backoff up to `job.max_retries`
3. If still failed, advance to the next tier and repeat

Tier 3 in **job mode** uses `purpose='complete'` — the local model runs
the original task with the original tools, like the legacy ISSUE-070
local fallback. (In turn mode, tier 3's `purpose='explain'`; see above.)

### `allow_fallback_chain` — the opt-out flag

New in ISSUE-076: `JobDefinition.allow_fallback_chain: bool = True`. When
True (the default), the job participates in the global chain. Set it to
False to hard-pin a job to its primary model with retries only:

- **Deterministic jobs** whose output must come from one specific model
- **Cost-sensitive cron jobs** pinned to a cheap model where silent
  escalation to tier 2 would blow up the budget
- **Jobs deliberately pinned to a local model** (`model='local:...'`) —
  the chain would otherwise escalate to cloud, defeating the purpose

### ⚠ Warning — local-pinned jobs and the silent-escalation footgun

A job with `model='local:qwen3.5:4b'` and the default
`allow_fallback_chain=True` **will silently escalate to
`MARCEL_BACKUP_MODEL` if the local run fails**. This is by design — the
chain doesn't know whether your local pin is a deliberate choice or just
what you happened to have configured — but it means:

> **Always set `allow_fallback_chain=False` when pinning a job to a local
> model.** Otherwise, an outage on the local Ollama server sends your
> cost-sensitive job to the cloud, which is probably not what you want.

A similar concern applies to jobs pinned to a cheap cloud model like
`anthropic:claude-haiku-4-5-20251001`. If budget matters, opt out.

### `allow_local_fallback` — legacy ISSUE-070 flag

Still works. Gates the **local** tier 3 for jobs in complete mode:

- `allow_local_fallback=False` (default) — tier 3 local models are
  stripped from the chain for this job. Cloud tier 2 still runs.
- `allow_local_fallback=True` — tier 3 local models are kept. If
  `MARCEL_FALLBACK_MODEL` is unset but `MARCEL_LOCAL_LLM_URL`/
  `MARCEL_LOCAL_LLM_MODEL` are, a `local:<tag>` tier 3 is synthesised
  from those legacy env vars (the bridge path for pre-ISSUE-076 jobs).

### Behaviour matrix

| `job.model`                     | `allow_fallback_chain` | `allow_local_fallback` | Effective chain                                                            |
|---------------------------------|:----------------------:|:----------------------:|----------------------------------------------------------------------------|
| `anthropic:claude-sonnet-4-6`   | True (default)         | False (default)        | sonnet → `MARCEL_BACKUP_MODEL` → *(local skipped)*                         |
| `anthropic:claude-sonnet-4-6`   | True                   | True                   | sonnet → `MARCEL_BACKUP_MODEL` → `MARCEL_FALLBACK_MODEL` (complete)        |
| `anthropic:claude-haiku-...`    | True (default)         | False                  | haiku → `MARCEL_BACKUP_MODEL` → *(local skipped)* — **cost surprise!**     |
| `anthropic:claude-haiku-...`    | False                  | False                  | haiku only, with retries. **Recommended for cheap cron jobs.**             |
| `local:qwen3.5:4b`              | True (default)         | False                  | local → `MARCEL_BACKUP_MODEL` → *(local skipped)* — **silent cloud escalation!** |
| `local:qwen3.5:4b`              | **False**              | False                  | local only, with retries. **REQUIRED for deliberate local pins.**          |
| any                             | False                  | True                   | pinned model with retries, then legacy local-fallback (ISSUE-070)          |

The two bold warning rows are the cases where you must remember to flip
`allow_fallback_chain=False`.

### What `fallback_used` reports

Each `JobRun` records which tier provided the successful output:

- `None` — tier 1 completed (or all tiers failed)
- `'backup'` — tier 2 (cloud backup) completed
- `'local'` — tier 3 local model completed, or the legacy
  `allow_local_fallback` path fired (kept for backwards compat with old
  `runs.jsonl` readers)

## Per-channel model pin (interactive)

Interactive turns still honour the per-channel model pin set via
`marcel({"action": "set_model", "value": "telegram:openai:gpt-4o"})`. The
pin **replaces tier 1 only** — tiers 2 and 3 still come from the
environment variables. If you've pinned `#writing` to GPT-4o for prose
quality, an OpenAI outage will still fall through to
`MARCEL_BACKUP_MODEL` (typically Anthropic) and then to the local
explain-tier.

## The `power` subagent

When Marcel decides a task exceeds its standard model's capabilities, it
can delegate to the bundled `power` subagent:

```python
# from inside a Marcel turn, the agent can call:
delegate(subagent_type='power', prompt='Refactor the X module to decouple Y from Z, think hard.')
```

The `power` agent lives at `~/.marcel/agents/power.md` (seeded from
`src/marcel_core/defaults/agents/power.md` on first startup). Its
frontmatter uses the `model: power` sentinel, which the delegate tool
resolves to `MARCEL_POWER_MODEL` at call time. To override the power
model without a restart, just set the env var:

```bash
MARCEL_POWER_MODEL=anthropic:claude-opus-4-6
```

### Tier sentinels in agent frontmatter

Any subagent can reference a tier by name in its frontmatter:

```markdown
---
name: custom
description: Example of a backup-tier-pinned agent
model: backup
---
```

Valid sentinels: `standard`, `backup`, `fallback`, `power`. They're
resolved against `settings.marcel_*_model` at delegate time. If the env
var is unset when the agent is invoked, `delegate()` returns a clean
error message rather than raising.

## Example configurations

### Cloud-only with cross-provider backup

```bash
# .env.local
MARCEL_STANDARD_MODEL=anthropic:claude-sonnet-4-6
MARCEL_BACKUP_MODEL=openai:gpt-4o
# MARCEL_FALLBACK_MODEL unset — no explain tier
```

Overloaded on Anthropic → silently retries on OpenAI. Total OpenAI
outage on top of that → raw error text, no apology. This is the minimum
"don't show my users overloaded_error" setup.

### Cloud + local explain

```bash
# .env.local
MARCEL_STANDARD_MODEL=anthropic:claude-sonnet-4-6
MARCEL_BACKUP_MODEL=openai:gpt-4o
MARCEL_FALLBACK_MODEL=local:qwen3.5:4b
MARCEL_LOCAL_LLM_URL=http://127.0.0.1:11434/v1
MARCEL_LOCAL_LLM_MODEL=qwen3.5:4b
```

Full three-tier chain. Both Anthropic and OpenAI down? The user gets a
friendly one-sentence apology from the local model instead of a Python
exception. Requires the Ollama setup described in
[docs/local-llm.md](./local-llm.md).

### Local-dominant with cloud explain

```bash
# .env.local
MARCEL_STANDARD_MODEL=local:qwen3.5:4b
MARCEL_BACKUP_MODEL=anthropic:claude-sonnet-4-6
MARCEL_FALLBACK_MODEL=openai:gpt-4o-mini
MARCEL_LOCAL_LLM_URL=http://127.0.0.1:11434/v1
MARCEL_LOCAL_LLM_MODEL=qwen3.5:4b
```

Everything runs on local Ollama by default. Local fails → Sonnet takes
over. Sonnet fails → the cheap OpenAI model writes the apology. This is
the privacy-first configuration: cloud only speaks when local is down.

## Observability

Every chain escalation is logged at INFO level:

```
shaun-telegram: stream started tier=standard model=anthropic:claude-sonnet-4-6
shaun-telegram: tier=standard failed (server_error) — advancing
shaun-telegram: stream started tier=backup model=openai:gpt-4o
shaun-telegram: turn complete tier=backup — 312 tokens (...)
```

For jobs, the same info plus `fallback_used` in `runs.jsonl`:

```bash
grep -r '"fallback_used":"backup"' ~/.marcel/users/*/jobs/*/runs.jsonl
grep -r '"fallback_used":"local"' ~/.marcel/users/*/jobs/*/runs.jsonl
```

## Known limitations

- **Mid-stream failures are not auto-retried.** If Anthropic drops the
  connection 500 tokens into a long answer, the user sees the partial
  reply plus an `[Error mid-response: ...]` tail. A future iteration
  could buffer text and re-run invisibly against tier 2, but that
  requires careful handling of already-executed tool calls and is
  deferred.
- **Tier 3 explain doesn't see conversation history.** Interactive turns
  hand the local model only the synthesised system prompt plus a short
  user wrapper — prior conversation context is deliberately dropped. The
  goal is a reliable apology, not a conversation resume.
- **`local:` fallback models need their transport configured.** Setting
  `MARCEL_FALLBACK_MODEL=local:qwen3.5:4b` without also setting
  `MARCEL_LOCAL_LLM_URL` and `MARCEL_LOCAL_LLM_MODEL` logs a warning and
  silently drops tier 3 from the chain.
