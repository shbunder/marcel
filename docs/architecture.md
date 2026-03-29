# Architecture

Marcel is structured as a central API server (`marcel-core`) that all clients connect to via REST and WebSocket. The agent runs server-side; clients are thin.

## CLI — Rust native binary

The primary client is a native Rust TUI in `src/marcel_cli/`, built on **ratatui** + **crossterm** (same stack as codex-cli). It compiles to a single ~3.6MB binary, connects to the backend via WebSocket, and renders streaming responses with markdown support. See [cli.md](cli.md) for full details.

## Module layout

```
src/marcel_core/
  main.py          # FastAPI app, lifespan, router registration
  api/
    health.py      # GET /health
    chat.py        # WebSocket /ws/chat
  agent/
    context.py     # build_system_prompt — loads profile, memory, conversation history
    runner.py      # stream_response — calls claude_agent_sdk, yields text tokens
    memory_extract.py  # background fact extraction after each turn
  storage/         # Flat-file read/write helpers (ISSUE-002)
  skills/          # cmd-tool dispatcher and skills.json registry (ISSUE-004)
  auth/            # JWT and user identity (Phase 2)
  watchdog/        # Self-modification safety and git rollback (ISSUE-005)
  telegram/        # Telegram bot (Phase 2)
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `{"status": "ok", "version": "..."}` |
| `WS` | `/ws/chat` | Streaming chat WebSocket |

## WebSocket protocol

Connect to `/ws/chat`. Send JSON, receive a stream of JSON messages.

**Client → server:**
```json
{"text": "What's on my calendar?", "user": "shaun", "conversation": null}
```

`"conversation": null` starts a new session. Subsequent messages include the conversation ID returned by the server.

**Server → client:**
```json
{"type": "started", "conversation": "2026-03-26T14-32"}
{"type": "token", "text": "You have..."}
{"type": "token", "text": " a dentist..."}
{"type": "done"}
```

On error:
```json
{"type": "error", "message": "..."}
```

## Agent loop sequence

For each conversation turn:

```
1. Client sends {"text": "...", "user": "shaun", "conversation": null | "id"}
2. If conversation is null → storage.new_conversation() → send {"type":"started","conversation":"id"}
3. agent/context.py: load profile + all memory files + recent conversation history
4. Build system prompt, call claude_agent_sdk.query(prompt=user_text, options=...)
5. For each StreamEvent with type=content_block_delta:
     → yield text token → send {"type":"token","text":"..."}
6. After stream: append both turns to conversation file (storage.append_turn)
7. Fire-and-forget: memory_extract.extract_and_save_memories() as asyncio background task
8. Send {"type":"done"}
```

Memory extraction runs after every turn without blocking the response. It calls Claude with a
structured prompt, parses `TOPIC: / CONTENT:` blocks, and appends new facts to the relevant
`data/users/{slug}/memory/{topic}.md` files.

## Running locally

```bash
make serve
```

Starts uvicorn on `0.0.0.0:8000`. In development, `--reload` is enabled. See the [self-modification docs](self-modification.md) for the production watchdog setup.
