# Habitats — the five kinds

Marcel's kernel ships **no behaviour**. Everything Marcel can *do* — call a
calendar API, read RSS feeds, schedule a morning digest, delegate a plan
to a subagent, receive a Telegram webhook — lives in a **habitat**: a
directory under [`$MARCEL_ZOO_DIR`](https://github.com/shbunder/marcel/blob/main/SETUP.md)
that the kernel discovers at startup.

There are exactly five kinds of habitat. Everything else in these docs
(the toolkit, skills, channels, jobs, subagents) is a specialisation of
one kind. Read this page first; the per-kind deep-dives make much more
sense once you know where they sit in the taxonomy.

## Overview

| Kind | Directory | Artefact | Discovered by | What it contains |
|---|---|---|---|---|
| **Toolkit** | `toolkit/<name>/` | `__init__.py` + `toolkit.yaml` | [`ToolkitHabitat.discover_all`](#) | Python handlers registered with `@marcel_tool("<name>.<action>")`. The *executable* layer. |
| **Skill** | `skills/<name>/` | `SKILL.md` + optional `SETUP.md` | [`SkillHabitat.discover_all`](#) | Markdown that teaches the agent *when* to reach for a tool. The *prompting* layer. |
| **Subagent** | `agents/<name>.md` | single Markdown file | [`SubagentHabitat.discover_all`](#) | Named, scoped agents (with their own tool filter + model) the main agent can `delegate()` to. |
| **Channel** | `channels/<name>/` | `__init__.py` + `channel.yaml` | [`ChannelHabitat.discover_all`](#) | Bidirectional transports: FastAPI router for inbound webhooks + `send_message` / `send_photo` / friends for outbound push. |
| **Job** | `jobs/<name>/template.yaml` | YAML + optional scripts | [`JobHabitat.discover_all`](#) | Scheduled background work: cron / interval / event / oneshot triggers, run by the executor under one of three *dispatch types*. |

(The `discover_all` links are placeholders until the per-kind deep-dives
land — for now, read the source in
[`src/marcel_core/plugin/habitat.py`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/plugin/habitat.py).)

## Pick your habitat

A decision aid for *"I want Marcel to do X"*.

```text
Does it involve running Python code?
├── Yes → Is it deterministic (no LLM)?
│        ├── Yes, always  → toolkit habitat
│        └── Sometimes    → toolkit habitat (handler) + skill habitat (agent prompting)
│
└── No  → Is it a scheduled background task?
         ├── Yes                       → job habitat
         │   (handler lives in a toolkit; trigger lives in the job)
         │
         └── No, it's conversational content → skill habitat
             (or, if the agent needs a scoped sub-pass: subagent habitat)

Does it receive external messages (webhooks, websockets, email, SMS)?
└── Yes → channel habitat (bidirectional transport)
```

Real features usually span **two** habitats: a toolkit park for the code
plus a skill park teaching the agent when to call it. A channel habitat
is transport-shaped and doesn't pair; a subagent habitat is standalone.

## Minimal example per kind

The shortest thing that could possibly work, for each kind.

### Toolkit — `toolkit/demo/`

```python
# toolkit/demo/__init__.py
from marcel_core.plugin import marcel_tool

@marcel_tool("demo.ping")
async def ping(params: dict, user_slug: str) -> str:
    return "pong"
```

```yaml
# toolkit/demo/toolkit.yaml
name: demo
description: A minimal toolkit habitat
provides: [demo.ping]
requires: {}
```

### Skill — `skills/demo/`

```markdown
---
name: demo
description: Teach the agent about the demo toolkit
depends_on: [demo]
---

# Demo

When the user says "ping", call `toolkit(id="demo.ping", params={})` and
quote the result back.
```

### Subagent — `agents/explore.md`

```markdown
---
name: explore
description: Read-only codebase exploration
model: anthropic:claude-haiku-4-5-20251001
tools: [read_file, list_dir, grep]
max_requests: 10
timeout_seconds: 300
---

You are a read-only exploration agent. Find relevant files and summarise
without editing anything. Return file paths and line ranges.
```

### Channel — `channels/demo/`

```python
# channels/demo/__init__.py
from fastapi import APIRouter
from marcel_core.plugin import register_channel
from marcel_core.plugin.channels import ChannelCapabilities, ChannelPlugin

router = APIRouter(prefix="/demo", tags=["demo"])

@router.post("/webhook")
async def webhook(payload: dict) -> dict:
    return {"ok": True}

register_channel(
    ChannelPlugin(
        name="demo",
        router=router,
        capabilities=ChannelCapabilities(rich_ui=False, attachments=False),
        send_message=None,
    )
)
```

```yaml
# channels/demo/channel.yaml
name: demo
description: A minimal channel habitat
```

### Job — `jobs/ping_sweep/template.yaml`

```yaml
description: Call the demo.ping handler every 30 minutes
default_trigger:
  type: interval
  interval_seconds: 1800
notify: silent
model: anthropic:claude-haiku-4-5-20251001

# Optional (ISSUE-ea6d47): picks the dispatch shape.
# Omitted ⇒ 'agent' (full main-agent turn).
dispatch_type: tool
tool: demo.ping
tool_params: {}

# system_prompt is required by the schema but ignored for dispatch_type=tool.
system_prompt: unused — dispatch_type is tool
```

## Composition — how habitats reference each other

Habitats reference each other **by name**, uniformly. A skill's
`depends_on: [banking]` resolves to the `banking` toolkit habitat. A
job's `dispatch_type: tool`, `tool: banking.sync` resolves to the
`banking` toolkit's `banking.sync` handler. A subagent's
`tools: [toolkit]` allows it to call the toolkit dispatcher — access to
individual handlers is controlled by the skill layer's `depends_on`.

Cross-reference diagram (who-calls-what):

```text
User message (Telegram, WebSocket, CLI)
    │
    ▼
Channel habitat ── inbound webhook ──► kernel harness
    ▲
    │ outbound send_message
    │
Harness turn ── reads ──► Skill habitats (SKILL.md in system prompt)
                          │
                          │ "call toolkit(id=X)" ──► Toolkit habitat (handler)
                          │
                          │ "delegate(subagent=Y)" ──► Subagent habitat
                          │
                          └── schedule ──► Job habitat
                                           │
                                           │ dispatch_type=tool ──► Toolkit (handler)
                                           │ dispatch_type=subagent ──► Subagent
                                           └ dispatch_type=agent ──► Full main-agent turn
```

The kernel wrappers in
[`src/marcel_core/plugin/habitat.py`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/plugin/habitat.py)
([`ISSUE-5f4d34`](https://github.com/shbunder/marcel/blob/main/project/issues/closed/ISSUE-260422-5f4d34-habitat-protocol-orchestrator.md))
provide the uniform `Habitat` Protocol — `kind`, `name`, `source` — over
all five kinds so discovery, logging, and admin tooling treat them
uniformly.

## Cross-links to per-kind deep dives

Richer material lives in the kind-specific pages. (During the Phase 4
docs rewrite these will become dedicated deep-dives; the links below
resolve to the current pages for now.)

| Kind | Deep dive |
|---|---|
| Toolkit | [Plugin API](plugins.md) |
| Skill | [Skills](skills.md) |
| Subagent | [Subagents](subagents.md) |
| Channel | [Telegram](channels/telegram.md) (one concrete example; a kind-level page is pending) |
| Job | [Jobs](jobs.md) |

## Further reading

- [Architecture](architecture.md) — where habitats fit in the kernel as a whole.
- [Self-modification](self-modification.md) — how Marcel rewrites its own habitats safely.
- [Storage](storage.md) — per-user data vs. system config (habitats must never cross the boundary).
