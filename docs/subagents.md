# Subagents

Marcel's main agent can delegate a focused subtask to a purpose-built
**subagent** via the `delegate` tool. A subagent is a fresh pydantic-ai
`Agent` instance with its own system prompt, a filtered tool pool, and its
own model budget. The parent waits for the subagent to finish and receives
a single string result back.

!!! note "Admin-role only"
    `delegate` is a power tool — it's registered only on admin-role agents,
    alongside `bash`, `read_file`, `git_*`, and `claude_code`. Regular users
    never see it in their tool pool, and a subagent spawned from an admin
    parent cannot escalate: the recursion guard drops `delegate` from every
    child's pool unless the child's frontmatter explicitly opts in.

Think of it as `Task()` from Claude Code: you describe what the helper
should do, the helper runs in an isolated context, and you get its final
report — without the parent's turn history cluttering the child's context,
or the child's intermediate tool calls cluttering the parent's.

## When to use it

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
the wrapping overhead isn't worth it.

## Invoking the delegate tool

`delegate` is an **admin-only** tool. It appears in the tool pool when the
caller's role is `admin`; regular users never see it.

```text
delegate(
    subagent_type="explore",
    prompt="Find every reference to create_marcel_agent in src/marcel_core "
           "and list the file paths and line numbers.",
    description="Locate create_marcel_agent call sites",
)
```

Arguments:

- **`subagent_type`** *(required)* — the `name` of a subagent from the
  agents directory, e.g. `explore` or `plan`. Must match a markdown file
  under `<data_root>/agents/` (without the `.md` suffix).
- **`prompt`** *(required)* — the task for the subagent. Be specific: the
  subagent has no memory of the parent conversation, so include any file
  paths, line numbers, and context it will need.
- **`description`** *(optional)* — a 3-5 word summary for logs. Does not
  affect execution.

The return value is the subagent's final output as a string. On failure
(unknown agent, timeout, model error) the tool returns an error message
prefixed with `delegate error:` instead of raising, so the parent can
decide how to recover.

## Agent definition files

Subagents live at `<data_root>/agents/<name>.md` (typically
`~/.marcel/agents/`). Each is a markdown file with YAML frontmatter:

```markdown
---
name: explore
description: Fast read-only codebase explorer
model: inherit                        # or e.g. anthropic:claude-haiku-4-5-20251001
tools: [read_file, web, integration, marcel]
disallowed_tools: []
max_requests: 25
timeout_seconds: 300
---

You are the **explore** subagent — a focused, read-only researcher.

[... full system prompt here ...]
```

The body after the second `---` becomes the subagent's system prompt
**verbatim** — no MARCEL.md, memory, channel guidance, or skill index is
layered on top. This is deliberate: the subagent runs in a clean context
so the parent's state cannot bleed in, and the subagent's token budget
goes entirely to its own specialized instructions.

### Frontmatter reference

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

The resolution happens every time the subagent is invoked, so env-var
updates take effect on the next turn without a restart. If the referenced
env var is unset when the agent is invoked, `delegate()` returns a clean
`delegate error:` message rather than raising — the parent can decide
how to recover. See [docs/model-tiers.md](./model-tiers.md) for the full
tier system.

> **Removed (ISSUE-e0db47):** the `backup` sentinel is no longer accepted.
> Agent loading rejects it at startup with a warning pointing at the new
> per-tier names.

### Tool names

The `tools` allowlist uses the same stable short names as the main agent's
tool registry. Available tool names include:

**Read-only / everyone:** `web`, `integration`, `marcel`, `generate_chart`,
`create_job`, `list_jobs`, `get_job`, `update_job`, `delete_job`,
`run_job_now`, `job_templates`, `job_cache_write`, `job_cache_read`

**Admin-only:** `bash`, `read_file`, `write_file`, `edit_file`,
`git_status`, `git_diff`, `git_log`, `git_add`, `git_commit`, `git_push`,
`claude_code`, `delegate`

Admin-only tools are stripped from user-role subagents even if explicitly
allowlisted — role gating beats allowlist. This is the guarantee that
prevents a crafted agent markdown file from escalating a user-level
subagent to shell access.

### Recursion guard

By default, subagents **do not** get the `delegate` tool in their pool —
even when their frontmatter omits `tools` and inherits the full
role-default set. A subagent that wants to spawn further subagents must
explicitly list `delegate` in its `tools` allowlist. This prevents
unbounded nesting and makes the delegation tree easy to reason about.

## Default subagents

Three subagents ship with Marcel and are seeded to `<data_root>/agents/`
on first startup from `src/marcel_core/defaults/agents/`:

- **`explore`** — a read-only file/codebase explorer. Tools:
  `read_file`, `web`, `integration`, `marcel`. Good for "find the code
  that does X" and "summarize the structure of Y".
- **`plan`** — a software architect that turns a fuzzy task into a
  concrete implementation plan. Tools: `read_file`, `web`, `marcel`. Good
  for "what's the smallest change to do Z?".
- **`power`** — a heavyweight reasoning agent backed by `MARCEL_POWER_MODEL`
  (default `anthropic:claude-opus-4-6`). The parent delegates to it when
  a task is hard enough that the standard model is likely to fumble —
  multi-file refactors, debugging sessions requiring broad context,
  plans where a wrong step is expensive. Inherits the parent's role-default
  tool pool (admin users get bash/file IO/git; regular users get the safer
  subset). See [docs/model-tiers.md](./model-tiers.md).

Edit these files freely in your data root — the seed step never
overwrites existing files, so your customizations survive restarts. Add
new subagents by dropping additional `<name>.md` files alongside them.

## Cost and safety

Delegation is not free. A subagent runs its own model loop with its own
tool calls, so an incautious parent can multiply token usage several times
over. Mitigations:

- **Set `max_requests`** on every subagent frontmatter. 15-25 is a good
  default for scoped investigations. Unlimited agents are a footgun.
- **Set `timeout_seconds`** as a hard wall-clock backstop. 300 seconds is
  the default.
- **Keep allowlists tight.** A subagent that only needs `read_file` and
  `web` should not inherit the full admin pool.
- **Don't delegate the same task twice.** If the parent already has the
  answer, skip the round trip.

On the safety side: the `delegate` tool is only exposed to admin users;
subagents run under the parent's role; admin-only tools are stripped from
user-role subagents regardless of allowlist; and the recursion guard
prevents unbounded nesting. See [Self-Modification](self-modification.md)
for the broader permission model.

## Scope limits (v1)

The current implementation is intentionally minimal:

- **Synchronous only.** `delegate` blocks until the subagent returns.
  Background / async delegation is a deferred follow-up — for now, a
  parent that wants long-running work can have the subagent call
  `create_job` from within its own run.
- **No `parent_job_id` tracking.** Delegated runs aren't recorded in
  `runs.jsonl` today. If you need the delegation tree for observability,
  add structured logging in the calling path.
- **No fork mode** (parent context inheritance), **no worktree / remote
  isolation**, **no agent teams**. These clawcode features are deferred
  until concrete use cases appear.

See ISSUE-074 for the full design and the list of deferred features.
