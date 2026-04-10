# ISSUE-049: Full Migration to v2 Pydantic-AI Harness

**Status:** Open
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

- [ ] Port browser tools from MCP server to pydantic-ai tools in `harness/agent.py`
- [ ] Migrate `/ws/chat` (`api/chat.py`) from `stream_response` to `stream_turn` with v2 event mapping
- [ ] Remove manual history append from `chat.py` (v2 handles this automatically)
- [ ] Remove `session_manager` startup/cleanup from `main.py` lifespan
- [ ] Remove `session_manager.reset_user()` call from `telegram/webhook.py` (no-op with v2)
- [ ] Delete `agent/runner.py` (v1 stream_response)
- [ ] Delete `agent/sessions.py` (v1 SessionManager + ClaudeSDKClient)
- [ ] Delete `agent/context.py::build_system_prompt()` (replaced by `harness/context.py`)
- [ ] Delete `agent/events.py` (AG-UI events, replaced by v2 MarcelEvent types)
- [ ] Delete `skills/__init__.py::build_skills_mcp_server()` (v2 registers tools directly)
- [ ] Delete `tools/browser/tools.py::build_browser_mcp_server()` (replaced by pydantic-ai tools)
- [ ] Update `agent/__init__.py` re-exports
- [ ] Update or remove tests for deleted code (`test_agent.py`, `test_sessions.py`, `test_agent_events.py`)
- [ ] Run `make check`

## Subtasks

## Relationships
- Depends on: [[ISSUE-045-per-session-history]]
- Depends on: [[ISSUE-046-tool-call-history]]
- Depends on: [[ISSUE-047-remove-legacy-conversations]]
- Related to: [[ISSUE-043-browser-skill]]

## Comments

## Implementation Log
