# ISSUE-045: Per-Session History Storage

**Status:** Closed
**Created:** 2026-04-10
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature, architecture

## Capture
**Original request:** "there is central History.jsonl, but what when we build an ios-app where you can manage your own sessions. Shouldn't history be saved by user / channel / session-id (where for a channel without multi-session a session-id is created server-side for each user everytime a new session logic is triggered) or will you maintain one huge file? (is that scaleable?)"

**Follow-up Q&A:** None

**Resolved intent:** Restructure the conversation history storage from a single flat `history.jsonl` per user to a hierarchical layout organized by channel and session. This prepares for multi-session channels (iOS app) where users create and manage their own sessions, while keeping single-session channels (Telegram) working with server-generated session IDs. The current approach of reading the entire file and filtering by `conversation_id` is O(n) per turn and will not scale.

## Description

Currently `memory/history.py` stores all conversation history in one file per user (`data/users/{slug}/history.jsonl`). Every read operation scans the entire file and filters by `conversation_id`. This has two problems:

1. **Scalability** — as history grows across months and multiple channels, the file becomes a bottleneck. Every turn pays O(n) to scan all messages from all channels/sessions.
2. **Multi-session support** — an iOS app (or any multi-session client) needs to list, create, resume, and delete sessions independently. A flat file makes session management (list sessions, delete a session, get session metadata) expensive and fragile.

### Proposed structure

```
data/users/{slug}/history/
  {channel}/
    {session_id}.jsonl       # one file per session
    {session_id}.meta.json   # session metadata (title, created_at, last_active, ...)
```

### Session ID generation

- **Multi-session channels** (iOS, web): client creates/selects sessions; server validates
- **Single-session channels** (Telegram): server generates a new session ID when a new-conversation trigger fires (e.g., idle timeout, explicit `/new` command)
- Current `conversation_id` format (`2026-04-10T09-44`) already serves this role and can be retained as the session ID

## Tasks

- [✓] Design session metadata schema (title, created_at, last_active, channel, message_count)
- [✓] Create session CRUD in history.py (create_session, list_sessions, delete_session, get_session_meta)
- [✓] Add session listing API (`GET /v2/sessions`)
- [✓] Add session creation API (`POST /v2/sessions`)
- [✓] Add session deletion API (`DELETE /v2/sessions/{id}`)
- [✓] Migrate `append_message()`, `read_history()`, `read_recent_turns()` to per-session layout
- [✓] Write migration utility (`migrate_legacy_history`) for existing `history.jsonl`
- [✓] Update `chat_v2.py` and `runner.py` for channel-aware session storage
- [✓] Add tests (15 new: session CRUD, migration, legacy fallback, cross-channel isolation)
- [✓] Document new storage layout in `docs/storage.md`
- [ ] Verify end-to-end: deploy and confirm Telegram messages create per-session files

## Subtasks

## Relationships
- Related to: [[ISSUE-044-telegram-session-history]]
- Related to: [[ISSUE-026-agui-rich-content]]

## Comments

## Implementation Log
### 2026-04-10 — LLM Implementation
**Action**: Full implementation of per-session history storage
**Files Modified**:
- `src/marcel_core/memory/history.py` — rewrote to per-session files with SessionMeta, CRUD, migration, legacy fallback
- `src/marcel_core/harness/runner.py` — pass channel to append_message
- `src/marcel_core/api/sessions.py` — new REST endpoints (GET/POST/DELETE /v2/sessions)
- `src/marcel_core/api/chat_v2.py` — create v2 sessions alongside legacy conversations
- `src/marcel_core/main.py` — register sessions router
- `tests/memory/test_history.py` — 30 tests (15 new)
- `docs/storage.md` — JSONL history format, session metadata, session API docs
**Commands Run**: `make check`
**Result**: 763 tests passing, 0 pyright errors, 93% coverage
