# Skills & Integrations

Marcel has three MCP tools available to the agent:

1. **`integration`** — call registered integrations (iCloud, HTTP APIs, shell commands)
2. **`memory_search`** — search existing memories by keyword
3. **`notify`** — send progress updates to the user mid-task

Integrations can be defined as:

- **Python modules** with `@register` decorators — for integrations that need custom logic (API clients, stateful connections)
- **JSON entries** in `skills.json` — for simple HTTP calls or shell commands
- **Claude Code skills** (`.claude/skills/<name>/SKILL.md`) — prompt-driven skills that teach the agent how to use integrations

All integration types are dispatched through the `integration` tool. The agent learns about available integrations from the skill docs and the tool description.

## How it works

1. The agent receives a user request (e.g. "what's on my calendar?").
2. It reads the relevant SKILL.md (loaded as a Claude Code skill) which explains how to use the integration.
3. It calls `integration(skill="icloud.calendar", params={"days_ahead": "7"})`.
4. The executor dispatches to the right handler (python function, HTTP call, or shell command).
5. The result is returned as plain text to the agent.

## Adding a Python integration

Create a module in `src/marcel_core/skills/integrations/`:

```python
# src/marcel_core/skills/integrations/myservice.py

import json
from marcel_core.skills.integrations import register

@register("myservice.action")
async def action(params: dict, user_slug: str) -> str:
    """Each handler receives string params and the user slug."""
    value = params.get("key", "default")
    # ... do work ...
    return json.dumps(result, indent=2)
```

Then create a Claude Code skill doc at `src/marcel_core/skills/docs/myservice/SKILL.md`:

```markdown
---
description: Short description of what myservice does
---

Help the user with: $ARGUMENTS

You have access to the `integration` tool to interact with myservice.

## Available commands

### myservice.action

Description of what this does.

\`\`\`
integration(skill="myservice.action", params={"key": "value"})
\`\`\`

| Param | Type   | Required | Default | Description          |
|-------|--------|----------|---------|----------------------|
| key   | string | no       | default | What this param does |

Returns: description of the response format.
```

No changes to core code are needed — the module is auto-discovered at startup.

Run `make install-skills` to symlink the skill doc into `.claude/skills/` where the Claude Agent SDK picks it up. This runs automatically with `make serve` (dev) and during `docker build` (prod).

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

JSON skills should also have a SKILL.md in `.claude/skills/` to teach the agent how to use them.

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
| `skill` | string | yes | Dotted skill name (e.g. `"icloud.calendar"`) |
| `params` | object | no | String key-value pairs passed as arguments |

**On success**: returns the response as plain text.
**On error**: returns an error message with `is_error: true`.

## The memory_search tool contract

Searches the user's memory files by keyword. Use this when pre-loaded memory
context isn't enough and the agent needs to find specific information
mid-conversation.

| Argument | Type | Required | Description |
|---|---|---|---|
| `query` | string | yes | Search query — matches filenames, frontmatter fields, and body content |
| `type` | string | no | Filter by memory type: `schedule`, `preference`, `person`, `reference`, `household` |
| `max_results` | string | no | Maximum results to return (default: `"10"`) |

**On success**: returns matching memories formatted as markdown, with type
tags, filenames, and content snippets.
**On no results**: returns `No memories found matching "..."`.
**On error**: returns an error message with `is_error: true`.

Results are ranked: metadata matches (filename, name, description) first,
then body content matches, both sorted by recency.

## The notify tool contract

Sends a short progress update to the user. On the Telegram channel, this
sends a real-time message to the user's chat. On other channels, it's a
no-op that returns `"ok"`.

| Argument | Type | Required | Description |
|---|---|---|---|
| `message` | string | yes | Short plain-text progress update |

The agent should call `notify` at the start of any multi-step task and after
each major step, so the user always knows what's happening.
