# ISSUE-011: Telegram Channel Integration

**Status:** Closed
**Created:** 2026-03-29
**Assignee:** Claude
**Priority:** Medium
**Labels:** feature, integration, channel

## Capture
**Original request:** "let's now build the a second channel integration, allow to talk to marcel using telegram and assist with the setup for user shaun, add documentation so other users can link their telegram"

**Resolved intent:** Add Telegram as a second input/output channel for Marcel. Users can message their Marcel bot on Telegram and receive responses as if they were using the CLI. The integration is channel-based (not skill-based): incoming Telegram messages are routed through the existing agent loop with `channel="telegram"`, and responses are sent back via the Telegram Bot API. Setup is documented so Shaun and future users can link their own Telegram account. Session state (active conversation per chat) is persisted across server restarts.

## Description

Telegram acts as a channel peer to the existing CLI. A webhook endpoint receives updates from Telegram, looks up the Marcel user for the incoming chat ID (via `TELEGRAM_USER_MAP` env var), runs the agent, and sends the full response back. Conversation continuity is maintained by storing the active `conversation_id` per chat in `data/telegram/sessions.json`.

**Key design decisions:**
- Use webhooks (not polling) — cleaner, no background thread required
- User linking via `TELEGRAM_USER_MAP=chat_id:user_slug` env var — simple for a family setup
- Active conversation stored in `data/telegram/sessions.json` — persists across restarts
- Response sent as a single message after streaming completes (Telegram has no streaming)
- MarkdownV2 parse mode with plain-text fallback if formatting is rejected

## Tasks
- [✓] ISSUE-011-a: Create telegram/bot.py — Telegram Bot API client
- [✓] ISSUE-011-b: Create telegram/sessions.py — session state management
- [✓] ISSUE-011-c: Create telegram/webhook.py — FastAPI webhook router
- [✓] ISSUE-011-d: Update telegram/__init__.py — module exports
- [✓] ISSUE-011-e: Update main.py — register telegram router
- [✓] ISSUE-011-f: Update .env — add TELEGRAM_BOT_TOKEN, TELEGRAM_USER_MAP
- [✓] ISSUE-011-g: Write tests/core/test_telegram.py
- [✓] ISSUE-011-h: Write docs/channels/telegram.md
- [✓] ISSUE-011-i: Update mkdocs.yml nav

## Relationships
- Related to: [[ISSUE-003-agent-loop]]

## Implementation Log

### 2026-03-29 - LLM Implementation
**Action**: Implemented full Telegram channel integration
**Files Modified**:
- `src/marcel_core/telegram/__init__.py` — module entrypoint, exports router
- `src/marcel_core/telegram/bot.py` — Telegram Bot API client (send_message, set_webhook, delete_webhook, escape_markdown_v2)
- `src/marcel_core/telegram/sessions.py` — chat_id → user_slug lookup from TELEGRAM_USER_MAP env var; conversation_id persistence in data/telegram/sessions.json
- `src/marcel_core/telegram/webhook.py` — FastAPI router: POST /telegram/webhook; handles /start, unlinked chats, dispatches to agent loop
- `src/marcel_core/main.py` — registered telegram router
- `.env` — added TELEGRAM_BOT_TOKEN, TELEGRAM_USER_MAP, TELEGRAM_WEBHOOK_SECRET placeholders
- `tests/core/test_telegram.py` — 25 tests covering escape helper, session state, webhook routing
- `docs/channels/telegram.md` — full setup guide for Shaun and other users
- `mkdocs.yml` — added Channels > Telegram nav entry
**Commands Run**: `uv run pytest tests/core/ -v`
**Result**: 108/108 tests passed
**Next**: Close issue
