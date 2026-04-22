# ISSUE-3c1534: Five-habitat taxonomy — rename integrations → functions, tighten every habitat kind's contract

**Status:** Open
**Created:** 2026-04-22
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, taxonomy, marcel-zoo, architecture, docs

## Capture

**Original request (user):** User proposed a clarified habitat taxonomy with five kinds, each with explicit contracts:

> - **functions** — single-run python function, simple input / output. This includes python-scripts, and integrations (should we rename?!)
> - **skills** — md-files with yaml headers that will be loaded dynamically when the agent needs them, allow the agent to progressively discover (and forget) its capabilities
> - **(sub)agents** — md-files with yaml headers, the file itself is the instruction prompt, the header declares the agent's capabilities (they run using marcel's own harness as a subprocess)
> - **channels** — md-files (tells the agent how to use the channel) + formatting logic (python)
> - **jobs** — yaml-files configuring what to run when, can start both a function or a subagent
>
> Write a comprehensive issue how to refactor marcel-zoo into these habitats, what do we need for this? what needs to change? Be very thorough. Stay true to the principles of marcel (keep the code modular in line with pydantic-ai's vision).

**Resolved intent:** Align marcel's habitat vocabulary with pydantic-ai's primitives (tools, agents, system prompts, I/O transports) by adopting an explicit five-kind taxonomy: **functions** (replacing "integrations"), **skills**, **subagents**, **channels**, **jobs**. Each kind has one canonical directory shape, one frontmatter schema, one runtime contract, and one composition story with the other kinds. The rename and the schema tightening ship together so the kernel and zoo drop every ad-hoc "is-this-integration-or-skill?" edge case in one coherent refactor.

The refactor is cross-repo (marcel-core + marcel-zoo + docs) and phased. This issue captures the target state and the migration plan; sub-issues track the per-phase implementation work.

## Description

### Why now

Four pressures have converged:

1. **Nomenclature drift.** "Integrations" historically meant "python code that talks to an external API." Today it also covers pure-computation habitats (a function that summarises text via LLM, zero external I/O). The name no longer matches the shape.
2. **Skill/integration overlap is confusing.** A `SKILL.md` `depends_on: [icloud]` resolves against `integrations/`, not `skills/`. A new contributor reads "depends on icloud" and looks in `skills/icloud/`. Renaming integrations → functions decouples the words and removes the false overlap.
3. **Jobs have grown a second shape.** Today every job is `trigger_type: agent` (run the main LLM agent with a system prompt). Real workflows increasingly want `trigger_type: function` (deterministic call, no LLM cost) or `trigger_type: subagent` (bounded LLM turn). The current schema can't express that.
4. **UDS isolation (ISSUE-f60b09) is about to rename directory lookup paths anyway.** ISSUE-14b034 (Phase 2 of f60b09) migrates `integrations/` to UDS. Renaming at the same time avoids touching every habitat twice.

### Principles

Every decision below is checked against these:

- **Lightweight over bloated** (CLAUDE.md) — no new habitat kind added for cleanliness alone; each kind must justify its existence.
- **Generic over specific** — one primitive for "thing the agent can call," not a taxonomy per external API shape.
- **Human-readable over clever** — directory names, file names, and schema keys should be self-explanatory to a zoo keeper who reads the repo for the first time.
- **pydantic-ai alignment** — each habitat kind maps cleanly onto a pydantic-ai primitive:
  - function → pydantic-ai tool
  - subagent → pydantic-ai Agent instance
  - skill → dynamic system-prompt content
  - channel → I/O transport layer
  - job → scheduled invocation of Agent.run() or a tool call

### The five habitat kinds (target state)

```
marcel-zoo/
├── functions/           ← python callables with declared I/O (was: integrations/)
│   └── <name>/
│       ├── __init__.py          # @register("<name>.<action>") decorators
│       ├── function.yaml        # declarative contract (was: integration.yaml)
│       └── pyproject.toml       # per-habitat deps (ISSUE-14b034, Phase 2 of f60b09)
│
├── skills/              ← markdown injected into the agent's system prompt
│   └── <name>/
│       ├── SKILL.md             # frontmatter + body, loaded progressively
│       ├── SETUP.md             # fallback when requirements not met
│       └── components.yaml      # optional a2ui component schemas
│
├── agents/              ← subagent definitions (md + frontmatter)
│   └── <name>.md                # flat file — frontmatter declares capabilities, body is system prompt
│
├── channels/            ← bidirectional I/O transports (md + python)
│   └── <name>/
│       ├── __init__.py          # register_channel(plugin)
│       ├── channel.yaml         # capabilities declaration
│       ├── CHANNEL.md           # format hint for the agent
│       └── <transport>.py       # webhook, formatter, session state
│
├── jobs/                ← scheduled triggers (yaml only)
│   └── <name>/
│       └── template.yaml        # trigger_type: function | subagent | agent
│
├── MARCEL.md            ← top-level persona
├── routing.yaml         ← channel ↔ user routing
└── pyproject.toml       ← zoo-wide deps (shrinks as functions grow per-habitat venvs)
```

### Per-kind contract

For each kind: **purpose**, **directory shape**, **frontmatter**, **runtime**, **composition** (who calls it, what it calls).

---

#### 1. Functions (renamed from integrations)

**Purpose.** A callable unit with typed-ish input/output. Invoked by the agent via the `function` tool (today called `integration`). Every function belongs to a named family (e.g. `docker.*`, `icloud.*`) and the family name equals the directory name.

Functions are the only habitat kind where python code runs on behalf of the agent at turn-time. Every other kind is either data (skills, agent markdown, channel markdown, jobs), transport (channels), or orchestration (jobs).

**Directory shape.**

```
functions/<name>/
├── __init__.py          # required — imports register decorators at load time
├── function.yaml        # required — contract declaration
├── pyproject.toml       # required when isolation: uds — per-habitat deps
└── tests/               # optional — habitat-owned tests
```

**`function.yaml` schema.**

```yaml
name: docker                  # must equal directory name
description: Manage docker containers on the home NUC
isolation: uds                # inprocess | uds (default: inprocess during migration)
provides:                     # handler names this function registers
  - docker.list
  - docker.status
requires:                     # what this function needs to run
  credentials: [DOCKER_API_TOKEN]
  env: [DOCKER_HOST]
  files: [ca.pem]
  packages: [docker]          # installed into habitat venv when isolation: uds
scheduled_jobs:               # optional — kernel scheduler materialises these as jobs
  - name: docker_health_sweep
    handler: docker.status
    cron: "*/5 * * * *"
```

**Handler signature (unchanged today).**

```python
from marcel_core.plugin import register

@register("docker.list")
async def list_containers(params: dict, user_slug: str) -> str:
    """Called by the agent via integration(id='docker.list', params={...})."""
    ...
```

**Runtime.**

- In-process today (kernel imports `<function>/__init__.py`, `@register` decorators populate the kernel registry).
- UDS-isolated after ISSUE-14b034 (kernel spawns the function as a subprocess, connects over UDS, handlers run in their own venv/heap).
- Dispatch is always via the `function` tool (today's `integration` tool is renamed — see Tool renaming below).

**Composition.**

- **Called by:** the main agent (via `function(id="docker.list", params=...)`), subagents (same tool name, inherited from parent unless disallowed in frontmatter), the scheduler (via `trigger_type: function` jobs).
- **Called by skills:** indirectly — a skill's `depends_on: [docker]` tells the agent that using `docker.*` requires the function's `requires:` to be satisfied.
- **Calls:** anything a python async function can call (HTTP APIs, subprocess, filesystem under `~/.marcel/users/{slug}/`, other functions via the same tool).

**Two function sub-shapes — do we need them?**

The user's capture mentioned "python-scripts" as a variant: a simple `.py` file with input/output but without the full `__init__.py` + `function.yaml` scaffolding. My recommendation is **no separate sub-shape** — the directory shape is simple enough (three files) and a flat `function.yaml` can reference a single handler in a single-file habitat. If a zoo keeper wants "just a script," they write a one-handler function habitat. One shape is easier to teach than two.

---

#### 2. Skills

**Purpose.** Markdown documents injected into the agent's system prompt on demand. Skills teach the agent *what it can do, when, and how* — progressive capability disclosure. The loader walks `<zoo>/skills/` and `<data_root>/skills/` (user customisations), reads frontmatter, and either ships `SKILL.md` or the `SETUP.md` fallback to the prompt based on whether requirements are met.

Skills carry **no python code**. They are pure instruction.

**Directory shape.**

```
skills/<name>/
├── SKILL.md             # required — loaded when requirements met
├── SETUP.md             # optional — loaded when requirements not met
└── components.yaml      # optional — a2ui component schemas
```

**Frontmatter (SKILL.md).**

```yaml
---
name: morning_digest
description: Summarise unread email, today's calendar, and news highlights for {user_slug}.
requires:                           # inline requirements
  credentials: [OPENAI_API_KEY]     # optional; depends_on covers most cases
  env: [TZ]
depends_on: [icloud, news]          # function names; inherits their requires: too
preferred_tier: standard            # local | fast | standard | power
---

# Morning digest

When the user asks for their morning digest...
```

**Runtime.** Loaded once per user at session startup (or on-demand when the classifier detects intent). No python execution.

**Composition.**

- **Called by:** the main agent's prompt builder (decides which skills are relevant to the current turn).
- **References:** functions via `depends_on:` (links the skill to the function's `requires:`) and/or describes inline usage patterns.
- **Not called by:** subagents directly — subagents have their own frontmatter-declared capabilities, not dynamic skill loading. Jobs never load skills (they're structured triggers).

---

#### 3. (Sub)agents

**Purpose.** A subagent is a bounded LLM invocation with its own system prompt, its own tool allowlist, and its own model choice. The main agent delegates to a subagent for tasks that benefit from a clean context (code review, long-form writing, focused research). Per the user's capture: *"they run using marcel's own harness as a subprocess."*

In pydantic-ai terms, a subagent is a separate `Agent` instance. In Marcel terms, the subagent's markdown file IS the system prompt; its frontmatter declares the wrapper agent configuration.

**Directory shape.**

```
agents/<name>.md             # flat file — markdown with YAML frontmatter
```

No directory per agent. No paired files. Every agent is one `.md`.

**Frontmatter.**

```yaml
---
name: code_reviewer
description: Senior code reviewer. Reviews a branch diff across correctness, security, performance.
model: anthropic:claude-sonnet-4-6  # or "inherit" to use the parent's tier
tools: [read_file, grep, glob, bash]  # allowlist — subagent sees only these
disallowed_tools: [delegate]         # denylist applied after allowlist
max_requests: 20                     # pydantic-ai request cap
timeout_seconds: 300
---

# System prompt body

You are a senior code reviewer with deep knowledge of…
```

**Runtime.**

- Invoked via `delegate(agent="code_reviewer", task="…")` from the main agent.
- The delegate tool spawns a fresh pydantic-ai `Agent` with the subagent's frontmatter applied (system_prompt = body, tools = filtered from the parent's pool, model = frontmatter).
- Note on "subprocess": today the subagent runs in the SAME python process as the parent agent (pydantic-ai Agent instance, not OS subprocess). The user's capture described it as "subprocess" — this might become literally true if we adopt the UDS pattern for agents too (see Open Questions), but it's not in this issue's scope.

**Composition.**

- **Called by:** the main agent via `delegate`.
- **Calls:** the tools in its allowlist — which usually includes `function` (so subagents can call functions), never includes `delegate` (no recursion), and maybe excludes admin tools (`bash`, `git_*`) for role-gated hygiene.
- **Not called by:** jobs (well, they CAN — see job trigger_types below), channels, skills.

---

#### 4. Channels

**Purpose.** Channels are I/O transports. They carry user messages into Marcel and Marcel's responses back out. Telegram today, Signal/Discord/iMessage potentially. Channels are bidirectional: the kernel mounts their webhook router for inbound; the kernel calls their push methods for outbound.

Channels carry both python code (transport logic, formatter) AND markdown (how the agent should format messages for this channel, e.g. "Telegram supports markdown and emojis"). That's why they're their own habitat kind: neither pure data nor pure function.

**Directory shape.**

```
channels/<name>/
├── __init__.py          # required — register_channel(plugin) at import
├── channel.yaml         # required — name, capabilities, requires
├── CHANNEL.md           # required — format hint injected into system prompt
├── <transport>.py       # bot.py, webhook.py, sessions.py, formatting.py, …
└── tests/
```

**`channel.yaml` schema.**

```yaml
name: telegram
description: Telegram bot channel for household members.
isolation: uds            # inprocess (today) | uds (after ISSUE-931b3f)
capabilities:
  rich_ui: true           # can render a2ui artifacts (Mini App)
  attachments: true       # can send images
  streaming: false        # Telegram doesn't stream responses
  markdown: telegram      # telegram-flavoured markdown
requires:
  credentials: [TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET]
  env: [MARCEL_PUBLIC_URL]
```

**Runtime.**

- At kernel startup, `channels.discover()` imports each channel habitat's `__init__.py`, which calls `register_channel(plugin)`.
- The kernel mounts `plugin.router` (FastAPI) for inbound webhooks.
- The kernel calls `plugin.send_message(user_slug, text)` / `send_photo` / `send_artifact_link` for outbound.
- Under ISSUE-931b3f (Phase 3 of f60b09), UDS channels will have a kernel-side HTTP→UDS proxy for inbound; outbound methods become UDS RPC calls.

**Composition.**

- **Called by:** the kernel (inbound — mounts router; outbound — calls push methods from notification code paths).
- **Reads:** user identity from `~/.marcel/users/*/profile.md` frontmatter (telegram session mapping).
- **Not called by:** the main agent directly (the agent never decides "send to channel X"; it returns text and the kernel's transport layer delivers).

---

#### 5. Jobs

**Purpose.** Scheduled triggers that wake Marcel up at a specific time (cron) or cadence (interval) to do something. Today every job wakes up the main agent with a system prompt. This issue proposes jobs gain an explicit `trigger_type` — dispatching to a function, a subagent, or the main agent — so deterministic scheduled tasks don't pay LLM cost.

Jobs carry **no python code**. They are declarative triggers that reference other habitats.

**Directory shape.**

```
jobs/<name>/
└── template.yaml        # the whole habitat is this one file
```

**`template.yaml` schema.**

Three trigger shapes, one common schema:

```yaml
# Shape A — call a function directly, no LLM
name: docker_health_sweep
description: Poll all running containers every 5 minutes, alert on unhealthy.
default_trigger:
  cron: "*/5 * * * *"
trigger_type: function
function: docker.list       # name from a function habitat's provides:
params:                      # static params passed to the function
  filter: running
notify: on_output            # always | on_failure | on_output | silent

# Shape B — run a subagent (bounded LLM turn)
name: morning_digest
description: Read unread email + today's calendar + news headlines, produce a summary.
default_trigger:
  cron: "0 7 * * *"
trigger_type: subagent
subagent: morning_digest_agent   # name from agents/
task: "Generate today's digest for {user_slug}."
notify: always

# Shape C — run the main agent (current default, back-compat)
name: evening_retro
description: Ask Marcel to reflect on the day's conversations.
default_trigger:
  cron: "0 22 * * *"
trigger_type: agent             # explicit; previously the only mode
system_prompt: "You are Marcel at end of day..."
task_template: "Reflect on {user_slug}'s day."
notify: silent
model: anthropic:claude-sonnet-4-6
```

**Runtime.**

- The kernel scheduler (in `src/marcel_core/jobs/scheduler.py`) fires the job at the configured time.
- Scheduler branches on `trigger_type`:
  - `function` → calls the function registry directly, result passed to `notify` logic.
  - `subagent` → spawns the named subagent via `delegate`, result passed to `notify` logic.
  - `agent` → current path — runs the main agent with the supplied system prompt.
- All three paths share the same retry/alerting/observability story today. This extends that story to the new trigger types.

**Composition.**

- **Called by:** the scheduler (cron/interval firings).
- **Calls:** one function OR one subagent OR the main agent.
- **Data:** `notify:` can deliver output through a channel (uses channel push methods).

---

### Composition — who references what

```
User message
    │
    ▼
[Channel (inbound)] ──→ Kernel ──→ Main Agent
                                       ├── [Skills] (injected into system prompt)
                                       ├── [Functions] (via `function` tool)
                                       ├── [Subagents] (via `delegate` tool)
                                       └── Kernel ──→ [Channel (outbound)]

Scheduler (cron/interval)
    │
    ▼
[Job] ──→ dispatches by trigger_type:
           ├── [Function]        (direct call, no LLM)
           ├── [Subagent]        (bounded LLM, own context)
           └── Main Agent        (full turn with all skills)
```

Cross-references:

- Skills reference Functions (via `depends_on:`).
- Subagents reference Functions (via `tools:` allowlist).
- Jobs reference Functions or Subagents (via `trigger_type` + name).
- Channels are orthogonal — they don't reference functions/skills/subagents; they just ferry messages.

No kind references Skills by name (skills are loaded by the kernel's prompt builder based on classifier signals — the agent doesn't "call" a skill).
No kind references Jobs by name (jobs are leaves — triggered, never referenced).

### The rename matrix

Every name change in one place:

| Today | Target | Scope |
|---|---|---|
| `<zoo>/integrations/` | `<zoo>/functions/` | marcel-zoo directory |
| `integration.yaml` | `function.yaml` | per-habitat file name |
| `marcel_core.skills.integrations` | `marcel_core.functions` | kernel module path |
| `integration` tool (agent-facing) | `function` tool | tool registration name |
| Skill frontmatter `depends_on: [icloud]` | unchanged (but resolves against `functions/`) | kernel loader logic |
| `@register("icloud.events")` | unchanged | public decorator name |
| `IntegrationHandler` type | `FunctionHandler` | kernel internal type |
| `IntegrationMetadata` | `FunctionMetadata` | kernel internal type |
| `HabitatRollback` exception | unchanged | kernel internal |
| `_marcel_ext_integrations.*` (sys.modules prefix) | `_marcel_ext_functions.*` | kernel internal |

**Back-compat aliases during migration:**

- Kernel accepts BOTH `integrations/` and `functions/` directory lookup during Phase 1–3.
- Kernel accepts BOTH `integration.yaml` and `function.yaml`.
- Agent-facing tool name `integration` is kept as an alias of `function` until all skills migrate.
- `from marcel_core.plugin import register` unchanged — the decorator name is stable.

All aliases removed in a final cleanup phase (mirrors ISSUE-807a26's single-pattern discipline).

### Kernel changes (marcel-core)

#### Module moves

| Current | New |
|---|---|
| `src/marcel_core/skills/integrations/__init__.py` | `src/marcel_core/functions/__init__.py` |
| `src/marcel_core/skills/integrations/` (package) | `src/marcel_core/functions/` (package) |
| `src/marcel_core/plugin/_uds_bridge.py` | unchanged (references updated) |
| `src/marcel_core/plugin/_uds_supervisor.py` | unchanged |

The rationale for moving the module out of `skills/`: today `marcel_core.skills.integrations` is a historical accident. Skills and integrations are distinct habitat kinds; the module nesting implies a relationship that no longer matches the taxonomy. Moving to `marcel_core.functions` makes the top-level kernel structure mirror the five-kind habitat taxonomy:

```
src/marcel_core/
├── functions/      ← function habitat loader + registry (was skills/integrations/)
├── skills/         ← skill habitat loader + prompt builder
├── agents/         ← subagent loader + delegate tool
├── plugin/
│   ├── channels.py ← channel habitat registry
│   ├── jobs.py     ← job template loader
│   ├── _uds_bridge.py
│   └── _uds_supervisor.py
└── ...
```

`marcel_core.plugin` remains the stable re-export surface (`from marcel_core.plugin import register, register_channel, …`). Zoo habitats never import from `marcel_core.functions` directly; they always go through `marcel_core.plugin`.

#### Tool renaming

The agent-facing tool today is called `integration`:

```python
integration(id="icloud.list_events", params={"range": "today"})
```

Renamed to `function`:

```python
function(id="icloud.list_events", params={"range": "today"})
```

Both names registered during migration; `integration` is marked deprecated in the tool's docstring (agent sees a gentle note). Phase-final, `integration` is removed.

#### Schema changes

- `function.yaml` schema: identical to today's `integration.yaml` (only the filename changes).
- `template.yaml` schema: add `trigger_type: function | subagent | agent` with default `agent` (preserves existing jobs unchanged).

#### Loader changes

- `functions.discover()` — walks BOTH `<zoo>/integrations/` and `<zoo>/functions/` during Phase 1–3; drops the integrations path in the final phase.
- Skill loader — `depends_on: [foo]` resolves via the unified functions registry (the registry itself is the same dict; only its module location changes).
- Scheduler — branches on `trigger_type` at job fire time.

#### Scheduler changes

```python
# src/marcel_core/jobs/scheduler.py (sketch)

async def _fire_job(job: JobDefinition):
    trigger = job.template.get("trigger_type", "agent")   # default preserves back-compat
    if trigger == "function":
        result = await _fire_function_job(job)
    elif trigger == "subagent":
        result = await _fire_subagent_job(job)
    elif trigger == "agent":
        result = await _fire_agent_job(job)                # current path unchanged
    else:
        raise ValueError(f"Unknown trigger_type: {trigger!r}")
    await _handle_notify(job, result)
```

Each `_fire_*` is a thin wrapper — function calls the registry, subagent invokes via delegate, agent is the current `Agent.run()` path.

### Zoo changes (marcel-zoo)

Per-habitat migration. Each habitat is one PR in marcel-zoo:

- `integrations/docker/` → `functions/docker/`, `integration.yaml` → `function.yaml`
- `integrations/icloud/` → `functions/icloud/`, same
- `integrations/news/` → `functions/news/`, same
- `integrations/banking/` → `functions/banking/`, same

Inside each migrated habitat:

- `from marcel_core.plugin import register` — unchanged (the decorator name stays).
- Handler functions — unchanged.
- YAML content — unchanged apart from filename.

**Jobs migration:** every existing zoo job gains an explicit `trigger_type: agent` (default-equivalent). If any jobs would be better served by `trigger_type: function` (e.g. a scheduled RSS fetch that doesn't need the LLM), they migrate in the same PR.

**Root `pyproject.toml`:** shrinks as functions migrate to UDS + per-habitat venvs (ISSUE-14b034 handles this).

### Documentation restructure

#### New canonical page: `docs/habitats.md`

The single source of truth for the taxonomy. Contains:

- The five kinds in one side-by-side comparison table.
- A "when do I add which kind?" decision flowchart.
- One minimal example per kind.
- Cross-links to per-kind deep-dive pages.

#### Existing pages to update

| Page | Change |
|---|---|
| `docs/plugins.md` | Rewritten as the function + channel deep-dive (removes skill/job content that migrates to per-kind pages). |
| `docs/skills.md` | Becomes the skill deep-dive; references `functions/` not `integrations/`. |
| `docs/jobs.md` (new or split from scheduler doc) | Deep-dive on the three trigger types. |
| `docs/agents.md` (new or split from plugins.md) | Deep-dive on subagent shape + delegate tool. |
| `docs/channels.md` (new or split from plugins.md) | Deep-dive on channel shape + bidirectional runtime. |
| `README.md` | Architectural decisions bullet updated to name the five kinds. |
| `SETUP.md` | `make zoo-setup` section references `functions/` not `integrations/`. |
| `CLAUDE.md` | Integration pattern summary (section "Integration pattern (summary)") rewritten as "Habitat taxonomy (summary)" pointing at the five kinds. |
| `.claude/rules/integration-pairs.md` | Renamed to `.claude/rules/function-skill-pairs.md` with updated vocabulary. |

#### mkdocs.yml nav

```yaml
nav:
  - Home: index.md
  - Habitats: habitats.md          ← new
    - Functions: plugins.md         ← rewritten
    - Skills: skills.md
    - Subagents: agents.md          ← new
    - Channels: channels.md         ← new
    - Jobs: jobs.md                 ← new
  - ...
```

### Testing

- `tests/core/test_plugin.py` → `tests/core/test_functions.py` (rename; update 23 test methods and their assertions).
- `tests/core/test_uds_integrations.py` → `tests/core/test_uds_functions.py`.
- New: `tests/jobs/test_trigger_types.py` — one test per trigger_type (function, subagent, agent).
- Back-compat tests: a job with no `trigger_type` defaults to `agent`; a zoo with `integrations/` folder is discovered during Phase 1–3.
- The fixture habitat at `tests/fixtures/uds_habitat/` is re-classified as `tests/fixtures/uds_function/` — the rename propagates to the test directory too.

### Relationship to UDS isolation work (f60b09 family)

This refactor MUST ship before ISSUE-14b034 (UDS Phase 2 — migrate zoo integrations to UDS). Reasoning:

- If UDS Phase 2 runs first, every integration habitat gets touched once (add `isolation: uds`) and then again (rename to function). Double churn, merge conflicts if both are in flight.
- If this refactor runs first, the rename is one PR per habitat; UDS Phase 2 then adds one line (`isolation: uds`) to the same habitat's `function.yaml`.

Proposed sequencing:

1. **This issue (ISSUE-3c1534)** — rename + trigger_type + docs. One merged state.
2. **ISSUE-14b034** (UDS Phase 2) — picks up the renamed structure and adds UDS isolation.
3. **ISSUE-931b3f** (UDS Phase 3 — channels + jobs) — unchanged scope; benefits from cleaner channel/job vocabulary.
4. **ISSUE-807a26** (UDS Phase 4 — remove in-process path) — unchanged scope.

### Migration strategy — phased

Each phase is independently shippable. `make check` green after every phase.

#### Phase 1 — kernel adds the new names as aliases

Back-compat only. Zoo untouched. Tests passing under both old and new names.

- Move `src/marcel_core/skills/integrations/` → `src/marcel_core/functions/` (package move).
- `marcel_core.skills.integrations` re-export the new module's symbols (deprecation warning in docstring).
- Loader `discover()` walks BOTH `integrations/` and `functions/` directories in the zoo.
- Loader reads BOTH `integration.yaml` and `function.yaml`.
- Tool registered under BOTH `integration` and `function` names.
- Deprecation warnings in logs whenever the old path is taken.

#### Phase 2 — jobs gain trigger_type

Kernel + tests only. Zoo jobs untouched (they default to `agent`).

- Scheduler branches on `trigger_type`.
- `_fire_function_job` and `_fire_subagent_job` implemented.
- New tests for each trigger type.

#### Phase 3 — zoo rename

One PR per habitat in marcel-zoo.

- `integrations/docker/` → `functions/docker/`, etc.
- Each PR self-contained; merge order doesn't matter.
- Existing jobs gain explicit `trigger_type: agent`; new jobs use the appropriate type.

#### Phase 4 — documentation

- `docs/habitats.md` authored.
- Per-kind deep-dive pages (some new, some split from existing).
- `mkdocs.yml` nav updated.
- `README.md`, `SETUP.md`, `CLAUDE.md` updated.
- `.claude/rules/` rules renamed and vocabulary-updated.

#### Phase 5 — deprecation cleanup

After enough soak time (e.g. one release cycle).

- Remove `integrations/` directory support.
- Remove `integration.yaml` filename support.
- Remove `integration` tool alias.
- Remove `marcel_core.skills.integrations` re-export shim.

Phases 1–4 each land as their own sub-issue for tracking. Phase 5 is a single final cleanup.

## Non-goals

Explicitly out of scope for this issue (and its sub-issues):

- **Changing the function handler signature** (`async def fn(params: dict, user_slug: str) -> str`). Typed params/returns via pydantic are a separate future discussion.
- **Changing how subagents run** (pydantic-ai delegate pattern stays; "subprocess" in the user's capture is aspirational, not structural).
- **Changing how channels are discovered or mount routers.** The bidirectional transport model stays.
- **Introducing per-habitat isolation modes for agents or skills.** Skills are markdown; subagents are pydantic-ai Agent instances in the kernel process.
- **Multi-tenant / third-party-habitat security model** (capability tokens, per-habitat credential scoping). Only relevant if and when Marcel runs untrusted zoos; out of scope here.
- **Typed tool returns** (str → typed pydantic models). Worth doing eventually but orthogonal.
- **Removing the `@register` decorator** in favour of a class-based habitat API. Stability win > cosmetic win.
- **Backporting to old marcel releases.** This is a breaking-change migration; no shim in old kernels.

## Tasks

Phase 1 — kernel aliases:

- [ ] Move `src/marcel_core/skills/integrations/` → `src/marcel_core/functions/` (git mv preserves history)
- [ ] Create `src/marcel_core/skills/integrations/__init__.py` as a re-export shim with a DeprecationWarning
- [ ] Update `marcel_core.plugin.__init__` re-exports to point at the new module
- [ ] Loader: walk BOTH `<zoo>/integrations/` and `<zoo>/functions/` during discovery
- [ ] Loader: read BOTH `integration.yaml` and `function.yaml` per habitat
- [ ] Tool: register under BOTH `integration` and `function` names
- [ ] Add DeprecationWarning in logs when the old path is taken
- [ ] Tests: rename `test_plugin.py` → `test_functions.py`; add a back-compat test asserting the old path still works
- [ ] `make check` green

Phase 2 — jobs trigger_type:

- [ ] Extend `JobDefinition` / `template.yaml` schema with `trigger_type: function | subagent | agent` (default `agent`)
- [ ] Implement `_fire_function_job` — calls the function registry directly, result fed into notify
- [ ] Implement `_fire_subagent_job` — invokes via `delegate`, result fed into notify
- [ ] Refactor existing `_fire_job` to branch on `trigger_type`
- [ ] Add tests in `tests/jobs/test_trigger_types.py` — one per trigger type, plus a default-preserving test
- [ ] Document the three trigger types in `docs/jobs.md` (created in Phase 4)
- [ ] `make check` green

Phase 3 — zoo rename (separate PRs in marcel-zoo):

- [ ] `functions/docker/` — rename + verify
- [ ] `functions/icloud/` — rename + verify
- [ ] `functions/news/` — rename + verify
- [ ] `functions/banking/` — rename + verify
- [ ] Existing zoo jobs gain explicit `trigger_type: agent`
- [ ] Zoo root `pyproject.toml` entries for migrated integrations are dropped (deferred until UDS Phase 2 anyway)
- [ ] `make check` from the kernel green with the renamed zoo

Phase 4 — documentation:

- [ ] Author `docs/habitats.md` — the single canonical taxonomy page
- [ ] Split/rewrite `docs/plugins.md` as the function deep-dive
- [ ] Author `docs/agents.md` — subagent deep-dive
- [ ] Author `docs/channels.md` — channel deep-dive
- [ ] Author `docs/jobs.md` — job trigger-type deep-dive
- [ ] Update `docs/skills.md` vocabulary
- [ ] Update `README.md` "Architectural decisions" bullet
- [ ] Update `SETUP.md` `make zoo-setup` references
- [ ] Update `CLAUDE.md` Integration pattern → Habitat taxonomy
- [ ] Rename `.claude/rules/integration-pairs.md` → `.claude/rules/function-skill-pairs.md` with updated vocabulary
- [ ] Update `mkdocs.yml` nav
- [ ] `docs-build --strict` green

Phase 5 — deprecation cleanup (file as its own sub-issue after soak):

- [ ] Remove `integrations/` directory support from the loader
- [ ] Remove `integration.yaml` filename support
- [ ] Remove `integration` tool alias
- [ ] Remove the `marcel_core.skills.integrations` re-export shim
- [ ] Grep the zoo for any remaining `integration` references — expected zero
- [ ] `make check` green

Overall:

- [ ] File sub-issues for Phase 1, 2, 3, 4 (5 is post-soak)
- [ ] Coordinate with ISSUE-14b034 — this work must merge first
- [ ] `/finish-issue` → merged close commit on main (when all phases done)

## Relationships

- **Blocks:** [[ISSUE-14b034]] (UDS Phase 2 — migrate zoo functions to UDS). That issue's per-habitat work is simpler if the rename has already landed.
- **Clarifies:** [[ISSUE-f60b09]] (UDS Phase 1 — kernel mechanism). The UDS mechanism is one dimension (runtime isolation); this issue is another dimension (habitat kind). They compose cleanly.
- **Related:** [[ISSUE-931b3f]] (UDS Phase 3 — channels + jobs). Channel habitats' `isolation: uds` and this issue's trigger_type schema are independent; they can ship in either order.
- **Related:** [[ISSUE-807a26]] (UDS Phase 4 — remove in-process path). The deprecation-cleanup Phase 5 of this issue rhymes with f60b09's Phase 4 — both remove back-compat after soak.
- **Supersedes:** the implicit "integrations are different from skills" mental model that has caused friction in every recent zoo issue.

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Zoo forks break on rename | Medium | Medium | Phase 1 kernel supports both `integrations/` and `functions/` directory names; forks migrate at their own pace during soak |
| Skills referencing `integration(id=...)` in their markdown break | High | Low | Tool `integration` stays registered as an alias during Phases 1–4; soak period allows skill docs to catch up |
| Docs churn creates broken cross-references | Medium | Low | Phase 4 is one coherent PR that rewrites all affected pages together, not piecemeal |
| `trigger_type: function` returns a raw string that's useless to the user | Low | Medium | Existing `notify: on_output` logic delivers the raw string via the channel — same as today's `agent` trigger returning the agent's final message |
| `trigger_type: subagent` spawns a subagent for what should have been an agent turn | Low | Low | Documentation makes clear: subagent is for bounded LLM cost; agent is for full capability |
| Phase 3 zoo PRs merge out of order, leaving zoo mid-migration | Medium | Low | Kernel back-compat tolerates mixed `integrations/` and `functions/` during the whole migration |
| Someone adds a new habitat kind we didn't foresee | Low | Medium | The five-kind taxonomy is curated, not open. Adding a sixth requires its own design issue; ad-hoc kinds violate "generic over specific" |

## Open questions

Left open for the user's call before Phase 1 implementation begins:

1. **Module location: `marcel_core.functions` vs `marcel_core.plugin.functions`?**
   - `marcel_core.functions` makes the top-level package structure mirror the habitat taxonomy (one directory per kind).
   - `marcel_core.plugin.functions` keeps everything plugin-related under one namespace.
   - Recommendation: `marcel_core.functions` for clarity of intent. The stable import surface is `marcel_core.plugin` re-exports, so zoo habitats don't see the internal path anyway.

2. **Decorator rename: `@register` vs `@function`?**
   - `@register` is the current name, decorator-style, zoo habitats use it.
   - `@function` would be more aligned with the new taxonomy but breaks every current habitat import.
   - Recommendation: keep `@register`. Renaming a decorator for cosmetic alignment is low-value and high-churn.

3. **Should `python-script` be a sub-shape of function?**
   - The user's capture mentioned "python-scripts" as a variant.
   - Proposed above: no — one function shape is enough; a single-handler habitat IS a python script.
   - If there's a real use case for "bare .py file, no function.yaml, no __init__.py" — surface it now, otherwise drop it.

4. **Should we introduce a `Habitat` base type / Protocol?**
   - Pro: common discovery logic (walk dir, read YAML, validate, register).
   - Con: the five kinds genuinely differ (python module vs flat markdown vs yaml-only).
   - Recommendation: no common type. Per-kind loaders are clearer than a layered abstraction they barely share.

5. **Should jobs with `trigger_type: subagent` reference the subagent by file path or by name?**
   - By name (`subagent: morning_digest_agent`) matches how delegate works — one name, resolved via the agents loader.
   - By path would allow jobs to reference an inline subagent definition, but that collides with the "jobs are yaml-only" principle.
   - Recommendation: by name, symmetrical with `delegate(agent="...")`.

6. **Timing vs ISSUE-14b034.**
   - Recommendation: this issue FIRST, then 14b034 picks up the renamed structure. Avoids double-churn on every zoo habitat.

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
