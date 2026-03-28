# ISSUE-004: cmd tool + skills registry

**Status:** Closed
**Created:** 2026-03-26
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature, phase-1

## Capture
**Original request:** I don't want hundreds of tools. Use a central cmd-tool with a JSON config that maps skills to API calls with inline arguments.

**Resolved intent:** Implement the plumbing for the skill system: a `skills.json` registry, a `cmd` tool registered with the agent, and an HTTP executor. Phase 1 ships no real integrations — just the infrastructure so integrations can be added in Phase 3 without touching core code.

## Description

### `skills.json` entry shape

```json
{
  "calendar.list_events": {
    "description": "List upcoming calendar events for the user",
    "method": "GET",
    "url": "https://www.googleapis.com/calendar/v3/calendars/primary/events",
    "auth": {
      "type": "oauth2",
      "provider": "google",
      "scopes": ["https://www.googleapis.com/auth/calendar.readonly"]
    },
    "params": {
      "maxResults": { "from": "args.limit", "default": 10 },
      "timeMin":    { "from": "args.from_date", "transform": "iso8601" }
    },
    "response_transform": "jq:.items[] | {title: .summary, start: .start.dateTime}"
  }
}
```

Supported auth types in Phase 1: `none`, `api_key` (header or query param). `oauth2` is wired but returns a "not connected" message until Phase 3 adds the OAuth flow.

### `cmd` tool registered with the agent

```python
async def cmd(skill: str, **kwargs: str) -> str:
    """
    Execute a registered integration skill.
    skill: dotted skill name from the registry (e.g. "calendar.list_events")
    kwargs: skill-specific arguments
    """
```

When the agent calls `cmd("calendar.list_events", limit="5")`, the executor:
1. Loads the skill config from `skills.json`
2. Checks auth — if oauth2 and not connected, returns "User has not connected Google yet."
3. Resolves params from kwargs + defaults
4. Executes the HTTP request
5. Applies response_transform (jq expression)
6. Returns the result as a plain string

### Skills module layout

```
src/marcel_core/skills/
  __init__.py
  registry.py      # loads + validates skills.json; get(skill_name) → config
  executor.py      # run(config, kwargs, user_slug) → str
  tool.py          # cmd() function registered as a claude_agent_sdk tool
  skills.json      # registry file (starts empty, populated in Phase 3)
```

### Skill description files

Each skill can have a companion `.md` file in `src/marcel_core/skills/descriptions/` that gets injected into the agent's system prompt to explain what skills are available. In Phase 1 this directory is empty.

## Tasks
- [✓] `skills/registry.py`: load and validate `skills.json`; raise clear error for unknown skills
- [✓] `skills/executor.py`: HTTP executor supporting `none` and `api_key` auth; jq response transform (use `jq` Python lib or inline parse)
- [✓] `skills/tool.py`: `cmd` function with correct signature for claude_agent_sdk tool registration
- [✓] Register `cmd` tool with the agent in `agent/runner.py`
- [✓] `skills/skills.json`: empty registry `{}`
- [✓] Tests: registry loading, executor with a mock HTTP response, unknown skill error
- [✓] Docs: `docs/skills.md` — skills.json schema, how to add an integration, cmd tool contract

## Relationships
- Depends on: [[ISSUE-001-marcel-core-server-scaffold]], [[ISSUE-003-agent-loop]]

## Implementation Log

### 2026-03-28 - LLM Implementation
**Action**: Implemented skills registry, HTTP executor, cmd MCP tool, and wired into agent runner
**Files Modified**:
- `src/marcel_core/skills/__init__.py` — module init, exports `build_skills_mcp_server`, `get_skill`, `list_skills`
- `src/marcel_core/skills/skills.json` — empty registry `{}`
- `src/marcel_core/skills/registry.py` — loads skills.json, `get_skill` with clear KeyError, `list_skills`
- `src/marcel_core/skills/executor.py` — async HTTP executor with `none`/`api_key`/`oauth2` auth, param resolution, jq transform
- `src/marcel_core/skills/tool.py` — `build_skills_mcp_server(user_slug)` builds in-process MCP server with `cmd` tool
- `src/marcel_core/skills/descriptions/.keep` — placeholder directory for per-skill description files
- `src/marcel_core/agent/runner.py` — replaced `tools=[]` stub with `mcp_servers={'skills': build_skills_mcp_server(user_slug)}` and `allowed_tools=['cmd']`
- `pyproject.toml` — added `httpx>=0.27.0` to dependencies, `respx>=0.21.0` to dev dependencies
- `tests/core/test_skills.py` — tests for registry, executor auth variants, param resolution, and response transforms
- `docs/skills.md` — skills.json schema, auth types, params, response_transform, how to add an integration, cmd tool contract
- `mkdocs.yml` — added `- Skills: skills.md` to nav
**Result**: All tasks complete. No real integrations shipped — infrastructure only, ready for Phase 3.
