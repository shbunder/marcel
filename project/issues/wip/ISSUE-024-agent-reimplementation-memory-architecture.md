# ISSUE-024: Agent Reimplementation — ClaudeSDKClient + Memory Architecture

**Status:** WIP
**Created:** 2026-04-02
**Assignee:** Marcel
**Priority:** High
**Labels:** feature, agent, memory, architecture

## Capture

**Original request:** Deep analysis of Claude Code source (~/repos/claude-code) to identify improvements for Marcel. Conversation evolved into a concrete reimplementation plan after discovering that (1) Marcel underuses the Agent SDK — calling `query()` one-shot instead of `ClaudeSDKClient` with persistent sessions, and (2) Marcel's memory system is its key differentiator as a multi-user agent, but the current implementation is naive (append-only, no deduplication, regex-based extraction, no relevance filtering).

**Follow-up Q&A:**
- User confirmed Marcel should keep its own memory system (not rely on Claude Code's auto-memory) because it's multi-user
- User wants the focus to be on long-term memory and cross-session memory access
- User wants to learn from Claude Code source to inspire the reimplementation

**Resolved intent:** Rewrite Marcel's agent layer to use `ClaudeSDKClient` for persistent, multi-turn sessions (gaining prompt cache reuse, interrupt support, and SDK-managed context/compaction), and redesign the memory system with typed frontmatter, relevance-based selection, cross-session memory search, household shared memory, and agent-based extraction — making memory Marcel's core differentiator as a multi-user personal assistant.

## Description

### Current Problems

1. **`query()` is one-shot.** Each message creates a new SDK process. Conversation history is manually loaded into the system prompt. No prompt cache reuse across turns. No ability to interrupt. The SDK handles compaction/retry/memory internally but Marcel rebuilds context from scratch every time.

2. **Memory is append-only.** `memory_extract.py` uses regex parsing of a structured text response. No deduplication — the same fact can be appended repeatedly. No awareness of existing memories during extraction. No expiry — schedule facts persist forever. All memory is loaded into every prompt regardless of relevance.

3. **No cross-session memory access.** The agent can only use memories pre-loaded at session start. If a relevant memory wasn't in the top-level dump, it's invisible. No way to search memories mid-conversation.

4. **Conversation storage duplicates SDK state.** Marcel stores conversations as markdown files AND the SDK maintains its own session state. The manual history injection into the system prompt is redundant when using `ClaudeSDKClient`.

### Design (inspired by Claude Code patterns)

#### Part A: ClaudeSDKClient Migration

Replace `query()` with persistent `ClaudeSDKClient` sessions managed by a `SessionManager`.

- `SessionManager` holds a dict of `(user_slug, conversation_id) → ActiveSession`
- `ActiveSession` wraps a `ClaudeSDKClient` instance with metadata (user, channel, last_active)
- On first message: `client.connect(prompt)` with system prompt containing user profile + selected memories
- On follow-up: `client.query(user_text)` — SDK maintains conversation context
- Idle cleanup: disconnect sessions after configurable timeout (default 1h)
- Conversation markdown files become an audit log only — no longer loaded into context

Key gains: prompt cache reuse, SDK-managed compaction/retry, interrupt support, model switching, MCP reconnection.

#### Part B: Typed Memory with Frontmatter

Replace bare markdown memory files with frontmatter-typed files, inspired by Claude Code's `memdir/memoryTypes.ts` and `memoryScan.ts`.

Memory types for a personal assistant context:
- `schedule` — time-bound events/deadlines (has `expires` field, auto-pruned)
- `preference` — likes, dislikes, habits, communication style (long-lived)
- `person` — facts about people in the user's life (names, relationships, birthdays)
- `reference` — pointers to external systems, accounts, addresses
- `household` — shared facts across all users (stored under `_household` pseudo-user)

Frontmatter format:
```yaml
---
name: dentist_appointment
type: schedule
user: alice
expires: 2026-04-15
confidence: told  # told | observed | inferred
---
```

#### Part C: Relevance-Based Memory Selection

Inspired by Claude Code's `findRelevantMemories.ts`. Instead of dumping all memory into the prompt:

1. Scan frontmatter headers (filename, type, description, mtime) — cheap, no full reads
2. Side-query a fast model (Haiku) with the user's message + header manifest
3. Load full content only for selected memories (up to ~8)
4. Include household memories in the candidate pool
5. Attach staleness warnings for old memories (from Claude Code's `memoryAge.ts`)

#### Part D: Memory Search MCP Tool

New `memory_search` tool exposed alongside `integration` and `notify`. Lets the agent actively search memory mid-conversation when pre-loaded context isn't enough.

Parameters: `query` (keyword/semantic), `type` (optional filter), `user` (defaults to current, can include `_household`).

#### Part E: Agent-Based Memory Extraction

Replace regex-based extraction with a file-tool agent (inspired by Claude Code's `extractMemories.ts`):

- Run as a cheap `query()` call (Haiku, max_turns=3) with `claude_code` tools preset
- CWD set to user's memory directory
- Agent reads existing memory files, writes/updates using Edit/Write tools
- Receives manifest of existing memories (prevents duplicates)
- Writes typed frontmatter
- Sets `expires` on schedule-type memories
- Skip extraction if main agent already wrote memories during the turn

#### Part F: Memory Lifecycle

- Auto-expiry: periodic task prunes `schedule` memories past their `expires` date
- Staleness notes: memories older than N days get a freshness warning when loaded
- Index cap: 200 lines max (from Claude Code's `MAX_ENTRYPOINT_LINES`)
- Household memory: `_household` pseudo-user, included in all users' relevance selection

## Tasks

### Part A — ClaudeSDKClient Migration
- [✓] ISSUE-024-a: Create `SessionManager` class + `ActiveSession` dataclass in `agent/sessions.py`
- [✓] ISSUE-024-b: Rewrite `runner.py` — `stream_response` uses `SessionManager.get_or_create()`, calls `client.query()` + `client.receive_response()`
- [✓] ISSUE-024-c: Surface SDK events to callers — `ResultMessage` (cost/tokens) via `TurnResult` dataclass
- [✓] ISSUE-024-d: Add idle session cleanup (background task, configurable timeout)
- [✓] ISSUE-024-e: Update `chat.py` — stop injecting history into system prompt; keep conversation markdown as audit log only
- [✓] ISSUE-024-f: Update `context.py` — system prompt becomes profile + memory + channel hint (no history)

### Part B — Typed Memory
- [✓] ISSUE-024-g: Define `MemoryHeader` dataclass and `MemoryType` enum in `storage/memory.py`
- [✓] ISSUE-024-h: Add `scan_memory_headers()` — reads frontmatter only from all `.md` files in a user's memory dir
- [✓] ISSUE-024-i: Add `format_memory_manifest()` — one-line-per-file text for prompts
- [ ] ISSUE-024-j: Migrate existing memory files to frontmatter format (one-time migration utility)

### Part C — Relevance Selection
- [✓] ISSUE-024-k: Create `agent/memory_select.py` — side-query to Haiku that picks top-N relevant memory files from manifest
- [✓] ISSUE-024-l: Integrate into `context.py` — replace `_load_all_memory()` with scan-based loading, capped at MAX_MEMORY_FILES
- [✓] ISSUE-024-m: Add staleness warnings for memories older than configurable threshold

### Part D — Memory Search Tool
- [ ] ISSUE-024-n: Add `memory_search` MCP tool to `skills/tool.py` alongside `integration` and `notify`
- [ ] ISSUE-024-o: Implement keyword search across memory files (grep frontmatter + content)

### Part E — Agent-Based Extraction
- [ ] ISSUE-024-p: Rewrite `memory_extract.py` — use `query()` with Haiku + `claude_code` tools preset + CWD=memory dir
- [ ] ISSUE-024-q: Pass existing memory manifest to extraction agent (deduplication)
- [ ] ISSUE-024-r: Skip extraction if main agent already wrote to memory dir during turn

### Part F — Memory Lifecycle
- [ ] ISSUE-024-s: Add auto-expiry: prune `schedule` memories past `expires` date (runs before extraction)
- [ ] ISSUE-024-t: Add `_household` pseudo-user support — included in all users' memory selection
- [ ] ISSUE-024-u: Add memory index cap (200 lines) with truncation warning

### Testing & Shipping
- [✓] ISSUE-024-v: Tests for SessionManager (create, reuse, idle cleanup, disconnect)
- [✓] ISSUE-024-w: Tests for typed memory (scan, manifest, frontmatter parsing, staleness)
- [✓] ISSUE-024-x: Tests for relevance selection (small-set load-all, household inclusion/exclusion, parse_selection)
- [ ] ISSUE-024-y: Tests for memory search tool
- [ ] ISSUE-024-z: Tests for agent-based extraction (mock agent, deduplication, skip logic)
- [ ] ISSUE-024-aa: Update docs (architecture.md, storage.md), run `make check`, version bump

## Relationships
- Related to: [[ISSUE-003-agent-loop]] (original agent loop implementation — this replaces it)
- Related to: [[ISSUE-018-telegram-coder-mode]] (coder mode also uses claude_agent_sdk — may need session awareness)
- Related to: [[ISSUE-023-redesign-skill-system]] (MCP tool registration pattern — memory_search follows same pattern)

## Comments

### 2026-04-02 — Design Analysis
Conducted deep analysis of Claude Code source at ~/repos/claude-code. Key findings that shaped this design:

1. **`ClaudeSDKClient` vs `query()`**: The SDK's `ClaudeSDKClient` class maintains persistent sessions with full conversation state, prompt cache reuse, and bidirectional control (interrupt, model switch, MCP management). Marcel's current `query()` usage discards all of this — rebuilding context from scratch on every message.

2. **Memory extraction pattern**: Claude Code runs a forked agent with restricted tool permissions (Read/Grep/Glob + Edit/Write only in memory dir). It passes a manifest of existing memories so the agent can update rather than duplicate. Marcel's regex-based extraction is fragile and unaware of existing state.

3. **Relevance selection**: Claude Code's `findRelevantMemories.ts` uses a Sonnet side-query to pick the top-5 relevant memories from a manifest of headers. This keeps the prompt small even with hundreds of memory files.

4. **Memory taxonomy**: Claude Code uses 4 types (user, feedback, project, reference) with frontmatter. Marcel should adapt this for personal assistant context with different types (schedule, preference, person, reference, household).

5. **Staleness**: Claude Code's `memoryAge.ts` attaches freshness warnings to old memories. Schedule-type memories need auto-expiry — a dentist appointment from 3 months ago is noise.

### 2026-04-02 — Scoping Note
This is a large issue. Implementation should proceed in phases: Part A (session migration) first as it's the foundation, then Parts B+C (typed memory + selection) as they're tightly coupled, then D+E (search + extraction) which build on the new memory format, and finally F (lifecycle) as polish.

## Implementation Log

### 2026-04-02 — Part A: ClaudeSDKClient Migration
**Action**: Replaced one-shot `query()` calls with persistent `ClaudeSDKClient` sessions managed by a `SessionManager` singleton.
**Files Modified**:
- `src/marcel_core/agent/sessions.py` — Created. `SessionManager` manages `(user_slug, conv_id) → ActiveSession` dict. Each `ActiveSession` wraps a `ClaudeSDKClient` with idle timeout cleanup. Background cleanup loop runs every 5 minutes. Module-level `session_manager` singleton.
- `src/marcel_core/agent/runner.py` — Rewritten. `stream_response` now calls `session_manager.get_or_create()` then `client.query()` + `client.receive_response()`. Yields `TurnResult` dataclass with cost/usage metadata from `ResultMessage`. No longer imports or calls `claude_agent_sdk.query()` directly.
- `src/marcel_core/agent/context.py` — Removed `conversation_id` parameter and history loading. System prompt is now profile + memory + channel hint only. Conversation history is maintained by the SDK internally.
- `src/marcel_core/api/chat.py` — Updated to handle `TurnResult` from stream. Done message now includes `cost_usd` and `turns` when available. Conversation markdown persisted as audit log only.
- `src/marcel_core/telegram/webhook.py` — Updated for `TurnResult`. `/new` and auto-new now call `session_manager.reset_user()` to disconnect SDK sessions.
- `src/marcel_core/main.py` — Wired session manager lifecycle into FastAPI lifespan: `start_cleanup_loop()` on startup, `stop_cleanup_loop()` + `disconnect_all()` on shutdown.
- `src/marcel_core/agent/__init__.py` — Exports `SessionManager`, `ActiveSession`, `TurnResult`, `session_manager`.
- `tests/core/test_agent.py` — Rewrote runner tests to mock `SessionManager` instead of `claude_agent_sdk.query`. Added 6 new `TestSessionManager` tests (create, reuse, disconnect, reset_user, cleanup_idle, disconnect_all). Added `TurnResult` cost test for WebSocket. Updated context tests to remove conversation_id.
**Commands Run**: `make check` — lint/format pass, 0 type errors in changed files (5 pre-existing in icloud/watchdog), 131 tests pass.
**Next**: Part B — Typed memory with frontmatter.

### 2026-04-02 — Part B+C: Typed Memory + Relevance Selection
**Action**: Added typed memory infrastructure with frontmatter parsing and relevance-based memory selection.
**Files Modified**:
- `src/marcel_core/storage/memory.py` — Rewrote. Added `MemoryType` enum (schedule, preference, person, reference, household), `MemoryHeader` dataclass, `parse_frontmatter()` regex parser, `scan_memory_headers()` (reads first 2KB per file for frontmatter, returns sorted headers), `format_memory_manifest()` (one-line-per-file text), `memory_age_days()` and `memory_freshness_note()` staleness helpers. All original CRUD operations preserved unchanged.
- `src/marcel_core/agent/memory_select.py` — Created. `select_relevant_memories()` scans headers from user + `_household`, loads all for small sets (≤10 files), or asks Haiku to select top-8 from manifest for large sets. `_parse_selection()` handles JSON array parsing with code fence stripping. Falls back to loading all on API failure.
- `src/marcel_core/agent/context.py` — Rewrote `_load_memory()` to use `scan_memory_headers()` instead of index-based loading. Caps at 15 files, sorted by mtime, includes `_household` shared memories, appends freshness warnings.
- `src/marcel_core/storage/__init__.py` — Exports new types: `MemoryHeader`, `MemoryType`, `format_memory_manifest`, `memory_age_days`, `memory_freshness_note`, `parse_frontmatter`, `scan_memory_headers`.
- `src/marcel_core/agent/__init__.py` — Exports `extract_and_save_memories`, `select_relevant_memories`.
- `tests/core/test_storage.py` — Added 19 tests: `TestParseFrontmatter` (4), `TestMemoryType` (2), `TestScanMemoryHeaders` (4), `TestFormatMemoryManifest` (2), `TestMemoryStaleness` (7).
- `tests/core/test_agent.py` — Added 9 tests: `TestParseSelection` (5), `TestSelectRelevantMemories` (4).
**Bug fixed**: Slug detection in `memory_select.py` and `context.py` used `'_household' in str(header.filepath)` which incorrectly matched when the filesystem path (e.g. pytest tmp dir name) contained `_household`. Fixed to use `header.filepath.parent.parent.name` which directly extracts the slug from the directory structure.
**Commands Run**: `ruff format`, `ruff check --fix`, `pyright` (0 errors on changed files), `pytest tests/ -v` — 160 tests pass.
**Next**: Part D — Memory search tool.
