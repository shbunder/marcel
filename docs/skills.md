# Skills & Integrations

Marcel has two primary tools available to the agent:

1. **`integration`** — call registered integrations (iCloud, HTTP APIs, shell commands)
2. **`marcel`** — internal utilities: `read_skill`, `read_skill_resource`, `search_memory`, `search_conversations`, `compact`, `notify`

Integrations can be defined as:

- **Python modules** with `@register` decorators — for integrations that need custom logic (API clients, stateful connections)
- **JSON entries** in `skills.json` — for simple HTTP calls or shell commands

Skill documentation lives in `<dir>/skills/<name>/SKILL.md`. These teach the agent how to use each integration. Skills are loaded from two directories and injected into the system prompt:

1. **`<MARCEL_ZOO_DIR>/skills/`** — habitats from the marcel-zoo checkout (skipped when `MARCEL_ZOO_DIR` is unset). See [Plugin API](plugins.md).
2. **`<MARCEL_DATA_DIR>/skills/`** (typically `~/.marcel/skills/`) — user-level overrides and custom skills.

Data-root skills override zoo skills with the same name. The override is silent — drop a `~/.marcel/skills/<name>/SKILL.md` to replace any zoo-shipped version.

## How it works

1. The agent receives a user request (e.g. "what's on my calendar?").
2. Its system prompt includes the relevant SKILL.md content (loaded by the skill loader).
3. It calls `integration(id="icloud.calendar", params={"days_ahead": "7"})`.
4. The executor dispatches to the right handler (python function, HTTP call, or shell command).
5. The result is returned as plain text to the agent.

## The three skill shapes

A skill's frontmatter determines how the loader treats it and whether a `SETUP.md` fallback is meaningful. There are three distinct shapes:

### 1. Standalone — pure teaching material, no requirements

The simplest shape: no `depends_on:`, no `requires:`. The `SKILL.md` is always loaded because it documents built-in kernel tools (e.g. the `web`, `memory`, or `jobs` utility action families) or pure domain knowledge. No `SETUP.md` is needed because there is nothing to set up.

```yaml
---
name: memory
description: Manage conversation memory — search past conversations, recall facts
---
```

Standalone skills live in `<MARCEL_ZOO_DIR>/skills/<name>/` alongside every other skill habitat. The kernel ships none of them by default.

### 2. Self-contained — inline `requires:`

A skill that needs credentials, environment variables, files, or Python packages but has no paired integration handler — for instance, a skill that teaches the agent how to use a tool whose credentials are read directly at call time. Declare the dependencies inline; they drive SKILL.md → SETUP.md switching.

### 3. Integration-backed — `depends_on:`

The typical case for any skill that calls `integration(id="...")`. See below.

## Skill fallback (SETUP.md)

A skill's `SETUP.md` (shapes 2 and 3) activates when the skill's requirements are not met. This guides new users through first-time setup rather than failing silently. Standalone skills (shape 1) do not need a `SETUP.md`.

There are two ways to declare requirements in `SKILL.md` frontmatter:

### Inline `requires:` — for self-contained skills

```yaml
---
name: myservice
description: What myservice does
requires:
  credentials:
    - MY_API_KEY          # must exist in user's credential store
  env:
    - SOME_ENV_VAR        # must be set in the environment
  files:
    - signing_key.pem     # must exist in user's data directory
  packages:
    - some_python_pkg     # must be importable
---
```

### `depends_on:` — for skills that call an integration habitat

When a skill is just the documentation that fronts an integration habitat (the typical case), declare the link instead of duplicating the requirements:

```yaml
---
name: docker
description: Manage Docker containers
depends_on:
  - docker
---
```

The loader looks up `<MARCEL_ZOO_DIR>/integrations/docker/integration.yaml`, reads its `requires:` block, and treats those as the skill's requirements. This keeps the credential / env list in one place — the integration's `integration.yaml` — and avoids drift between the integration and its skill doc. See [Plugin API → Integration metadata](plugins.md#integration-metadata).

Both forms can be combined; the skill's effective requirements are the union of inline `requires:` and every `depends_on:` integration's `requires:`.

When all requirements are met, the agent sees `SKILL.md`. When any are missing — including a `depends_on:` integration whose metadata is not registered (zoo not loaded or `integration.yaml` missing) — it sees `SETUP.md` (marked as "not configured" in the prompt).

## Adding a Python integration

Python integrations live as zoo habitats: `<MARCEL_ZOO_DIR>/integrations/<name>/__init__.py` (plus `integration.yaml`), installable components of marcel-zoo. See [Plugins](plugins.md) for the full habitat contract. The kernel ships zero bundled integrations — every real integration lives in the zoo.

Habitats must use `from marcel_core.plugin import register` (the stable plugin surface) and obey the directory-name ↔ handler-namespace rule: an integration at `.../integrations/myservice/` may only register `myservice.*` handlers; handlers outside that namespace cause the whole habitat to be rolled back.

```python
import json
from marcel_core.plugin import register

@register("myservice.action")
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
depends_on:
  - myservice
---

You have access to the `integration` tool to interact with myservice.

## Available commands

### myservice.action

Description of what this does.

\`\`\`
integration(id="myservice.action", params={"key": "value"})
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

No changes to kernel code are needed — the integration module is auto-discovered at startup, the skill habitat is loaded from `<MARCEL_ZOO_DIR>/skills/` automatically, and `depends_on:` resolves the credentials/env block from the integration's `integration.yaml`.

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

JSON skills should also have a SKILL.md (and SETUP.md) in `.marcel/skills/` to teach the agent how to use them.

## skills.json reference

### Skill types

| Type | Description |
|------|-------------|
| `http` (default) | Makes HTTP requests with configurable auth, params, and response transforms |
| `shell` | Runs a local shell command with `{param}` placeholder substitution |
| `python` | Auto-generated for `@register`'d functions — do not add manually |

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

## The integration tool contract

| Argument | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Dotted integration ID (e.g. `"icloud.calendar"`) |
| `params` | object | no | String key-value pairs passed as arguments |

**On success**: returns the response as plain text.
**On error**: returns an error message with `is_error: true`.

### Auto-loaded skill docs

As a safety net, the first `integration(id="<family>.<method>")` call per conversation prepends the full `SKILL.md` to the tool result so the model always has enough context to interpret what came back. The integration tool tracks which skill families have been loaded in `deps.turn.read_skills`, primed at turn start from past `marcel(action="read_skill", name=...)` calls in the conversation history — so once a skill's docs are in the context window, they are not re-injected on subsequent turns. The model can short-circuit the auto-load on its own by calling `marcel(action="read_skill", name=...)` before the first integration call.

## The marcel tool contract

The `marcel` tool provides internal utilities via action-based dispatch.

| Argument | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | One of: `read_skill`, `read_skill_resource`, `search_memory`, `search_conversations`, `compact`, `notify` |
| `name` | string | for `read_skill`, `read_skill_resource` | Skill name |
| `resource` | string | for `read_skill_resource` | Resource filename or stem to load (e.g. `"feeds"`, `"SETUP.md"`) |
| `query` | string | for `search_*` | Search query — matches filenames, frontmatter fields, and body content |
| `message` | string | for `notify` | Short plain-text progress update |
| `type_filter` | string | no | Filter by memory type (for `search_memory`) |
| `max_results` | int | no | Maximum results (default: 10 for memory, 5 for conversations) |

### read_skill

Loads the full SKILL.md documentation for a skill. The system prompt only contains a compact index (name + description per skill); use this action to get full docs before calling an unfamiliar integration.

The response includes an **Available resources** footer listing any extra files in the skill directory (e.g. `SETUP.md`, `feeds.yaml`, `components.yaml`).

### read_skill_resource

Loads a named resource file from a skill's directory. Resources are any files other than `SKILL.md` — typically `SETUP.md`, `feeds.yaml`, `components.yaml`, or other configuration files.

Matching is case-insensitive and accepts both a bare stem (`"feeds"`) and a full filename (`"feeds.yaml"`).

```
marcel(action="read_skill_resource", name="news", resource="feeds")
```

Use `read_skill` first to discover what resources a skill exposes.

### search_memory

Searches the user's memory files by keyword. Results are ranked: metadata matches first, then body content, sorted by recency.

### search_conversations

Searches past conversation segments by keyword. Returns matching messages with surrounding context.

### compact

Compresses the current conversation segment into a summary and opens a fresh segment.

### notify

Sends a short progress update to the user. On Telegram, this sends a real-time message. On other channels, it returns `"ok"` (the user sees streaming output).

The agent should call `notify` at the start of any multi-step task and after each major step.
