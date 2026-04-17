# Model Tiers & Fallback Chains

Marcel runs every model call through a **four-tier ladder**, indexed 0–3,
from cheapest local to most capable cloud. Each cloud tier carries its own
cross-cloud backup, and any cloud tier can tail its chain with a
cloud-outage explainer (local by default). The same chain covers
interactive turns (Telegram, CLI, web) and scheduled jobs.

## The four tiers

| Index | Tier         | Primary env var              | Per-tier backup                 | Default primary                             |
|:-----:|--------------|------------------------------|---------------------------------|---------------------------------------------|
| 0     | **LOCAL**    | `MARCEL_FALLBACK_MODEL`      | *(none — LOCAL is the floor)*   | *(unset; requires Ollama — see [local-llm.md](local-llm.md))* |
| 1     | **FAST**     | `MARCEL_FAST_MODEL`          | `MARCEL_FAST_BACKUP_MODEL`      | `anthropic:claude-haiku-4-5-20251001`       |
| 2     | **STANDARD** | `MARCEL_STANDARD_MODEL`      | `MARCEL_STANDARD_BACKUP_MODEL`  | `anthropic:claude-sonnet-4-6`               |
| 3     | **POWER**    | `MARCEL_POWER_MODEL`         | `MARCEL_POWER_BACKUP_MODEL`     | `anthropic:claude-opus-4-6`                 |

LOCAL is the floor tier. Its chain is exactly one entry — LOCAL IS the
fallback, so it never recursively tails itself.

Every backup is opt-in — a fresh install with only the primary env vars
set behaves like a single-model deployment with no failover.

> **Breaking change (ISSUE-e0db47):** `MARCEL_BACKUP_MODEL` was removed.
> Deployments that relied on it must migrate to the matching per-tier
> variable — typically `MARCEL_STANDARD_BACKUP_MODEL`.

## Admin tier defaults

Two env vars control which tiers Marcel picks when nothing else applies
(ISSUE-6a38cd). Both accept tier **indexes** (0–2 — POWER is never
admin-selectable):

| Env var                  | Default | Meaning                                                             |
|--------------------------|:-------:|---------------------------------------------------------------------|
| `MARCEL_DEFAULT_TIER`    | `1`     | Tier used when no session tier is stored and no prefix/skill applies. |
| `MARCEL_FALLBACK_TIER`   | `0`     | Tier that tails every cloud chain as the cloud-outage explainer.      |

- Set `MARCEL_DEFAULT_TIER=0` to run a privacy-first household where every
  turn starts on local.
- Set `MARCEL_FALLBACK_TIER=1` if you have no local LLM and want a cloud
  haiku to write the cloud-outage apology instead.

Both values are range-checked at startup (`ValidationError` on 3+).

## User-facing slash prefixes

A user can one-shot-override the tier for a single turn by starting the
message with a slash prefix. The prefix is stripped from the text before
the model sees it; the session tier is **not** mutated.

| Prefix        | Effect                                                                 |
|---------------|------------------------------------------------------------------------|
| `/local`      | Force tier 0 for this turn.                                            |
| `/fast`       | Force tier 1 for this turn.                                            |
| `/standard`   | Force tier 2 for this turn.                                            |
| `/power`      | **Rejected.** Marcel replies with a short explanation and does not call any model. POWER is reachable only via a skill or subagent that declares it. |
| `/<skillname>`| Force-load that skill's `SKILL.md` into the turn's context and use the remaining text as the skill's input. Mirrors Claude Code's `/<command>` pattern — skills are prompt templates with args. Unknown names fall through unchanged. |

Only a literal leading `/` at column 1 counts. `hello /fast` is plain
text; ` /fast hello` (leading space) is plain text; `/` alone is plain
text. Reserved prefixes (`local`, `fast`, `standard`, `power`) cannot be
used as skill names — tier prefixes always win.

## How the session picks a tier (interactive turns)

The session tier is decided **once per session** and then reused until the
conversation is idle-summarized (`MARCEL_IDLE_SUMMARIZE_MINUTES`, default
60). The resolution pipeline lives in
`marcel_core.harness.turn_router.resolve_turn` (ISSUE-6a38cd) — a pure
function, highest precedence wins:

1. **User slash prefix** — `/local`, `/fast`, `/standard` on the current
   message force that tier for this turn only (prefix is stripped before
   the model sees the text). `/power` is rejected. Does **not** mutate the
   session tier. Users can only downshift: a skill that asked for POWER
   is never reachable this way (there is no `/power`), so the "skill
   beats user" guarantee still holds in the one direction that matters.
2. **Active skill `preferred_tier`** — a per-turn override. When the turn's
   context includes one or more skills whose SKILL.md declares
   `preferred_tier: fast|standard|power`, the highest one wins
   (POWER > STANDARD > FAST). Does **not** mutate the session tier.
3. **Session tier** — persisted in the user's
   `~/.marcel/users/{slug}/settings.json` under `channel_tiers`. Set by the
   classifier on the session's first message and bumped on frustration.
4. **Classifier** — runs only on the session's first message. Uses
   keyword lists in `~/.marcel/routing.yaml` to pick between
   `MARCEL_DEFAULT_TIER` and `MARCEL_DEFAULT_TIER + 1`
   (typically FAST ↔ STANDARD). **POWER is never auto-selected** — it's
   reached only via an explicit skill (`preferred_tier: power`) or
   subagent (`model: power`).

Frustration detection runs alongside step 3. If the user's message matches
a frustration pattern and the session is currently on FAST, the session
tier is bumped to STANDARD (and persisted). Frustration on STANDARD is a
no-op — POWER remains opt-in.

See [Routing config](routing.md) for the keyword lists and how to edit
them without a restart.

## Behaviour inside a single turn

Once the tier is chosen, the runner builds that tier's chain as
`[primary, backup?, local-explain?]` and iterates:

- **Pre-stream failure** (no tokens yet) on a transient category → silently
  advance to the next entry. Common Anthropic `overloaded_error` path.
- **Mid-stream failure** (partial tokens sent) → keep the partial output,
  append an `[Error mid-response: ...]` tail, do not retry. Retrying would
  either duplicate work on the backup or discard output the user saw.
- **Permanent error** → surface the raw error text. Canned apologies on
  malformed input hide the real problem.

### The local explain tier

When both the primary and its backup have failed, `MARCEL_FALLBACK_MODEL`
runs with a **synthesised system prompt** whose only job is to tell the
user, in one short paragraph, that cloud models are temporarily
unavailable. It runs with:

- Empty message history — no conversation context leak to the local model.
- Empty tool filter — no tools registered at all.
- `request_limit=1` — a small local model cannot start an accidental tool
  loop.
- The first line of the original error (truncated to 240 chars) plus its
  category tag as the *only* grounding.

A 4B-class local model is not going to usefully answer "Can you restart my
Plex?" when Anthropic is down — but it *can* reliably produce "Sorry,
Marcel's main models are having a hiccup." That's the contract.

### Eligible error categories

The shared `classify_error()` helper in `marcel_core.harness.model_chain`
advances the chain on:

- `rate_limit` — 429, "too many requests"
- `timeout` — ETIMEDOUT, "timed out"
- `network` — DNS, connection refused, socket errors
- `server_error` — 500/502/503/504, "overloaded", "internal error"
- `auth_or_quota` — 401/403, "invalid API key", "insufficient_quota"

Anything else is `permanent` and short-circuits the chain.

## Job behaviour (scheduled background runs)

**Jobs always run at the STANDARD tier.** They never consult
`channel_tiers`, never invoke the classifier, and ignore skill
`preferred_tier` fields. A job's own `model` pin (often `local:`) wins as
the primary slot; `MARCEL_STANDARD_BACKUP_MODEL` covers cross-cloud
failover.

For each tier entry:

1. `execute_job()` on the tier's model
2. If transient, exponential backoff up to `job.max_retries`
3. If still failed, advance to the next entry

The local fallback in **job mode** uses `purpose='complete'` — the local
model retries the original task with the original tools, like the legacy
ISSUE-070 local fallback. (In turn mode, the local tier's `purpose='explain'`;
see above.)

### `allow_fallback_chain` — the opt-out flag

`JobDefinition.allow_fallback_chain: bool = True`. When True (the default),
the job participates in the STANDARD chain. Set to False to hard-pin:

- **Deterministic jobs** whose output must come from one specific model
- **Cost-sensitive cron jobs** where silent escalation would break the
  budget
- **Jobs deliberately pinned to a local model** (`model='local:...'`) —
  the chain would otherwise escalate to cloud, defeating the purpose

### Local-pinned jobs — automatic guard (ISSUE-b95ac5)

The executor **automatically forces `allow_fallback_chain=False`** when
`job.model` starts with `local:`. This prevents a local-pinned job from
silently escalating to cloud tiers if the local run fails. The override
is logged as a warning.

Setting `allow_fallback_chain=False` explicitly in your JOB.md is still
valid for clarity, but no longer required.

A similar concern applies to jobs pinned to a cheap cloud model like
`anthropic:claude-haiku-4-5-20251001`. If budget matters, opt out manually
— the automatic guard only covers `local:` models.

### `allow_local_fallback` — legacy ISSUE-070 flag

Gates the **local** fallback for jobs in complete mode:

- `allow_local_fallback=False` (default) — the local entry is stripped
  from the chain for this job. The STANDARD backup still runs.
- `allow_local_fallback=True` — the local entry is kept. If
  `MARCEL_FALLBACK_MODEL` is unset but `MARCEL_LOCAL_LLM_URL` /
  `MARCEL_LOCAL_LLM_MODEL` are, a `local:<tag>` entry is synthesised from
  those legacy env vars (the bridge path for pre-ISSUE-076 jobs).

### Behaviour matrix

| `job.model`                     | `allow_fallback_chain` | `allow_local_fallback` | Effective chain                                                                     |
|---------------------------------|:----------------------:|:----------------------:|-------------------------------------------------------------------------------------|
| `anthropic:claude-sonnet-4-6`   | True (default)         | False (default)        | sonnet → `MARCEL_STANDARD_BACKUP_MODEL` → *(local skipped)*                         |
| `anthropic:claude-sonnet-4-6`   | True                   | True                   | sonnet → `MARCEL_STANDARD_BACKUP_MODEL` → `MARCEL_FALLBACK_MODEL` (complete)        |
| `anthropic:claude-haiku-...`    | True (default)         | False                  | haiku → `MARCEL_STANDARD_BACKUP_MODEL` → *(local skipped)* — **cost surprise!**     |
| `anthropic:claude-haiku-...`    | False                  | False                  | haiku only, with retries. **Recommended for cheap cron jobs.**                      |
| `local:ministral-3:14b`         | True (default)         | False                  | local only, with retries. *(auto-forced by ISSUE-b95ac5 guard)*                     |
| `local:ministral-3:14b`         | False                  | False                  | local only, with retries. *(same — explicit opt-out, guard is redundant)*           |
| any                             | False                  | True                   | pinned model with retries, then legacy local fallback (ISSUE-070)                   |

The haiku row is the remaining case where you should flip
`allow_fallback_chain=False` if cost matters.

### What `fallback_used` reports

Each `JobRun` records which entry provided the successful output:

- `None` — the primary completed (or all entries failed)
- `'backup'` — the STANDARD backup completed
- `'local'` — the local fallback completed, or the legacy
  `allow_local_fallback` path fired (kept for backwards compat with old
  `runs.jsonl` readers)

## Per-channel model pin (interactive)

Interactive turns still honour the per-channel model pin set via the
`settings` skill:

```python
marcel(action="set_model", channel="telegram", model="anthropic:claude-opus-4-6")
```

The pin **replaces the tier's primary slot only** — the backup still comes
from the *tier's* env var. If you've pinned `#writing` to GPT-4o for prose
quality, an OpenAI outage will still fall through to whichever tier's
backup was selected for that session (and then to the local explain tier).

## Skill-declared preferred tier

A skill can declare `preferred_tier` in its SKILL.md frontmatter:

```markdown
---
name: developer
description: Write, edit, review code — and modify Marcel itself.
preferred_tier: power
---
```

Valid values: `fast`, `standard`, `power`.

- The preference applies **only while the skill is active in the turn's
  context** (i.e. the skill's docs are loaded).
- It does **not** mutate `channel_tiers`. The session tier resumes on the
  next turn where no preferred-tier skill is active.
- When multiple active skills declare a tier, the highest one wins.
- Unknown values log a warning and are ignored — a broken edit never hides
  the skill from the agent.

Two default skills ship with declarations:

- `developer` → `preferred_tier: power`
- `settings` → `preferred_tier: fast`

## The `power` subagent

When Marcel decides a task exceeds its session tier, it can delegate to
the bundled `power` subagent:

```python
delegate(subagent_type='power', prompt='Refactor the X module to decouple Y from Z, think hard.')
```

The `power` agent lives at `~/.marcel/agents/power.md` (seeded from
`src/marcel_core/defaults/agents/power.md` on first startup). Its
frontmatter uses the `model: power` sentinel, which the delegate tool
resolves to `MARCEL_POWER_MODEL` at call time.

### Tier sentinels in agent frontmatter

Any subagent can reference a tier by name in its frontmatter:

```markdown
---
name: custom
description: Example of a fast-tier-pinned agent
model: fast
---
```

Valid sentinels: `fast`, `standard`, `power`, `fallback`. They're resolved
against `settings.marcel_<tier>_model` at delegate time. If the env var is
unset when the agent is invoked, `delegate()` returns a clean error message
rather than raising.

> **Removed:** `model: backup` is no longer valid. Agent loading rejects
> it at startup with a warning pointing at the new tier names.

## Example configurations

### Cloud-only with per-tier backups

```bash
# ~/.marcel/.env
MARCEL_FAST_MODEL=anthropic:claude-haiku-4-5-20251001
MARCEL_FAST_BACKUP_MODEL=openai:gpt-4o-mini

MARCEL_STANDARD_MODEL=anthropic:claude-sonnet-4-6
MARCEL_STANDARD_BACKUP_MODEL=openai:gpt-4o

MARCEL_POWER_MODEL=anthropic:claude-opus-4-6
MARCEL_POWER_BACKUP_MODEL=openai:gpt-4o
# MARCEL_FALLBACK_MODEL unset — no local explain tier
```

Overloaded on Anthropic → silently retries on the matching OpenAI model.
Full Anthropic + OpenAI outage → raw error text. This is the minimum
"don't show my users `overloaded_error`" setup for the four-tier router.

### Cloud + local explain

```bash
# ~/.marcel/.env
MARCEL_STANDARD_MODEL=anthropic:claude-sonnet-4-6
MARCEL_STANDARD_BACKUP_MODEL=openai:gpt-4o

MARCEL_FAST_MODEL=anthropic:claude-haiku-4-5-20251001
MARCEL_FAST_BACKUP_MODEL=openai:gpt-4o-mini

MARCEL_FALLBACK_MODEL=local:ministral-3:14b
MARCEL_LOCAL_LLM_URL=http://127.0.0.1:11434/v1
MARCEL_LOCAL_LLM_MODEL=ministral-3:14b
```

FAST and STANDARD each have their own cloud backup. If both the primary
and its backup fail, the local model writes a friendly one-sentence
apology instead of a Python exception. Requires the Ollama setup in
[docs/local-llm.md](./local-llm.md).

### Local-dominant with cloud explain

```bash
# ~/.marcel/.env
MARCEL_STANDARD_MODEL=local:ministral-3:14b
MARCEL_STANDARD_BACKUP_MODEL=anthropic:claude-sonnet-4-6
MARCEL_FALLBACK_MODEL=openai:gpt-4o-mini
MARCEL_LOCAL_LLM_URL=http://127.0.0.1:11434/v1
MARCEL_LOCAL_LLM_MODEL=ministral-3:14b
```

Everything runs on local Ollama by default. Local fails → Sonnet takes
over. Sonnet fails → the cheap OpenAI model writes the apology. This is
the privacy-first configuration: cloud only speaks when local is down.

## Observability

Every tier decision, chain advance, and frustration bump is logged at INFO
level:

```
tier_resolved user=shaun channel=telegram tier=fast reason=classified:fast:\bwhat(?:'s| is)\b
shaun-telegram: stream started tier=fast model=anthropic:claude-haiku-4-5-20251001
shaun-telegram: tier=fast failed (server_error) — advancing
shaun-telegram: stream started tier=fast model=openai:gpt-4o-mini
shaun-telegram: turn complete tier=fast — 312 tokens (...)
```

For jobs, the same info plus `fallback_used` in the per-user run logs:

```bash
grep -r '"fallback_used":"backup"' ~/.marcel/jobs/*/runs/*.jsonl
grep -r '"fallback_used":"local"' ~/.marcel/jobs/*/runs/*.jsonl
```

## Known limitations

- **Mid-stream failures are not auto-retried.** If Anthropic drops the
  connection 500 tokens into a long answer, the user sees the partial
  reply plus an `[Error mid-response: ...]` tail.
- **The local explain tier doesn't see conversation history.** Interactive
  turns hand the local model only the synthesised system prompt plus a
  short user wrapper — prior context is deliberately dropped. The goal is
  a reliable apology, not a conversation resume.
- **`local:` fallback models need their transport configured.** Setting
  `MARCEL_FALLBACK_MODEL=local:ministral-3:14b` without
  `MARCEL_LOCAL_LLM_URL` / `MARCEL_LOCAL_LLM_MODEL` logs a warning and
  silently drops the local entry from the chain.
- **The classifier runs once per session.** A user who starts with a
  trivial question ("what time is it?") and then asks a hard one
  ("debug this stack trace") stays on FAST until frustration triggers or
  the session goes idle. The frustration loop is the intended corrective
  — a complex question that doesn't include `debug`/`analyze`/etc. keywords
  still misroutes until frustration or idle reset kicks in.
