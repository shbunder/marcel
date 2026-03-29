# ISSUE-013: Fix Telegram Agent Hanging on Requests

**Status:** Closed
**Created:** 2026-03-29
**Assignee:** Claude Code
**Priority:** High
**Labels:** bug, telegram, agent

## Capture
**Original request:** "I have the feeling when I ask for features to marcel on telegram, it sometimes hangs? I asked for an update, but it's not performing the update, what is wrong? could it be that under the hood claude is asking for feedback or something?"

**Resolved intent:** The Telegram agent was silently hanging in two ways: (1) the background memory extraction task used `permission_mode='default'` which can prompt for interactive terminal confirmation — something impossible when running as a headless systemd service, causing the task to block indefinitely; (2) there was no timeout on the `stream_response` call, so any slow/stuck Claude API call would leave the user waiting forever after the initial "Got it, working on it..." acknowledgement.

## Description

Two bugs caused the Telegram agent to appear frozen after sending an initial ack:

1. **`permission_mode='default'` in memory extraction** — `memory_extract.py` used `default` mode, which may require interactive permission confirmation. Running as a service with no TTY means this blocks forever.
2. **No timeout on `stream_response`** — `webhook.py` iterated the agent stream with no `asyncio.wait_for` wrapper. If the Claude API stalled or the agent consumed all 10 turns trying to execute an unachievable task, the user would wait indefinitely.

## Tasks
- [✓] Change `permission_mode='default'` → `'bypassPermissions'` in `memory_extract.py`
- [✓] Wrap `stream_response` with `asyncio.wait_for(..., timeout=120.0)` in `webhook.py`

## Implementation Log

### 2026-03-29 - Claude Code
**Action**: Fixed both hang vectors
**Files Modified**:
- `src/marcel_core/agent/memory_extract.py` — changed `permission_mode='default'` to `'bypassPermissions'`
- `src/marcel_core/telegram/webhook.py` — wrapped agent stream collection in `asyncio.wait_for(_collect(), timeout=120.0)`, with a clean user-facing error on `TimeoutError`
**Result**: Memory extraction no longer blocks on permission prompts; Telegram responses now time out cleanly after 2 minutes instead of hanging indefinitely
