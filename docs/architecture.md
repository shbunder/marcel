# Architecture

Marcel is structured as a central API server (`marcel-core`) that all clients connect to via REST and WebSocket. The agent runs server-side; clients are thin.

## Module layout

```
src/marcel_core/
  main.py          # FastAPI app, lifespan, router registration
  api/
    health.py      # GET /health
    chat.py        # WebSocket /ws/chat
  agent/           # claude_agent_sdk agent loop (ISSUE-003)
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

## Running locally

```bash
make serve
```

Starts uvicorn on `0.0.0.0:8000`. In development, `--reload` is enabled. See the [self-modification docs](self-modification.md) for the production watchdog setup.
