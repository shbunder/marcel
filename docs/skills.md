# Skills

Marcel integrates with external services through a **skills registry** — a single `skills.json` file that maps skill names to HTTP call configurations. The agent dispatches to these integrations via a single `cmd` tool, keeping the tool surface minimal and all integration logic in data, not code.

## How it works

1. `skills.json` contains one entry per integration (e.g. `"weather.current"`).
2. When the agent needs to call an integration it invokes the `cmd` tool with the skill name and any arguments.
3. The executor reads the skill config, resolves auth and params, makes the HTTP request, and returns the response as plain text.
4. An optional `response_transform` (jq expression) shapes the response before it reaches the agent.

## skills.json schema

Each top-level key is a **dotted skill name** (e.g. `"calendar.list_events"`). The value is a skill config object.

### Skill config fields

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no | Human-readable description of what the skill does. |
| `method` | string | no | HTTP method. Defaults to `GET`. |
| `url` | string | yes | Full URL for the HTTP request. |
| `auth` | object | no | Auth configuration (see below). Defaults to no auth. |
| `params` | object | no | Query parameter mappings (see below). |
| `response_transform` | string | no | Post-processing expression applied to the response body (see below). |

### Auth config

The `auth` object has a `type` field:

**`none`** (default) — no authentication.

```json
{ "auth": { "type": "none" } }
```

**`api_key`** — reads a key from an environment variable and sends it as a header or query parameter.

```json
{
  "auth": {
    "type": "api_key",
    "env_var": "OPENWEATHER_API_KEY",
    "location": "query",
    "param_name": "appid"
  }
}
```

| Field | Description |
|---|---|
| `env_var` | Name of the environment variable holding the key. |
| `location` | `"header"` (default) or `"query"`. |
| `header_name` | Header name when `location` is `"header"`. Defaults to `"Authorization"`. |
| `param_name` | Query param name when `location` is `"query"`. Defaults to `"api_key"`. |

**`oauth2`** — not yet implemented. Returns a "not connected" message to the agent until Phase 3 adds the OAuth flow.

### Params config

The `params` object maps query parameter names to resolution rules:

```json
{
  "params": {
    "q":          { "from": "args.city" },
    "units":      { "default": "metric" },
    "maxResults": { "from": "args.limit", "default": 10 }
  }
}
```

| Field | Description |
|---|---|
| `from` | `"args.<name>"` — pull the value from the caller-supplied arguments. |
| `default` | Fallback value when the argument is missing or `from` is not set. |

### response_transform

An optional expression applied to the raw response body before it is returned to the agent.

Only `jq:` expressions are supported (requires the `jq` Python package). If `jq` is not installed the raw body is returned unchanged.

```json
{ "response_transform": "jq:.weather[0].description" }
```

## Example entry

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

## How to add an integration

1. Choose a dotted skill name following the pattern `<domain>.<action>` (e.g. `shopping.add_item`).
2. Add the entry to `src/marcel_core/skills/skills.json`.
3. If the skill needs an API key, add the environment variable to `.env` and document it.
4. Optionally create a companion file `src/marcel_core/skills/descriptions/<skill-name>.md` describing the skill for the agent's system prompt context.
5. No code changes are required — the executor picks up the new entry automatically on the next request.

## The cmd tool contract

The agent receives a single `cmd` tool with this interface:

| Argument | Type | Required | Description |
|---|---|---|---|
| `skill` | string | yes | Dotted skill name from the registry (e.g. `"weather.current"`). |
| `params` | object | no | String key-value pairs passed as arguments to the skill. |

**On success** the tool returns the (optionally transformed) response body as plain text.

**On error** the tool returns an error message as text with `is_error: true`. Errors include: unknown skill name, HTTP error responses, and execution failures.

The agent should call `cmd` with `list_skills` (if available) or use the description injected at startup to discover what skills are registered before attempting a call.
