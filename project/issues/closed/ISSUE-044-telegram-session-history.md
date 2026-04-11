# ISSUE-044: Telegram Session History

**Status:** Closed
**Created:** 2026-04-10
**Assignee:** Claude
**Priority:** High
**Labels:** bug, feature

## Capture
**Original request:** "each time I send a message in telegram, marcel forget what I said the turn before, something seems off with session management"

**Follow-up Q&A:**
- User wants long-lived Telegram sessions that feel like one continuous conversation
- New sessions only on: (1) explicit `/new` command, (2) Marcel restart
- Use a generous conversation window for Telegram

**Resolved intent:** The pydantic-ai agent runner creates a fresh agent per turn but never loads prior conversation history into `message_history`, so every Telegram message is processed without context. Fix this by loading JSONL history and converting it to pydantic-ai `ModelMessage` format. Also increase the auto-new timeout to match the desired long-session UX, and clear Telegram sessions on startup so restarts create fresh conversations.

## Description

`stream_turn()` in `runner.py` has a TODO at line 140 where conversation history should be loaded but isn't. Each turn calls `agent.run_stream(user_text, deps=deps)` without passing `message_history`, so the LLM never sees prior turns.

The history is already being saved (via `memory/history.py`) but never loaded back. The `read_recent_turns()` function exists and is ready to use.

Additionally, `AUTO_NEW_HOURS = 6` in `sessions.py` is too aggressive for the desired UX — Telegram sessions should be long-lived. And sessions should be cleared on Marcel restart.

## Tasks
- [✓] Add `history_to_messages()` converter in `runner.py` — convert `HistoryMessage` to pydantic-ai `ModelRequest`/`ModelResponse`
- [✓] Load conversation history in `stream_turn()` and pass as `message_history` to `run_stream()`
- [✓] Increase Telegram auto-new timeout to 48h (effectively disabling it for normal use)
- [✓] Clear Telegram sessions on startup so restarts create fresh conversations
- [✓] Add tests for history conversion and history loading in stream_turn
- [✓] Exclude the current user message from loaded history (reordered: load history first, then append user message)

## Relationships
- Related to: [[ISSUE-031-migrate-to-pydantic-ai-harness]]

## Comments

## Implementation Log
### 2026-04-10 — LLM Implementation
**Action**: Implemented conversation history loading for pydantic-ai agent runner
**Files Modified**:
- `src/marcel_core/harness/runner.py` — Added `history_to_messages()` converter and `_HISTORY_TURNS` config; load prior turns via `read_recent_turns()` and pass as `message_history` to `agent.run_stream()`; reordered to load history before appending current user message
- `src/marcel_core/channels/telegram/sessions.py` — Increased `AUTO_NEW_HOURS` from 6 to 48; added `clear_all_sessions()` function
- `src/marcel_core/main.py` — Call `clear_all_sessions()` in lifespan startup
- `tests/harness/test_runner.py` — Added `TestHistoryToMessages` (5 tests) and `TestStreamTurnWithHistory` (2 tests)
- `tests/core/test_telegram.py` — Updated auto-new threshold tests for 48h; added `TestClearAllSessions` (3 tests)
**Commands Run**: `make check`
**Result**: All 732 tests passing, typecheck clean
