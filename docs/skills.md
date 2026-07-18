# Skills & Toolkit habitats

This page covers the **skill habitat** (agent prompting) and its pairing
with the **toolkit habitat** (Python handlers). See
[Habitats](habitats.md) for the full five-kind taxonomy.

Marcel exposes two primary tools to the agent, plus a framework-managed
`load_capability` tool (see
[Deferred-capability disclosure](#deferred-capability-disclosure)):

1. **`toolkit`** — call registered handlers (iCloud, HTTP APIs, shell
   commands). The `integration` name is still accepted as a back-compat
   alias during Phases 1–4 of `ISSUE-3c1534` (marcel-admin board archive).
2. **`marcel`** — internal utilities: `search_conversations`, `compact`,
   `notify`, `list_models`, `get_model`, `set_model`, `render`. (Memory has
   its own tools — `write_memory` / `read_memory` / `search_memory` /
   `delete_memory` from the Memory capability, FEAT-260718-30d45a.)

Toolkit handlers can be defined as:

- **Python modules** with `@marcel_tool` decorators — for handlers that
  need custom logic (API clients, stateful connections).
- **JSON entries** in `skills.json` — for simple HTTP calls or shell
  commands.

Skill documentation lives in `<root>/skills/<name>/SKILL.md`. A skill is a
folder with a `SKILL.md` following the [agentskills.io](https://agentskills.io)
open standard. Each visible skill becomes a **deferred pydantic-ai
Capability** rather than being pasted wholesale into the system prompt — see
[Deferred-capability disclosure](#deferred-capability-disclosure).

### Where skills are discovered — the three-root chain

Skills are collected **per user** from three roots, in order of increasing
specificity. A skill named the same in a more-specific root **shadows** the
same-named skill in a less-specific one (the shadowing is logged):

1. **`<MARCEL_ZOO_DIR>/skills/`** — global habitats shared by every user
   (source `zoo-global`; skipped when `MARCEL_ZOO_DIR` is unset). See
   [Toolkit habitats](plugins.md).
2. **`<MARCEL_ZOO_DIR>/users/<slug>/skills/`** — git-managed per-user
   habitats (source `zoo-user`).
3. **`<MARCEL_DATA_DIR>/users/<slug>/skills/`** — runtime-installed
   per-user skills (source `data-user`).

There is no global `<MARCEL_DATA_DIR>/skills/` root — global skills come from
the zoo. To override a zoo-shipped skill for one user, drop a same-named
skill into that user's `zoo-user` or `data-user` root; being more specific,
it wins.

## How it works

1. The agent receives a user request (e.g. "what's on my calendar?").
2. Its prompt carries a compact **catalog** of the available skills (each
   skill's name + description, ~100 tokens each) — not the full bodies.
3. It calls `load_capability("icloud")` to pull the `icloud` skill's
   `SKILL.md` body into context as that capability's instructions.
4. It calls `toolkit(id="icloud.calendar", params={"days_ahead": "7"})`.
5. The executor dispatches to the right handler (python function, HTTP call, or shell command).
6. The result is returned as plain text to the agent.

## Deferred-capability disclosure

Skills are exposed to the model as **deferred pydantic-ai Capabilities**
(`defer_loading=True`). The framework does not paste every skill body into
the prompt. Instead:

- The prompt carries a compact **catalog** — one entry per visible skill,
  each being the skill's id (its `name`) plus its `description` (~100 tokens
  each).
- The framework adds a managed **`load_capability`** tool. When the model
  calls `load_capability("<skill-name>")`, that skill's `SKILL.md` body is
  returned as the capability's instructions and stays in the message history
  (surviving compaction).
- Each loaded skill owns a **`read_skill_resource(resource)`** tool scoped to
  its own directory. This tool only appears in the tool list *after* the
  skill is loaded, and only reads files inside that skill's folder (e.g.
  `feeds.yaml`, `components.yaml`, `SETUP.md`).

There is no `# Skills — what you can do` block in the system prompt and no
auto-injection of a skill's docs on the first toolkit call — the model loads
a skill via `load_capability` before calling its toolkit. A `/<skillname>`
slash command force-loads that skill's capability eagerly, so its body is in
the prompt from the first request.

### Role gating

A skill whose frontmatter sets `metadata.marcel-role: admin` is dropped from
the catalog for non-admin users — never shown, never loadable. Skills
without a `marcel-role` are visible to everyone.

## Frontmatter — the agentskills.io standard

A skill's `SKILL.md` opens with YAML frontmatter conforming to the
[agentskills.io](https://agentskills.io) open standard. Exactly two keys are
required:

| Key | Rules |
|---|---|
| `name` | 1–64 chars, lowercase alphanumerics and single hyphens. **Must equal the skill's directory name.** |
| `description` | 1–1024 chars. Shown in the skill catalog; this is what the model reads to decide whether to load the skill. |

Any other **top-level** frontmatter key is tolerated but ignored. Marcel's
own extensions never sit at the top level — they live in the spec-legal
`metadata:` map (a flat string→string map) under `marcel-*` keys:

| `metadata` key | Value (string) | Purpose |
|---|---|---|
| `marcel-connectors` | comma-separated toolkit names, e.g. `banking,news` | Toolkit habitats this skill fronts; their `requires:` blocks become the skill's requirements. |
| `marcel-tier` | `fast` \| `standard` \| `power` \| `local` | Preferred model tier while this skill is loaded (see [Model tiers](model-tiers.md)). |
| `marcel-role` | `admin` | Role-gates the skill — dropped from the catalog for non-admin users. |
| `marcel-requires-credentials` | comma-separated keys, e.g. `MY_API_KEY` | Credentials that must exist in the user's store. |
| `marcel-requires-env` | comma-separated vars, e.g. `SOME_ENV_VAR` | Environment variables that must be set. |

Example — a skill that fronts the `banking` toolkit:

```yaml
---
name: banking
description: Access the user's linked bank accounts — balances, transactions, spending insights
metadata:
  marcel-connectors: banking
---
```

!!! note "Legacy keys — deprecated but tolerated"
    Older skills declared `depends_on:` (a YAML list), `preferred_tier:`, and
    a `requires:` block (`{role, credentials, env}`) as **top-level** keys.
    The loader still migrates these — emitting a deprecation warning — but new
    and migrated skills use `metadata.marcel-*`. The mapping:

    | Legacy top-level key | New `metadata` key |
    |---|---|
    | `depends_on: [banking]` | `marcel-connectors: banking` |
    | `preferred_tier: power` | `marcel-tier: power` |
    | `requires: {role: admin}` | `marcel-role: admin` |
    | `requires: {credentials: [...]}` | `marcel-requires-credentials: ...` |
    | `requires: {env: [...]}` | `marcel-requires-env: ...` |

## The skill shapes

A skill's frontmatter determines how the loader treats it and whether a
`SETUP.md` fallback is meaningful. There are three distinct shapes:

### 1. Standalone — pure teaching material, no requirements

The simplest shape: no `marcel-connectors`, no `marcel-requires-*`. The
`SKILL.md` body is served whenever the skill is loaded because it documents
built-in kernel tools (e.g. the `web`, `memory`, or `jobs` utility action
families) or pure domain knowledge. No `SETUP.md` is needed because there is
nothing to set up.

```yaml
---
name: memory
description: Manage conversation memory — search past conversations, recall facts
---
```

Standalone skills live in `<MARCEL_ZOO_DIR>/skills/<name>/` alongside every other skill habitat. The kernel ships none of them by default.

### 2. Self-contained — `marcel-requires-*`

A skill that needs credentials or environment variables but has no paired
toolkit handler — for instance, a skill that teaches the agent how to use a
tool whose credentials are read directly at call time. Declare the
dependencies in `metadata`; they drive SKILL.md → SETUP.md switching.

```yaml
---
name: myservice
description: What myservice does
metadata:
  marcel-requires-credentials: MY_API_KEY
  marcel-requires-env: SOME_ENV_VAR
---
```

### 3. Toolkit-backed — `marcel-connectors`

The typical case for any skill that calls `toolkit(id="...")`. Rather than
duplicating the toolkit's requirements, name the toolkit(s) it fronts:

```yaml
---
name: docker
description: Manage Docker containers
metadata:
  marcel-connectors: docker
---
```

The loader looks up `<MARCEL_ZOO_DIR>/toolkit/docker/toolkit.yaml`, reads its `requires:` block, and treats those as the skill's requirements. This keeps the credential / env list in one place — the toolkit's `toolkit.yaml` — and avoids drift between the handler and its skill doc. See [Toolkit habitats → Metadata](plugins.md#metadata).

Forms combine: a skill's effective requirements are the union of its
`marcel-requires-*` keys and every `marcel-connectors` toolkit's `requires:`.

## Skill fallback (SETUP.md)

A skill's `SETUP.md` (shapes 2 and 3) activates when the skill's requirements
are not met. This guides new users through first-time setup rather than
failing silently. Standalone skills (shape 1) do not need a `SETUP.md`.

When all requirements are met — or there are none — loading the skill serves
`SKILL.md`. When any are missing — a missing credential or env var, or a
`marcel-connectors` toolkit that isn't configured (zoo not loaded or
`toolkit.yaml` missing) — loading it serves `SETUP.md` instead, and the
skill's catalog description is suffixed "— needs setup".

## Adding a Python toolkit habitat

Toolkit habitats live in marcel-zoo: `<MARCEL_ZOO_DIR>/toolkit/<name>/__init__.py` (plus `toolkit.yaml`), installable components of marcel-zoo. See [Plugins](plugins.md) for the full habitat contract. The kernel ships zero bundled toolkits — every real toolkit lives in the zoo.

Habitats must use `from marcel_core.plugin import marcel_tool` (the stable plugin surface) and obey the directory-name ↔ handler-namespace rule: a toolkit at `.../toolkit/myservice/` may only register `myservice.*` handlers; handlers outside that namespace cause the whole habitat to be rolled back.

```python
import json
from marcel_core.plugin import marcel_tool

@marcel_tool("myservice.action")
async def action(params: dict, user_slug: str) -> str:
    """Each handler receives string params and the user slug."""
    value = params.get("key", "default")
    # ... do work ...
    return json.dumps(result, indent=2)
```

Then create the paired skill habitat at `<MARCEL_ZOO_DIR>/skills/myservice/SKILL.md`:

```markdown
---
name: myservice
description: Short description of what myservice does
metadata:
  marcel-connectors: myservice
---

You have access to the `integration` tool to interact with myservice.

## Available commands

### myservice.action

Description of what this does.

\`\`\`
toolkit(id="myservice.action", params={"key": "value"})
\`\`\`

| Param | Type   | Required | Default | Description          |
|-------|--------|----------|---------|----------------------|
| key   | string | no       | default | What this param does |

Returns: description of the response format.
```

And a setup fallback at `<MARCEL_ZOO_DIR>/skills/myservice/SETUP.md`:

```markdown
---
name: myservice
description: Guide the user through setting up myservice
---

The user is asking about myservice, but it is **not yet configured**.

## How to set up myservice

[Step-by-step instructions for the user...]
```

No changes to kernel code are needed — the toolkit module is auto-discovered at startup, the skill habitat is loaded from `<MARCEL_ZOO_DIR>/skills/` automatically, and `marcel-connectors` resolves the credentials/env block from the toolkit's `toolkit.yaml`.

## Adding a JSON skill (HTTP or shell)

For simple integrations that don't need custom Python logic, add an entry to `src/marcel_core/skills/skills.json`:

### HTTP skill

```json
{
  "weather.current": {
    "description": "Get the current weather for a city",
    "method": "GET",
    "url": "https://api.openweathermap.org/data/2.5/weather",
    "auth": {
      "type": "api_key",
      "env_var": "OPENWEATHER_API_KEY",
      "location": "query",
      "param_name": "appid"
    },
    "params": {
      "q":     { "from": "args.city" },
      "units": { "default": "metric" }
    },
    "response_transform": "jq:{temp: .main.temp, description: .weather[0].description}"
  }
}
```

### Shell skill

```json
{
  "plex.restart": {
    "type": "shell",
    "description": "Restart the Plex Media Server Docker container.",
    "command": "docker restart plex-server"
  }
}
```

JSON skills should also have a SKILL.md (and SETUP.md) under `<MARCEL_ZOO_DIR>/skills/<name>/` to teach the agent how to use them.

## skills.json reference

### Skill types

| Type | Description |
|------|-------------|
| `http` (default) | Makes HTTP requests with configurable auth, params, and response transforms |
| `shell` | Runs a local shell command with `{param}` placeholder substitution |
| `python` | Auto-generated for `@marcel_tool`'d functions — do not add manually |

### HTTP skill fields

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no | Human-readable description |
| `method` | string | no | HTTP method. Defaults to `GET` |
| `url` | string | yes | Full URL for the request |
| `auth` | object | no | Auth configuration (see below) |
| `params` | object | no | Query parameter mappings |
| `response_transform` | string | no | jq expression applied to the response |

### Auth types

**`none`** (default) — no authentication.

**`api_key`** — reads a key from an environment variable:

| Field | Description |
|---|---|
| `env_var` | Environment variable holding the key |
| `location` | `"header"` (default) or `"query"` |
| `header_name` | Header name when location is header. Defaults to `"Authorization"` |
| `param_name` | Query param name when location is query. Defaults to `"api_key"` |

**`oauth2`** — placeholder, returns "not connected" message.

### Params config

Maps query parameter names to resolution rules:

| Field | Description |
|---|---|
| `from` | `"args.<name>"` — pull from caller arguments |
| `default` | Fallback when argument is missing |

### response_transform

Only `jq:` expressions are supported (requires the `jq` Python package). If jq is not installed, raw body is returned.

## The toolkit tool contract

| Argument | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Dotted integration ID (e.g. `"icloud.calendar"`) |
| `params` | object | no | String key-value pairs passed as arguments |

**On success**: returns the response as plain text.
**On error**: returns an error message with `is_error: true`.

### Skill docs are loaded on demand

There is no auto-injection of `SKILL.md` on the first `toolkit(...)` call and
no `deps.turn.read_skills` bookkeeping. The model pulls a skill's docs into
context by calling `load_capability("<skill-name>")` before calling its
toolkit — see [Deferred-capability disclosure](#deferred-capability-disclosure).
The loaded skill's own resource files are then reachable via that capability's
scoped `read_skill_resource(resource)` tool.

## The marcel tool contract

The `marcel` tool provides internal utilities via action-based dispatch.

| Argument | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | One of: `search_conversations`, `compact`, `notify`, `list_models`, `get_model`, `set_model`, `render` |
| `query` | string | for `search_*` | Search query — matches filenames, frontmatter fields, and body content |
| `message` | string | for `notify` | Short plain-text progress update |
| `max_results` | int | no | Maximum results (default: 10 for memory, 5 for conversations) |

!!! warning "Retired actions"
    `read_skill` and `read_skill_resource` were removed with Skills v2.
    Calling either now returns a short message redirecting to
    `load_capability` (see
    [Deferred-capability disclosure](#deferred-capability-disclosure)).
    A loaded skill's resources are read via that capability's own scoped
    `read_skill_resource(resource)` tool — not via the `marcel` tool.

The `list_models` / `get_model` / `set_model` actions are documented in
[Model tiers](model-tiers.md); `render` in
[A2UI Components](a2ui-components.md).

### search_conversations

Searches past conversation segments by keyword. Returns matching messages with surrounding context.

### compact

Compresses the current conversation segment into a summary and opens a fresh segment.

### notify

Sends a short progress update to the user. On Telegram, this sends a real-time message. On other channels, it returns `"ok"` (the user sees streaming output).

The agent should call `notify` at the start of any multi-step task and after each major step.
