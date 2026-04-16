# ISSUE-049: Full Migration to v2 Pydantic-AI Harness

**Status:** Closed
**Created:** 2026-04-10
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, architecture

## Capture
**Original request:** "can we also migrate the code-base and make the full switch?"

**Follow-up Q&A:**
- Q: Browser tools only work via v1's MCP server. How to handle?
- A: Port to pydantic-ai tools — they should have a way to wrap MCPs into tools.

**Resolved intent:** Fully migrate from the v1 ClaudeSDKClient agent path to the v2 pydantic-ai harness. This means: making `/ws/chat` use `stream_turn()` instead of `stream_response()`, porting browser tools to native pydantic-ai tools, removing the v1 `SessionManager`/`ClaudeSDKClient` infrastructure, and cleaning up dead code. After this, pydantic-ai is the single agent runtime.

## Description

Marcel currently runs two parallel agent paths:
- **v1**: `agent/runner.py` + `agent/sessions.py` (ClaudeSDKClient, MCP servers, persistent sessions)
- **v2**: `harness/runner.py` + `harness/agent.py` (pydantic-ai, stateless per-turn, JSONL history)

Telegram already uses v2. The `/ws/chat` WebSocket endpoint is the last v1 consumer. This issue migrates it and removes the entire v1 path.

### Browser tools migration
The 9 Playwright browser tools (`navigate`, `screenshot`, `snapshot`, `click`, `type`, `scroll`, `press_key`, `tab`, `close`) currently live behind an MCP server (`tools/browser/tools.py::build_browser_mcp_server()`). They need to be ported to native pydantic-ai tools registered in `harness/agent.py`, with a shared browser context managed per-turn or per-session.

## Tasks

- [✓] Port browser tools from MCP server to pydantic-ai tools in `harness/agent.py`
- [✓] Migrate `/ws/chat` (`api/chat.py`) from `stream_response` to `stream_turn` with v2 event mapping
- [✓] Remove manual history append from `chat.py` (v2 handles this automatically)
- [✓] Remove `session_manager` startup/cleanup from `main.py` lifespan
- [✓] Remove `session_manager.reset_user()` call from `telegram/webhook.py` (no-op with v2)
- [✓] Delete `agent/runner.py` (v1 stream_response)
- [✓] Delete `agent/sessions.py` (v1 SessionManager + ClaudeSDKClient)
- [✓] Delete `agent/context.py::build_system_prompt()` (replaced by `harness/context.py`)
- [✓] Delete `agent/events.py` (AG-UI events, replaced by v2 MarcelEvent types)
- [✓] Delete `skills/__init__.py::build_skills_mcp_server()` (v2 registers tools directly)
- [✓] Delete `tools/browser/tools.py::build_browser_mcp_server()` (replaced by pydantic-ai tools)
- [✓] Update `agent/__init__.py` re-exports
- [✓] Update or remove tests for deleted code (`test_agent.py`, `test_sessions.py`, `test_agent_events.py`)
- [✓] Run `make check`

## Subtasks

## Relationships
- Depends on: [[ISSUE-045-per-session-history]]
- Depends on: [[ISSUE-046-tool-call-history]]
- Depends on: [[ISSUE-047-remove-legacy-conversations]]
- Related to: [[ISSUE-043-browser-skill]]

## Comments

## Implementation Log

### 2026-04-10 - LLM Implementation
**Action**: Complete v2 migration — removed claude-agent-sdk dependency, consolidated endpoints, rewrote memory extraction, added consistent logging
**Files Modified**:
- `src/marcel_core/agent/memory_extract.py` — rewrote from claude_agent_sdk to pydantic-ai Agent (returns JSON operations applied to disk)
- `src/marcel_core/agent/__init__.py` — removed memory_select re-export
- `src/marcel_core/agent/memory_select.py` — deleted (backward-compat shim)
- `src/marcel_core/api/chat.py` — now the single WebSocket endpoint, added logging
- `src/marcel_core/api/chat_v2.py` — deleted (was duplicate of chat.py)
- `src/marcel_core/api/sessions.py` — deleted (session-based model obsolete)
- `src/marcel_core/api/conversations.py` — renamed /v2/ routes to /api/
- `src/marcel_core/main.py` — removed v2 routers, legacy migration, added log formatting and health check filter
- `src/marcel_core/harness/agent.py` — cleaned up log messages
- `src/marcel_core/harness/runner.py` — removed backward-compat alias, added trace logging
- `src/marcel_core/memory/summarizer.py` — consistent log format
- `src/marcel_core/channels/telegram/webhook.py` — trace format logs
- `src/marcel_core/channels/websocket.py` — removed v2 reference
- `src/marcel_core/jobs/executor.py` — consistent log format
- `src/marcel_core/tools/claude_code.py` — removed claude_agent_sdk fallback
- `src/marcel_core/__init__.py` — version 1.5.0 → 2.0.0
- `pyproject.toml` — removed claude-agent-sdk dependency, version 2.0.0
- `uv.lock` — updated (claude-agent-sdk uninstalled)
- `src/marcel_cli/src/app.rs` — updated /v2/ → /api/ endpoints
- `tests/core/test_agent.py` — rewrote for pydantic-ai based extraction
- `tests/core/test_chat_v2.py` — retargeted to /ws/chat endpoint
- `tests/core/test_agent_memory_select.py` — removed v1 reference
- `tests/harness/test_runner.py` — updated to use _messages_to_model
**Result**: 685 tests passing, claude-agent-sdk fully removed

**Reflection**:
- Coverage: 14/14 tasks addressed (all marked ✓)
- Shortcuts found: none
- Scope drift: added logging format improvements and health check filtering (requested by user alongside migration)

## Lessons Learned

### What worked well
- The migration was straightforward because v2 was already the primary path — Telegram and the WebSocket endpoint both used `stream_turn()`. The "migration" was mostly deleting v1 code
- Rewriting `memory_extract.py` from `claude_agent_sdk.query()` to a pydantic-ai Agent that returns JSON operations was a clean pattern — eliminates the dependency while keeping the same behavior
- Adding the health check log filter and suppressing httpx/httpcore noise immediately made Docker logs usable — small effort, high value

### What to do differently
- The `/v2/` prefix on endpoints should have been renamed to `/api/` when the endpoints were first created, not as a post-migration cleanup. Endpoint names should reflect purpose, not implementation version
- Multiple other issues' uncommitted changes were in the working tree during this migration — a cleaner approach would be to commit or stash other work first. The pre-commit hook caught test failures from these stale changes, costing debugging time
- The closing commit accidentally picked up `.marcel/` skill file deletions from another issue's work — should have been more careful with `git add` scope

### Patterns to reuse
- For SDK migrations: make the new path the default first (keep old code), then delete the old code in a separate issue — "migrate then delete" is less risky than "rewrite in place"
- JSON-return-value pattern for agent sub-tasks: instead of giving an agent file I/O tools, have it return structured JSON and apply the operations in the caller. Simpler, more testable, no permission issues
- Custom `logging.Filter` subclass on specific loggers (e.g. `uvicorn.access`) to suppress noisy patterns — cleaner than adjusting log levels which affects all messages
