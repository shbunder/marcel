# Agents (subagents)

A **subagent habitat** is a markdown file that declares a purpose-built
child agent the main agent can `delegate()` to — `explore` for read-only
codebase searches, `plan` for implementation planning, `power` for
heavyweight reasoning. Each subagent runs with its own system prompt, a
filtered tool pool, and its own model budget. The parent waits for the
subagent to finish and receives a single string result back.

Subagents are the simplest habitat kind: there is **no Python plugin
surface** — the kernel's `delegate` tool loads the markdown and spins up
a pydantic-ai agent with the declared tool pool, model, and body-as-
system-prompt. Think of it as Claude Code's `Task()`: you describe what
the helper should do, it runs in an isolated context, and you get its
final report — without the parent's turn history cluttering the child's
context, or the child's intermediate tool calls cluttering the parent's.

!!! note "Admin-role only"
    `delegate` is a power tool — it is registered only on admin-role
    agents, alongside `bash`, `read_file`, `git_*`, and `claude_code`.
    Regular users never see it in their tool pool, and a subagent
    spawned from an admin parent cannot escalate: the recursion guard
    drops `delegate` from every child's pool unless the child's
    frontmatter explicitly opts in.

See [Habitats](habitats.md) for how subagents fit alongside the other
four kinds.

## Directory layout

Two sources are scanned, in precedence order:

1. **`<MARCEL_ZOO_DIR>/agents/<name>.md`** — habitats from the
   marcel-zoo checkout (the authoritative source for bundled defaults).
2. **`<MARCEL_DATA_DIR>/agents/<name>.md`** (typically
   `~/.marcel/agents/`) — per-install override. A subagent with the
   same name wins over the zoo version.

The habitat convention is **one markdown file = one subagent**. No
wrapper directory unless the subagent grows resources (prompt fragments,
reference data) — at which point promote to
`<zoo>/agents/<name>/agent.md` + siblings. No habitats today require the
directory form.

## Minimal example

`<MARCEL_ZOO_DIR>/agents/explore.md`:

```markdown
---
name: explore
description: Fast read-only codebase explorer
model: inherit
tools: [read_file, web, integration, marcel]
disallowed_tools: []
max_requests: 25
timeout_seconds: 300
---

You are the **explore** subagent — a focused, read-only researcher.

[... full system prompt here ...]
```

After the next delegate call the agent is available to the parent as
`delegate(subagent_type="explore", ...)` — no restart required, loader
is a cold read.

## Frontmatter schema

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `name` | string | filename stem | Identifier used by `delegate(subagent_type=...)`. Must be unique within the agents directory. |
| `description` | string | `""` | One-line summary shown in the agent index. |
| `model` | string | `inherit` | Pydantic-ai model string (e.g. `anthropic:claude-haiku-4-5-20251001`). `inherit` uses the parent's model. Supports `local:<tag>` for self-hosted models and tier sentinels `fast` / `standard` / `power` / `fallback` (see [Model tier sentinels](#model-tier-sentinels)). |
| `tools` | list[string] | *(all role-default tools)* | Tool-name allowlist. See [Tool names](#tool-names). Omit for the full role-default pool. |
| `disallowed_tools` | list[string] | `[]` | Tools to remove after the allowlist is applied. Handy when you want "everything except X". |
| `max_requests` | int | *(none)* | Maximum model calls per delegated run (pydantic-ai `UsageLimits.request_limit`). Prevents runaway nesting. |
| `timeout_seconds` | int | `300` | Wall-clock budget for a single run. The tool returns a timeout error if exceeded. |

Clawcode-compatible aliases are also accepted: `disallowedTools` for
`disallowed_tools`, and `maxTurns` for `max_requests`.

The body after the second `---` becomes the subagent's system prompt
**verbatim** — no MARCEL.md, memory, channel guidance, or skill index
is layered on top. This is deliberate: the subagent runs in a clean
context so the parent's state cannot bleed in, and the subagent's token
budget goes entirely to its own specialized instructions.

### Model tier sentinels

In addition to fully-qualified `provider:model` strings, the `model`
frontmatter field accepts four **tier sentinels** that resolve against
the per-tier env vars at delegate time:

| Sentinel   | Resolves to                |
|------------|----------------------------|
| `fast`     | `MARCEL_FAST_MODEL`        |
| `standard` | `MARCEL_STANDARD_MODEL`    |
| `power`    | `MARCEL_POWER_MODEL`       |
| `fallback` | `MARCEL_FALLBACK_MODEL`    |

Resolution happens every time the subagent is invoked, so env-var
updates take effect on the next turn without a restart. If the
referenced env var is unset when the agent is invoked, `delegate()`
returns a clean `delegate error:` message rather than raising — the
parent can decide how to recover. See
[Model tiers](./model-tiers.md) for the full tier system.

> **Removed (ISSUE-e0db47):** the `backup` sentinel is no longer
> accepted. Agent loading rejects it at startup with a warning pointing
> at the new per-tier names.

### Tool names

The `tools` allowlist uses the same stable short names as the main
agent's tool registry.

**Read-only / everyone:** `web`, `integration`, `marcel`,
`generate_chart`, `create_job`, `list_jobs`, `get_job`, `update_job`,
`delete_job`, `run_job_now`, `job_templates`, `job_cache_write`,
`job_cache_read`

**Admin-only:** `bash`, `read_file`, `write_file`, `edit_file`,
`git_status`, `git_diff`, `git_log`, `git_add`, `git_commit`,
`git_push`, `claude_code`, `delegate`

Admin-only tools are stripped from user-role subagents even if
explicitly allowlisted — **role gating beats allowlist**. This is the
guarantee that prevents a crafted agent markdown file from escalating
a user-level subagent to shell access.

### Recursion guard

By default, subagents **do not** get the `delegate` tool in their pool —
even when their frontmatter omits `tools` and inherits the full
role-default set. A subagent that wants to spawn further subagents must
explicitly list `delegate` in its `tools` allowlist. This prevents
unbounded nesting and makes the delegation tree easy to reason about.

## Discovery

`load_agents()` (in `marcel_core.agents.loader`) is a **cold read on
every call** — editing a subagent markdown takes effect on the next
delegation, no restart required. An agent file that cannot be parsed
or is missing the `name` field is logged and skipped; siblings continue
loading.

The orchestrator wraps the loader as
[`SubagentHabitat.discover_all`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/plugin/habitat.py)
so logging and admin tooling see subagents on the same uniform surface
as the other four kinds.

When `MARCEL_ZOO_DIR` is unset and no local subagents exist in the data
root, `load_agents()` returns `[]` and the `delegate` tool surfaces
"no subagent named X" on any call. Consistent with every other habitat
kind, the kernel is content-free.

## Invoking via `delegate`

`delegate` is the tool the parent agent calls to run a subagent. It is
admin-only — user-role agents never see it.

```text
delegate(
    subagent_type="explore",
    prompt="Find every reference to create_marcel_agent in src/marcel_core "
           "and list the file paths and line numbers.",
    description="Locate create_marcel_agent call sites",
)
```

Arguments:

- **`subagent_type`** *(required)* — the `name` of a subagent from
  either agents source. Must match a markdown file under
  `<MARCEL_ZOO_DIR>/agents/` or `<data_root>/agents/` (without the
  `.md` suffix).
- **`prompt`** *(required)* — the task for the subagent. Be specific:
  the subagent has no memory of the parent conversation, so include
  any file paths, line numbers, and context it will need.
- **`description`** *(optional)* — a 3-5 word summary for logs. Does
  not affect execution.

The return value is the subagent's final output as a string. On
failure (unknown agent, timeout, model error) the tool returns an
error message prefixed with `delegate error:` instead of raising, so
the parent can decide how to recover.

### When to reach for it

Delegation earns its cost when the subtask is:

- **Scoped and read-mostly** — "find every place `foo` is called and
  summarize the call sites" is a great fit for the `explore` subagent.
- **Planning-heavy** — "figure out the minimal steps to migrate this
  module" fits the `plan` subagent.
- **Naturally parallelizable** — kicking off two `explore` runs over
  different parts of the repo in one parent turn lets the model make
  progress on both simultaneously.

Skip it when you already know the answer, when one or two direct tool
calls would do, or when the subtask is just "call this one function" —
the wrapping overhead is not worth it.

## Default subagents

Three subagents ship as habitats in
[marcel-zoo](https://github.com/shbunder/marcel-zoo) under
`<MARCEL_ZOO_DIR>/agents/`:

- **`explore`** — a read-only file/codebase explorer. Tools:
  `read_file`, `web`, `integration`, `marcel`. Good for "find the
  code that does X" and "summarize the structure of Y".
- **`plan`** — a software architect that turns a fuzzy task into a
  concrete implementation plan. Tools: `read_file`, `web`, `marcel`.
  Good for "what's the smallest change to do Z?".
- **`power`** — a heavyweight reasoning agent backed by
  `MARCEL_POWER_MODEL` (default `anthropic:claude-opus-4-6`). The
  parent delegates when a task is hard enough that the standard model
  is likely to fumble — multi-file refactors, debugging sessions
  requiring broad context, plans where a wrong step is expensive.
  Inherits the parent's role-default tool pool (admin users get
  bash/file IO/git; regular users get the safer subset). See
  [Model tiers](./model-tiers.md).

Override any of these by dropping `<name>.md` into `<data_root>/agents/`
(e.g. `~/.marcel/agents/explore.md`) — the data root wins on name
collisions. Add new subagents by dropping additional `<name>.md` files
into either source.

## Cost and safety

Delegation is not free. A subagent runs its own model loop with its
own tool calls, so an incautious parent can multiply token usage
several times over. Mitigations:

- **Set `max_requests`** on every subagent frontmatter. 15–25 is a
  good default for scoped investigations. Unlimited agents are a
  footgun.
- **Set `timeout_seconds`** as a hard wall-clock backstop. 300 seconds
  is the default.
- **Keep allowlists tight.** A subagent that only needs `read_file`
  and `web` should not inherit the full admin pool.
- **Don't delegate the same task twice.** If the parent already has
  the answer, skip the round trip.

On the safety side: the `delegate` tool is only exposed to admin
users; subagents run under the parent's role; admin-only tools are
stripped from user-role subagents regardless of allowlist; and the
recursion guard prevents unbounded nesting. See
[Self-modification](self-modification.md) for the broader permission
model.

## Why subagents are not containerised

Subagents are markdown plus a model instance — there is no Python code
to isolate. The containerisation / UDS work in ISSUE-f60b09 targets
Python habitats (toolkit in Phase 2, channels/jobs in Phase 3) where
dependency isolation and failure isolation have concrete payoffs.
Subagents already run in a clean context (no parent state bleeds in),
with tight tool allowlists (role gating + explicit allowlist), under a
`max_requests` / `timeout_seconds` budget. There is no additional
isolation ceiling to raise by spawning them in a subprocess.

## Scope limits (v1)

The current implementation is intentionally minimal:

- **Synchronous only.** `delegate` blocks until the subagent returns.
  Background / async delegation is a deferred follow-up — for now, a
  parent that wants long-running work can have the subagent call
  `create_job` from within its own run.
- **No `parent_job_id` tracking.** Delegated runs aren't recorded in
  `runs.jsonl` today. If you need the delegation tree for
  observability, add structured logging in the calling path.
- **No fork mode** (parent context inheritance), **no worktree /
  remote isolation**, **no agent teams**. These clawcode features are
  deferred until concrete use cases appear.

See ISSUE-074 for the full design and the list of deferred features.

## See also

- [Habitats](habitats.md) — the five-kind taxonomy.
- [Model tiers](model-tiers.md) — how `fast` / `standard` / `power` /
  `fallback` resolve at delegate time.
- [Self-modification](self-modification.md) — the broader permission
  model for admin tools.
