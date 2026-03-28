# ISSUE-003: Agent loop — claude_agent_sdk integration

**Status:** Closed
**Created:** 2026-03-26
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, phase-1

## Capture
**Original request:** marcel-core should be a Python AI agent built using claude_agent_sdk, capable of being an assistant and rewriting its own code.

**Resolved intent:** Wire the `claude_agent_sdk` agent into the FastAPI WebSocket endpoint. The agent loads user memory and recent conversation history as context on each turn, streams its response back to the client, and persists the turn + any memory updates after responding.

## Description

Replace the echo stub in `api/chat.py` with a real agent loop.

### Per-turn flow

```
1. Client sends: {"text": "...", "user": "shaun", "conversation": "2026-03-26T14-32"}
2. Load context:
   a. storage.load_memory_index("shaun") + all referenced memory files
   b. storage.load_conversation("shaun", conversation_filename)  [last ~3000 tokens]
3. Build system prompt: identity + memory context + channel format instructions
4. Run claude_agent_sdk agent with user message, stream tokens back over WebSocket
5. After stream completes:
   a. storage.append_turn(slug, filename, "user", user_text)
   b. storage.append_turn(slug, filename, "assistant", full_response)
   c. (async background) memory extraction: ask Claude to identify any new facts
      → storage.save_memory_file / update_memory_index if new facts found
   d. Update conversation index description if this was the first turn
```

### System prompt structure

```
You are Marcel, a personal assistant for {user_display_name}.

## What you know about {user_display_name}
{profile.md content}

## Memory
{memory/index.md + contents of all relevant memory files}

## Recent conversation
{last N turns from conversation file}

## Channel
You are responding via {channel}. {format instructions per channel}
```

### Agent module layout

```
src/marcel_core/agent/
  __init__.py
  runner.py       # run_agent(user_slug, channel, user_text, conversation_id) → AsyncIterator[str]
  context.py      # build_system_prompt(user_slug, channel) → str
  memory_extract.py  # extract_and_save_memories(user_slug, response_text, conversation_id)
```

### WebSocket message protocol

Client → Server:
```json
{"text": "What's on my calendar?", "user": "shaun", "conversation": "2026-03-26T14-32"}
```

Server → Client (streamed):
```json
{"type": "token", "text": "You have..."}
{"type": "token", "text": " a dentist..."}
{"type": "done"}
```

On error:
```json
{"type": "error", "message": "..."}
```

A `"conversation": null` in the client message means start a new conversation — the server creates one and returns its ID in the first `{"type": "started", "conversation": "2026-03-26T14-32"}` message.

## Tasks
- [✓] `agent/context.py`: `build_system_prompt` — loads profile + memory + recent conversation
- [✓] `agent/runner.py`: `stream_response` — sets up claude_agent_sdk agent, streams tokens
- [✓] `agent/memory_extract.py`: after each turn, ask Claude to extract new facts and persist
- [✓] `api/chat.py`: replace echo stub with real agent call, handle WebSocket message protocol
- [✓] Add `ANTHROPIC_API_KEY` to `.env` and document in README
- [✓] Tests: mock agent responses, verify context is built correctly, verify turns are persisted
- [✓] Docs: update `docs/architecture.md` with agent loop sequence

## Relationships
- Depends on: [[ISSUE-001-marcel-core-server-scaffold]], [[ISSUE-002-flat-file-storage]]
- Blocks: [[ISSUE-006-marcel-cli-tui]]

## Implementation Log

### 2026-03-26 - LLM Implementation
**Action**: Implemented full agent loop — context building, streaming, memory extraction, WebSocket protocol
**Files Modified**:
- `src/marcel_core/agent/context.py` — `build_system_prompt` with profile/memory/history loading
- `src/marcel_core/agent/runner.py` — `stream_response` async generator; StreamEvent token streaming with AssistantMessage fallback
- `src/marcel_core/agent/memory_extract.py` — background fact extraction; `_parse_and_save` TOPIC/CONTENT parser
- `src/marcel_core/agent/__init__.py` — public exports
- `src/marcel_core/api/chat.py` — full WebSocket protocol replacing echo stub
- `src/marcel_core/main.py` — added `load_dotenv()` at startup
- `pyproject.toml` — added `python-dotenv`, `pytest-asyncio`
- `.env` — added `ANTHROPIC_API_KEY` placeholder
- `tests/core/test_agent.py` — 24 tests covering all modules
- `tests/core/test_scaffold.py` — removed stale echo WebSocket tests (superseded by test_agent.py)
- `docs/architecture.md` — updated module layout + added agent loop sequence diagram
**Commands Run**: `uv sync`, `uv run pytest tests/ -q`
**Result**: 70/70 tests passing
