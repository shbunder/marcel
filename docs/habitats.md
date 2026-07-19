# Habitats — the five kinds

Marcel's kernel ships **no behaviour**. Everything Marcel can *do* — call a
calendar API, read RSS feeds, schedule a morning digest, delegate a plan
to a subagent, receive a Telegram webhook — lives in a **habitat**: a
directory under [`$MARCEL_ZOO_DIR`](https://github.com/shbunder/marcel/blob/main/SETUP.md)
that the kernel discovers.

There are exactly five kinds of habitat. Everything else in these docs
(skills, connectors, channels, jobs, subagents) is a
specialisation of one kind. Read this page first; the per-kind deep-dives make much more
sense once you know where they sit in the taxonomy.

> **Extensions.** A newer, unifying entrypoint — a module exposing
> `def register(marcel)` — lets one extension register any of these kinds
> through a single object, behind a versioned `marcel-sdk` import wall. It
> coexists with the per-kind loaders below. See
> [Extensions](extensions.md).

> **Where did toolkits go?** The former sixth kind — the toolkit habitat,
> in-process Python handlers behind a `toolkit(id=…)` dispatcher — retired in
> favour of connectors (FEAT-260718-c232d9). See
> [Toolkit habitats — retired](plugins.md) for the migration path.

## Overview

| Kind | Directory | Artefact | Deep dive | What it contains |
|---|---|---|---|---|
| **Connector** | `connectors/<name>/` | `connector.yaml` + optional `SETUP.md` + optional bundled server | [Connectors](connectors.md) | An MCP server plus its per-user auth. The *integration* layer — every capability that talks to the outside world, whether the server is third-party or bundled in the park. Each family member calls with their own credentials. |
| **Skill** | `skills/<name>/` | `SKILL.md` + optional `SETUP.md` | [Skills](skills.md) | Markdown that teaches the agent *when* to reach for a tool. The *prompting* layer. |
| **Subagent** | `agents/<name>.md` | single Markdown file | [Agents](agents.md) | Named, scoped agents (with their own tool filter + model) the main agent can `delegate()` to. |
| **Channel** | `channels/<name>/` | `__init__.py` + `channel.yaml` | [Channels](channels.md) | Bidirectional transports: FastAPI router for inbound webhooks + `send_message` / `send_photo` / friends for outbound push. |
| **Job** | `jobs/<name>/template.yaml` | YAML + optional scripts | [Jobs](jobs.md) | Scheduled background work: cron / interval / event / oneshot triggers, run by the executor under one of three *dispatch types*; agent jobs declare `skills:`/`connectors:` to run scoped and lean. |

Channel, skill, subagent, and job habitats are discovered **eagerly at
startup** through the uniform surface in
[`src/marcel_core/plugin/habitat.py`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/plugin/habitat.py)
— each of those kinds has a `*Habitat.discover_all()` classmethod so the
orchestrator, logging, and admin tooling treat them identically.
**Connectors resolve per user at capability-build time** instead: which
connectors a family member sees (and with whose credentials) is decided when
their agent is built, not once at startup.

## Pick your habitat

A decision aid for *"I want Marcel to do X"*.

```text
Does it talk to an external service, run real code, or need credentials?
├── Yes → connector habitat (an MCP server + per-user auth)
│         Usually paired with a skill habitat that teaches the agent
│         when and how to use the connector's tools.
│
└── No  → Is it a scheduled background task?
         ├── Yes                       → job habitat
         │   (deterministic work: dispatch_type: tool referencing a
         │    connector tool; trigger lives in the job)
         │
         └── No, it's conversational content → skill habitat
             (or, if the agent needs a scoped sub-pass: subagent habitat)

Does it receive external messages (webhooks, websockets, email, SMS)?
└── Yes → channel habitat (bidirectional transport)
```

Real features usually span **two** habitats: a connector park for the tools
plus a skill park teaching the agent when to call them. A channel habitat
is transport-shaped and doesn't pair; a subagent habitat is standalone.

## Minimal example per kind

The shortest thing that could possibly work, for each kind.

### Connector — `connectors/demo/`

```yaml
# connectors/demo/connector.yaml
name: demo
description: A minimal connector habitat
server:
  transport: inprocess
  module: server.py
auth:
  mode: none
  per_user: false
```

```python
# connectors/demo/server.py
from fastmcp import FastMCP

mcp = FastMCP('demo')

@mcp.tool
def ping() -> str:
    return 'pong'
```

See [Connectors](connectors.md) for transports (`http` / `stdio` /
`inprocess`), auth modes, and the trust model.

### Skill — `skills/demo/`

```markdown
---
name: demo
description: Teach the agent about the demo connector
metadata:
  marcel-connectors: demo
---

# Demo

When the user says "ping", call the `ping` tool and quote the result back.
```

### Subagent — `agents/explore.md`

```markdown
---
name: explore
description: Read-only codebase exploration
model: anthropic:claude-haiku-4-5-20251001
tools: [read_file, list_directory, search_files]
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
description: Call the demo connector's ping tool every 30 minutes
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
`metadata.marcel-connectors: banking` resolves to the `banking`
**connector** habitat — loading the skill activates the connector's tools in
the same step. A job's `dispatch_type: tool`, `tool: banking.sync` resolves
to the `banking` connector's `sync` tool (`<connector>.<tool>`). A
subagent's `tools:` frontmatter names the kernel tools it may use.

Cross-reference diagram (who-calls-what):

```text
User message (Telegram, WebSocket, CLI)
    │
    ▼
Channel habitat ── inbound webhook ──► kernel harness
    ▲
    │ outbound send_message
    │
Harness turn ── reads ──► Skill habitats (SKILL.md via load_capability)
                          │
                          │ activates tools of ──► Connector habitat (MCP server)
                          │
                          │ "delegate(agent_name=Y)" ──► Subagent habitat
                          │
                          └── schedule ──► Job habitat
                                           │
                                           │ dispatch_type=tool ──► Connector (MCP tool)
                                           │ dispatch_type=subagent ──► Subagent
                                           └ dispatch_type=agent ──► Full main-agent turn
```

The kernel wrappers in
[`src/marcel_core/plugin/habitat.py`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/plugin/habitat.py)
(`ISSUE-5f4d34`, marcel-admin board archive)
provide the uniform `Habitat` Protocol — `kind`, `name`, `source` — over
the eagerly-discovered kinds so discovery, logging, and admin tooling treat
them uniformly; connectors join the picture per user at capability-build
time.

## Cross-links to per-kind deep dives

Richer material lives in the kind-specific pages.

| Kind | Deep dive |
|---|---|
| Connector | [Connectors](connectors.md) |
| Skill | [Skills](skills.md) |
| Subagent | [Agents](agents.md) |
| Channel | [Channels](channels.md) (kind-level) • [Telegram](channels/telegram.md) (one concrete example) |
| Job | [Jobs](jobs.md) |

## Further reading

- [Architecture](architecture.md) — where habitats fit in the kernel as a whole.
- [Self-modification](self-modification.md) — how Marcel rewrites its own habitats safely.
- [Storage](storage.md) — per-user data vs. system config (habitats must never cross the boundary).
- [Toolkit habitats — retired](plugins.md) — the former sixth kind and its migration path.
