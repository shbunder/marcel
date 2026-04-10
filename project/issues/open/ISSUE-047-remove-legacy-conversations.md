# ISSUE-047: Remove Legacy Markdown Conversations

**Status:** Open
**Created:** 2026-04-10
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature, cleanup

## Capture
**Original request:** "does it make sense to retain ~/.marcel/users/{user-slug}/conversations? Can't we unify the history and parse the pure text content from the JSONL-files?"

**Follow-up Q&A:** None

**Resolved intent:** Remove the legacy markdown conversation system (`storage/conversations.py`, `conversations/` directories, dual-write code) and unify all history on the JSONL session system introduced in ISSUE-045. The REST endpoints that currently read from markdown files (`GET /conversations`, `GET /api/message/{id}`) should be rewritten to read from JSONL history instead. This eliminates redundant storage, simplifies the codebase, and makes JSONL the single source of truth.

## Description

With ISSUE-045 shipping per-session JSONL history, the legacy markdown conversation system is fully redundant. Currently both systems are written to in parallel ("dual-write during migration"). This issue removes the legacy side entirely:

### What to remove
- `src/marcel_core/storage/conversations.py` — the entire module
- `conversations/` re-exports from `storage/__init__.py`
- Dual-write code in `chat_v2.py` (lines 96-129: `storage.new_conversation`, `storage.append_turn`)
- Legacy conversation creation in `chat.py` and `telegram/webhook.py`
- `api/conversations.py` — replace with JSONL-backed equivalents or remove

### What to replace
- `GET /conversations` — rewrite to use `list_sessions()` from `memory.history`
- `GET /api/message/{conversation_id}` — rewrite to read from JSONL history (`read_history()`), extract assistant text
- `telegram/webhook.py` conversation creation — switch to `create_session()` from `memory.history`
- `chat.py` (v1) — this endpoint still uses `ClaudeSDKClient` and doesn't write to JSONL; it needs to either be migrated to write JSONL or be marked for deprecation

### Migration
- Run `migrate_legacy_history()` for all existing users to move data from `history.jsonl` to per-session files
- Existing `conversations/*.md` files can remain on disk (read-only archive) but are no longer written to or read by Marcel

## Tasks

- [ ] Remove dual-write code from `chat_v2.py` (storage.new_conversation, storage.append_turn)
- [ ] Rewrite `telegram/webhook.py` to use `create_session()` instead of `storage.new_conversation()`
- [ ] Rewrite `telegram/webhook.py` to read conversation context from JSONL instead of `load_conversation()`
- [ ] Rewrite `api/conversations.py` endpoints to use `list_sessions()` and `read_history()`
- [ ] Update or deprecate `chat.py` (v1) — either add JSONL writes or mark as deprecated
- [ ] Remove `storage/conversations.py` and its re-exports from `storage/__init__.py`
- [ ] Remove `tests/core/test_conversations.py` or rewrite for JSONL-backed endpoints
- [ ] Update `docs/storage.md` — remove legacy conversation format sections, mark as archived
- [ ] Add startup migration hook: auto-run `migrate_legacy_history()` for all users on first boot
- [ ] Verify end-to-end: Telegram messages, WebSocket chat, and Mini App all work without legacy files

## Subtasks

## Relationships
- Depends on: [[ISSUE-045-per-session-history]]
- Related to: [[ISSUE-046-tool-call-history]]

## Comments

## Implementation Log
