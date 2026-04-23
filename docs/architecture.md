# Architecture

Marcel is structured as a central API server (`marcel-core`) that all clients connect to via REST and WebSocket. The agent runs server-side; clients are thin.

## CLI — Rust native binary

The primary client is a native Rust TUI in `src/marcel_cli/`, built on **ratatui** + **crossterm** (same stack as codex-cli). It compiles to a single ~3.6MB binary, connects to the backend via WebSocket, and renders streaming responses with markdown support. See [cli.md](cli.md) for full details.

## Module layout

```
src/marcel_core/
  main.py          # FastAPI app, lifespan, router registration
  config.py        # Centralized pydantic-settings configuration
  tracing.py       # Optional OpenTelemetry tracing via Phoenix
  api/
    health.py      # GET /health
    chat.py        # WebSocket /ws/chat — streaming conversation
    conversations.py # GET /conversations, /api/history, /api/forget
    artifacts.py   # GET /api/artifact/{id}, /api/artifacts — rich content
  harness/
    agent.py       # create_agent() — pydantic-ai Agent with tool registration
    context.py     # MarcelDeps, TurnState, build_instructions_async — assembles the five-block system prompt
    runner.py      # stream_turn — streams from pydantic-ai agent, yields deltas/tool events
    marcelmd.py    # MARCEL.md loader — discovers home + project instruction files
  memory/
    conversation.py  # Segment-based continuous conversation storage
    summarizer.py    # Idle summarization — seals segments, generates rolling summaries
    selector.py      # Relevance-based memory selection via Haiku side-query
    extract.py       # Post-turn fire-and-forget memory extraction (Haiku)
    history.py       # Message types (HistoryMessage, ToolCall)
    pastes.py        # Content-addressed paste store for large tool results
  channels/
    adapter.py     # ChannelAdapter protocol — generic event dispatch
    websocket.py   # WebSocket channel adapter
    telegram/      # Telegram webhook, bot client, formatting, session state
  tools/
    core.py        # bash, read_file, write_file, edit_file, git_*
    marcel/        # Unified Marcel utility tool — per-action sub-modules
      dispatcher.py    # The marcel() entry point advertised to the LLM
      skills.py        # read_skill, read_skill_resource actions
      memory.py        # search_memory, save_memory actions
      conversations.py # search_conversations, compact actions
      notifications.py # notify action + send_notify helper
      settings.py      # list_models, get_model, set_model actions
    integration.py # Integration dispatcher — routes to skill registry
    charts.py      # Chart generation via matplotlib
    rss.py         # RSS/Atom feed fetcher
    claude_code.py # Claude Code delegation
    browser/       # Playwright-based browser tools (navigate, evaluate, snapshot)
  jobs/
    models.py      # Job templates and schedule models
    scheduler.py   # Cron-based job scheduler
    executor.py    # Job execution engine
    cache.py       # Inter-job data sharing cache
    tool.py        # Job management tool (list, create, run)
  skills/
    registry.py    # Merges skills.json with auto-discovered python integrations
    executor.py    # Routes to shell/http/python handlers
    loader.py      # Skill document loader (SKILL.md, SETUP.md)
                   # All toolkit habitats (banking, icloud, docker, news, …)
                   # live under <MARCEL_ZOO_DIR>/toolkit/ — see Toolkit docs.
                   # Kernel ships zero first-party toolkits.
  storage/         # Flat-file read/write helpers (users, memory, artifacts)
    artifacts.py   # Artifact storage for rich content (Mini App)
  auth/            # Token verification, Telegram initData, input validation
  watchdog/        # Self-modification safety, health checks, git rollback
  defaults/        # Bundled skill docs (SKILL.md, SETUP.md) seeded to data root

~/.marcel/        # Data root (configurable via MARCEL_DATA_DIR)
  config.toml      # CLI configuration
  MARCEL.md        # Global personal assistant instructions
  skills/          # Skill docs loaded into agent context
  users/
    {slug}/
      profile.md   # User identity and preferences
      memory/      # Typed memory files with frontmatter
      conversation/{channel}/  # Continuous conversation storage (segments + summaries)
      .pastes/     # Large tool result content
    _household/    # Shared family memories
  artifacts/       # Rich content served by Mini App
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `{"status": "ok", "version": "..."}` |
| `WS` | `/ws/chat` | Streaming chat WebSocket |
| `GET` | `/conversations` | List conversations for a user |
| `GET` | `/api/history` | Load conversation context (summary + active segment) |
| `POST` | `/api/forget` | Trigger summarization / start fresh |
| `GET` | `/api/artifact/{id}` | Fetch a rich-content artifact |
| `GET` | `/api/artifacts` | List artifact summaries |
| `GET` | `/api/components` | Full A2UI component catalog (all registered components with JSON Schema props) |
| `GET` | `/api/components/{name}` | Single component schema by name |
| `POST` | `/telegram/webhook` | Telegram Bot API webhook |

The `/api/components` endpoints let native frontends (Telegram Mini App, iOS, macOS) fetch the component catalog once at startup, so they know which A2UI widgets to render and what props each widget expects. Both endpoints require authentication (Telegram `initData` or Bearer token). See [A2UI Components](a2ui-components.md) for the component declaration format.

## WebSocket protocol

Connect to `/ws/chat`. Send JSON, receive a stream of JSON messages.

**Client -> server:**
```json
{"text": "What's on my calendar?", "user": "alice", "token": "your-api-token", "conversation": null}
```

**Server -> client:**
```json
{"type": "started", "conversation": "2026-03-26T14-32"}
{"type": "token", "text": "You have..."}
{"type": "token", "text": " a dentist..."}
{"type": "tool_call", "name": "integration", "arguments": {"id": "icloud.calendar"}}
{"type": "tool_result", "name": "integration", "preview": "[3 events]"}
{"type": "done", "cost_usd": 0.012}
```

On error:
```json
{"type": "error", "message": "..."}
```

## Agent loop sequence

For each conversation turn:

```
1. Client sends {"text": "...", "user": "alice", "token": "...", "conversation": null | "id"}
2. If conversation is null -> create or resume via conversation channel
3. build_instructions_async() — assembles five H1 blocks:
   - `# Marcel — who you are` — global MARCEL.md (H1 + self-ref blockquote stripped)
   - `# <User> — who the user is` — profile body (+ server context H2 for admin)
   - `# Skills — what you can do` — compact skill index, full docs on demand via `read_skill`
   - `# Memory — what you should know` — compact memory index, full bodies on demand via `read_memory` / `search_memory`
   - `# <Channel> — how to respond` — channel guidance (preamble stripped)
4. Summarize-if-idle: if last_active > 60 min ago, seal segment + generate summary
5. Load context: latest rolling summary + active segment messages
6. agent.run_stream(user_text, message_history=context)
7. For each stream event:
   - TextDelta -> yield token to client
   - ToolCallEvent -> yield tool_call event
   - ToolResultEvent -> yield tool_result event
8. Append all messages (user + assistant + tool) to active segment
9. Fire-and-forget: extract_and_save_memories() as asyncio background task
10. Send {"type": "done", "cost_usd": ...}
```

### Continuous conversation model

Marcel uses a single continuous conversation per (user, channel) pair. There are no sessions — the conversation never ends. Instead, it's managed through **segments** and **rolling summaries**:

- **Segments**: The active conversation is stored as append-only JSONL segments. When a segment reaches 500 messages or 500KB, it rotates to a new file.
- **Idle summarization**: When the conversation is idle for 60+ minutes, the active segment is sealed, tool results are stripped, and a Haiku-generated summary is saved. The summary incorporates the previous summary, creating a **rolling summary chain** that preserves the full conversation arc while naturally fading old details.
- **`/forget` command**: Manually triggers the same summarization process, letting users start fresh without losing context.
- **Search index**: Every user/assistant message is keyword-indexed for mid-conversation recall via the `marcel(action="search_conversations")` tool.

### Memory system

Memory files use YAML frontmatter with typed metadata (`schedule`, `preference`, `person`, `reference`, `household`, `feedback`). The system prompt contains a **compact memory index** — one line per file (name + description) — not the full bodies. The agent loads specific entries on demand via `marcel(action="read_memory", name="...")` or searches across them with `marcel(action="search_memory", query="...")`. This keeps the prompt small regardless of how many memories the user has accumulated and teaches the model to reach for memory lookups intentionally instead of relying on pre-loaded content. Schedule memories auto-expire past their date. The `_household` pseudo-user holds shared family memories included in all users' context. See [storage.md](storage.md#memory-file-memorytopicmd) for the full file format and API.

### Memory extraction

Runs after every turn as a fire-and-forget `asyncio.create_task` that never blocks the response. A Haiku-powered pydantic-ai Agent is given a system prompt that asks it to return a JSON array of memory operations (`create` / `update`); the caller applies them directly to disk. Existing memory headers are included in the prompt so the agent can update instead of duplicating. User corrections (`"don't do X"`) and non-obvious confirmations (`"yes exactly"`) are captured as `feedback`-type memories with a **Why** / **How to apply** structure for later reuse. See [storage.md](storage.md#memory-extraction-background) for the full lifecycle.

### Artifacts

When a response contains rich content (calendars, checklists, tables, charts), the Telegram webhook stores it as an **artifact** — a JSON file with a unique ID, content type, and the rendered content. The Mini App (Telegram WebApp) fetches artifacts for display in a viewer and gallery. See [artifacts.md](artifacts.md) for details.

### Observability

Optional LLM tracing via OpenTelemetry + Phoenix (Arize). When `MARCEL_TRACING_ENABLED=true`, all pydantic-ai agent calls are instrumented with OpenInference spans and exported to the configured endpoint (`MARCEL_TRACING_ENDPOINT`, default `http://localhost:6006`). Useful for debugging agent reasoning and monitoring token usage.

## Running locally

```bash
make serve
```

Starts the `marcel-dev` Docker container on `0.0.0.0:${MARCEL_DEV_PORT:-7421}` with `uvicorn --reload` and `./src` bind-mounted. See the [self-modification docs](self-modification.md) for the production watchdog setup and the unified (dev + prod) restart flow.
