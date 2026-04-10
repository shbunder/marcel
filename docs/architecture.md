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
    context.py        # build_system_prompt — loads MARCEL.md + profile + memory + skills
    marcelmd.py       # MARCEL.md loader — discovers home + project instruction files
    runner.py         # stream_response — streams from persistent ClaudeSDKClient session
    sessions.py       # SessionManager — persistent session lifecycle + idle cleanup
    memory_select.py  # Relevance-based memory selection via Haiku side-query
    memory_extract.py # Agent-based memory extraction (Haiku + claude_code tools)
  storage/         # Flat-file read/write helpers (typed memory with frontmatter)
  skills/
    tool.py        # integration dispatcher (external service calls)
    registry.py    # Merges skills.json with auto-discovered python integrations
    executor.py    # Routes to shell/http/python handlers
    skills.json    # Shell and HTTP skill configs
    integrations/  # Pluggable python integration modules (@register decorator)
      icloud.py    # iCloud calendar + mail
  icloud/          # iCloud client library (CalDAV, IMAP)
  auth/            # Token verification and input validation
  watchdog/        # Self-modification safety and git rollback
  telegram/        # Telegram webhook, bot client, session state

.marcel/
  MARCEL.md        # Personal assistant instructions (persona, tone, tools overview)
  skills/          # Skill docs — teach the agent how to use integrations
    icloud/        # SKILL.md + SETUP.md (fallback for unconfigured)
    banking/       # SKILL.md + SETUP.md
    plex/          # SKILL.md + SETUP.md
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
{"text": "What's on my calendar?", "user": "alice", "token": "your-api-token", "conversation": null}
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
1. Client sends {"text": "...", "user": "alice", "token": "...", "conversation": null | "id"}
2. If conversation is null → storage.new_conversation() → send {"type":"started","conversation":"id"}
3. SessionManager.get_or_create() retrieves or creates a persistent ClaudeSDKClient session
4. agent/context.py: load MARCEL.md files, scan memory headers, select top memories,
   build system prompt = MARCEL.md instructions + profile + memory + skills + channel hint
   (no conversation history — SDK maintains context internally)
5. client.query(user_text) → client.receive_response()
6. For each StreamEvent with type=content_block_delta:
     → yield text token → send {"type":"token","text":"..."}
7. After stream: append both turns to conversation file as audit log (storage.append_turn)
8. Fire-and-forget: memory_extract.extract_and_save_memories() as asyncio background task
9. Send {"type":"done", "cost_usd": ..., "turns": ...}
```

### Session management

Each (user, conversation) pair gets a persistent `ClaudeSDKClient` that maintains conversation state across turns. This enables prompt cache reuse and SDK-managed context compaction. Sessions are cleaned up after 1 hour of idle time by a background task.

### Memory extraction

Runs after every turn without blocking the response. Launches a lightweight agent (Haiku, max 3 turns) with `claude_code` tools preset and CWD set to the user's memory directory. The agent reads existing memory files (via a manifest of frontmatter headers), writes new facts with typed frontmatter, and can update existing memories instead of duplicating. Schedule-type memories include an `expires` date for auto-pruning.

### Memory system

Memory files use YAML frontmatter with typed metadata (`schedule`, `preference`, `person`, `reference`, `household`). At conversation start, a Haiku side-query selects the most relevant memories from a manifest of headers (up to 8 for large sets; all for small sets). The unified `marcel` tool provides `search_memory` and `search_conversations` actions for mid-conversation keyword search. Schedule memories auto-expire past their date. The `_household` pseudo-user holds shared family memories included in all users' context.

## Running locally

```bash
make serve
```

Starts uvicorn on `0.0.0.0:8000`. In development, `--reload` is enabled. See the [self-modification docs](self-modification.md) for the production watchdog setup.
