# Agents (subagents)

A **subagent habitat** is a markdown file that declares a purpose-built
child agent the main agent can `delegate()` to — `explore` for read-only
codebase searches, `plan` for implementation planning, `power` for
heavyweight reasoning. Each subagent runs with its own system prompt, a
filtered tool pool, and its own model and budget. The parent waits for
the subagent to finish and receives a single string result back.

Subagents are the simplest habitat kind: there is **no Python plugin
surface** — you write markdown, and the kernel spins up a pydantic-ai
agent with the declared tool pool, model, and body-as-system-prompt.
Think of it as Claude Code's `Task()`: you describe what the helper
should do, it runs in an isolated context, and you get its final report
— without the parent's turn history cluttering the child's context, or
the child's intermediate tool calls cluttering the parent's.

Since FEAT-260718-b6d1da, delegation rides the **harness `SubAgents`
capability** (`pydantic_ai_harness.subagents`): the capability
contributes the single `delegate` tool and lists the available
subagents in the agent's instructions. Everything Marcel-specific —
doc discovery, the frontmatter schema, child assembly, the recursion
rule — lives in `marcel_core.capabilities.subagents`.

!!! note "Admin-role only"
    `delegate` is a power tool — the SubAgents capability is attached
    only to admin-role builds, alongside the shell/file capabilities
    (`run_command`, `read_file`, …), `git_*`, and `claude_code`.
    Regular users never see it in their tool pool, and a subagent
    spawned from an admin parent cannot escalate: `delegate` is
    dropped from every child's pool, and a child only gets its own
    SubAgents capability when its frontmatter explicitly opts in.

See [Habitats](habitats.md) for how subagents fit alongside the other
four kinds.

## Directory layout

Agent docs resolve through the same scoping chain as skills — four
roots, scanned in least→most-specific order, **most specific wins** on
a name collision:

1. **`<MARCEL_ZOO_DIR>/agents/<name>.md`** — habitats from the
   marcel-zoo checkout (the authoritative source for bundled defaults).
2. **`<MARCEL_DATA_DIR>/agents/<name>.md`** (typically
   `~/.marcel/agents/`) — install-wide customization; overrides the
   zoo version of the same name.
3. **`<MARCEL_ZOO_DIR>/users/<slug>/agents/<name>.md`** — per-user
   habitats shipped in the zoo.
4. **`<MARCEL_DATA_DIR>/users/<slug>/agents/<name>.md`** — per-user
   local customization; wins over everything.

The per-user roots are only consulted when the agent is built for a
known user. The habitat convention is **one markdown file = one
subagent**; files starting with `_` or `.` are ignored.

## Minimal example

`<MARCEL_ZOO_DIR>/agents/explore.md`:

```markdown
---
name: explore
description: Fast read-only codebase explorer
model: inherit
tools: [read_file, web, marcel]
disallowed_tools: []
max_requests: 25
timeout_seconds: 300
---

You are the **explore** subagent — a focused, read-only researcher.

[... full system prompt here ...]
```

Agent docs are re-read on every agent build (i.e. every turn), so the
new subagent is available to the parent as
`delegate(agent_name="explore", ...)` on the next turn — no restart
required.

## Frontmatter schema

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `name` | string | filename stem | Identifier used by `delegate(agent_name=...)`. Must be unique across the agent roots (most specific wins). |
| `description` | string | `""` | One-line summary shown in the parent's subagent catalog. |
| `model` | string | `inherit` | Pydantic-ai model string (e.g. `anthropic:claude-haiku-4-5-20251001`). `inherit` (or omitting the key) runs the child on the parent's model at delegation time. Supports `local:<tag>` for self-hosted models and the tier sentinels `local` / `fast` / `standard` / `power` (see [Model tier sentinels](#model-tier-sentinels)). |
| `tools` | list[string] | *(all role-default tools)* | Tool-name allowlist. See [Tool names](#tool-names). Omit for the full role-default pool. |
| `disallowed_tools` | list[string] | `[]` | Tools to remove after the allowlist is applied. Handy when you want "everything except X". |
| `max_requests` | int | *(none)* | Maximum model calls per delegated run (pydantic-ai `UsageLimits.request_limit`), enforced as an **isolated per-child budget** — see [Cost and safety](#cost-and-safety). |
| `timeout_seconds` | int | `300` | Wall-clock budget for a single run. An explicit `0` means zero seconds — falsy values are honored, not silently replaced by the default. |

Clawcode-compatible camelCase aliases are also accepted:
`disallowedTools`, `maxRequests` / `maxTurns`, `timeoutSeconds`.
Unknown frontmatter keys are tolerated (logged at debug level) so agent
files written for other runtimes keep loading.

The body after the second `---` becomes the subagent's system prompt
**verbatim** — no MARCEL.md, channel guidance, or skill catalog is
layered on top, and skills/connectors are never attached to a child.
This is deliberate: the subagent runs in a clean context so the
parent's state cannot bleed in, and the child's token budget goes
entirely to its own specialized instructions. Each child is still a
full Marcel agent build, so its tool calls are policy-gated and
recorded exactly like the parent's.

### Model tier sentinels

In addition to fully-qualified `provider:model` strings, the `model`
frontmatter field accepts the four **tier names** from Marcel's model
ladder, resolved against the per-tier env vars:

| Sentinel   | Resolves to                |
|------------|----------------------------|
| `local`    | `MARCEL_FALLBACK_MODEL`    |
| `fast`     | `MARCEL_FAST_MODEL`        |
| `standard` | `MARCEL_STANDARD_MODEL`    |
| `power`    | `MARCEL_POWER_MODEL`       |

Resolution happens every time the parent agent is built (i.e. every
turn), so env-var updates take effect on the next turn without a
restart. If the referenced env var is unset, the agent is **skipped
with a logged warning** and simply absent from the parent's catalog —
delegation to it is never offered, rather than erroring mid-turn.

`model: inherit` (or an absent `model` key) builds the child
model-less; the harness runs it on **whatever model the parent is on
at delegation time**, including per-turn tier routing like `/fast`.

> **Removed:** the `backup` tier is no longer accepted — an agent doc
> pinning it is skipped with a warning pointing at the new per-tier
> names. The old `fallback` sentinel name is gone too; its tier is now
> spelled `local`.

### Tool names

The `tools` allowlist uses the same stable short names as the main
agent's tool registry.

**Read-only / everyone:** `web`, `marcel`,
`generate_chart`, `create_job`, `list_jobs`, `get_job`, `update_job`,
`delete_job`, `run_job_now`, `job_templates`, `job_cache_write`,
`job_cache_read`

**Admin-only:** `run_command`/`start_command`/`check_command`/`stop_command`
(the Shell capability), `read_file`/`write_file`/`edit_file`/`list_directory`/
`search_files`/`find_files`/`create_directory`/`file_info` (the FileSystem
capability), `git_status`, `git_diff`, `git_log`, `git_add`, `git_commit`,
`git_push`, `claude_code`, `delegate`. Shell and file tools are provided by
capabilities (FEAT-260718-38235c); listing any of them in a subagent's
`tools:` frontmatter grants exactly that subset (a read-only explorer that
lists only `read_file` never gains `write_file`). The legacy filter name
`bash` still grants the Shell capability, so older agent docs keep working.

Admin-only tools are stripped from user-role subagents even if
explicitly allowlisted — **role gating beats allowlist**. This is the
guarantee that prevents a crafted agent markdown file from escalating
a user-level subagent to shell access.

### Recursion rule

By default, subagents **cannot** delegate further — even when their
frontmatter omits `tools` and inherits the full role-default set.
`delegate` is not a registry tool: it is the SubAgents capability's
own tool, and it is always discarded from a child's tool pool. What
listing `delegate` in a subagent's `tools` allowlist *actually* does
is flip the child's build flag so the child gets its own SubAgents
capability (and therefore its own `delegate` tool). Nesting is
depth-bounded (two levels), so an opted-in agent that can reach itself
cannot recurse without limit. This keeps the delegation tree easy to
reason about.

## Discovery

Discovery is a fresh read on **every agent build** — editing a
subagent markdown takes effect on the next turn, no restart required.
A file that cannot be read (or that pins the removed `backup` tier) is
logged and skipped; siblings continue loading. A missing `name` key
falls back to the filename stem.

When no agent docs are visible at all — `MARCEL_ZOO_DIR` unset and no
local docs in the data roots — the SubAgents capability is simply not
attached, and `delegate` does not appear in the tool pool. Consistent
with every other habitat kind, the kernel is content-free.

For the boot summary and admin tooling, the orchestrator wraps
discovery as
[`SubagentHabitat.discover_all`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/plugin/habitat.py)
— the same uniform surface as the other eagerly-listed kinds. That
global view feeds logging only; agent builds always use the per-user
chain directly.

## Invoking via `delegate`

`delegate` is the tool the parent agent calls to run a subagent. The
SubAgents capability lists the available subagents (name + description)
in the parent's instructions, so the model knows the catalog without an
extra lookup.

```text
delegate(
    agent_name="explore",
    task="Find every reference to create_marcel_agent in src/marcel_core "
         "and list the file paths and line numbers.",
)
```

Arguments:

- **`agent_name`** *(required)* — the `name` of a subagent from the
  catalog.
- **`task`** *(required)* — the complete, self-contained instruction
  for the subagent. Be specific: the subagent has no memory of the
  parent conversation, so include any file paths, line numbers, and
  context it will need.

The return value is the subagent's final output as a string. Failures
surface as **readable tool results, never a crashed parent turn**:

- An unknown `agent_name` returns a retryable error listing the
  available subagents.
- Exceeding `timeout_seconds` returns a soft "exceeded its *N*s time
  budget" result.
- Exhausting `max_requests` returns a soft "reached its usage budget"
  result.
- A crash inside the child is contained and reported as a failure
  message.

In every case the parent sees what happened and decides how to recover
— retry with a corrected task, fall back to direct tool calls, or tell
the user.

Each delegation runs under a derived conversation id
(`<parent-conversation>:delegate:<agent_name>`) with a fresh per-turn
state, so the child's history and per-turn flags never mix with the
parent conversation.

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
  `read_file`, `web`, `marcel`. Good for "find the
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
  shell/file IO/git; regular users get the safer subset). See
  [Model tiers](./model-tiers.md).

Override any of these by dropping `<name>.md` into a more specific
root (e.g. `~/.marcel/agents/explore.md`, or a per-user
`~/.marcel/users/<slug>/agents/explore.md`) — the most specific root
wins on name collisions. Add new subagents by dropping additional
`<name>.md` files into any root.

## Cost and safety

Delegation is not free. A subagent runs its own model loop with its
own tool calls, so an incautious parent can multiply token usage
several times over. Mitigations:

- **Set `max_requests`** on every subagent frontmatter. 15–25 is a
  good default for scoped investigations. It becomes an **isolated
  per-child budget**: the child may spend at most that many model
  calls per delegation, its usage does not count against the parent's
  limits, and hitting the cap is a soft, readable tool result — not
  an aborted turn. An agent *without* `max_requests` instead folds
  its token usage into the parent run's totals. All bundled agents
  set `max_requests`.
- **Set `timeout_seconds`** as a hard wall-clock backstop. 300 seconds
  is the default.
- **Keep allowlists tight.** A subagent that only needs `read_file`
  and `web` should not inherit the full admin pool.
- **Don't delegate the same task twice.** If the parent already has
  the answer, skip the round trip.

On the safety side: the SubAgents capability is only attached for
admin builds; subagents run under the parent's role; admin-only tools
are stripped from user-role subagents regardless of allowlist; and the
recursion rule prevents unbounded nesting. See
[Self-modification](self-modification.md) for the broader permission
model.

## Subagents in jobs

A job with `dispatch_type: subagent` runs a subagent on a schedule,
through the **same seam** as delegation — the executor loads the doc
and builds the child exactly as the capability would, but always at
role `user` and without chain retries. A `model: inherit` doc runs on
the job's own `model:` (or the configured default) instead of a parent
model. See [Jobs → Dispatch types](jobs.md#dispatch-types).

## Why subagents need no extra isolation

Subagents are markdown plus a model instance — there is no Python code
to isolate. (Habitats that *do* carry code — connector servers — have
their own lifecycle and trust model; see
[Connectors → Trust model](connectors.md#trust-model).) Subagents already run in a
clean context (no parent state bleeds in), with tight tool allowlists
(role gating + explicit allowlist), under a `max_requests` /
`timeout_seconds` budget. There is no additional isolation ceiling to
raise by spawning them in a subprocess.

## Scope limits

The current implementation is intentionally minimal:

- **Synchronous only.** `delegate` blocks until the subagent returns.
  A parent that wants long-running work can have the subagent call
  `create_job` from within its own run.
- **No fork mode** (parent context inheritance), **no worktree /
  remote isolation**, **no agent teams**. These clawcode features are
  deferred until concrete use cases appear.

## See also

- [Habitats](habitats.md) — the five-kind taxonomy.
- [Model tiers](model-tiers.md) — how `local` / `fast` / `standard` /
  `power` resolve.
- [Jobs](jobs.md) — scheduling a subagent with `dispatch_type: subagent`.
- [Self-modification](self-modification.md) — the broader permission
  model for admin tools.
