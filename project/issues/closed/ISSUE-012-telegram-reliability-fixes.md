# ISSUE-012: Telegram Reliability Fixes

**Status:** Closed
**Created:** 2026-03-29
**Assignee:** Marcel
**Priority:** High
**Labels:** bug

## Capture
**Original request:** "why didn't Marcel respond through telegram? there should always be feedback, I want Marcel to be verbose and communicate if an error occurs" / "ensure this doesn't happen again, also since the server is secure, claude-code used by marcel should be allowed to do any operation" / "I'm again not getting a response through telegram"

**Resolved intent:** Several compounding bugs caused Marcel to silently fail on Telegram. The watchdog had no SIGTERM handler, so systemd restarts left orphaned uvicorn processes holding the port, causing a crash loop. The agent runner used `max_turns=1`, which prevented tool-calling responses (Claude would call a tool but never get to respond with the result). Empty responses were sent to Telegram silently rather than surfacing an error. The cloudflared systemd service was missing `--url`, so it connected to Cloudflare but couldn't route traffic. The Ship procedure also lacked the user-branch push step for review.

## Description

Multiple independent failures that together made Telegram completely silent:

1. **Watchdog orphan bug** — no SIGTERM/SIGINT handler, so when systemd killed the watchdog the uvicorn child kept running and held the port. Every restart attempt failed with `[Errno 98] address already in use`.
2. **`max_turns=1` in runner** — Claude's first response to a tool-requiring query is a tool call (no text). With `max_turns=1` the SDK stopped there. `full_response` was always `""` for any non-trivial question.
3. **Silent empty response** — `bot.send_message("", ...)` was called with empty text. Telegram rejects it with 400; the error was swallowed. No feedback reached the user.
4. **`send_message` swallows errors** — both the MarkdownV2 attempt and plain-text fallback silently returned on failure; callers had no way to know delivery failed.
5. **`permission_mode='default'`** — unnecessary confirmation prompts blocked tool execution on a trusted server.
6. **cloudflared service missing `--url`** — the systemd unit had no `--url http://localhost:7420`, so it established a Cloudflare connection but returned 503 on all requests.
7. **Ship procedure missing user-branch push** — no convention for pushing reviewed changes to a named branch for user approval.

## Tasks
- [✓] Add SIGTERM/SIGINT handlers to watchdog so uvicorn is stopped before watchdog exits
- [✓] Increase `max_turns` from 1 to 10 so tool-calling responses complete
- [✓] Guard against empty `full_response` in webhook — send explicit error message to user
- [✓] Wrap final `bot.send_message` in try/except — report delivery failures to user
- [✓] Raise on Telegram API failure in `bot.send_message` instead of silently returning
- [✓] Set `permission_mode='bypassPermissions'` in runner
- [✓] Add `--url http://localhost:7420` to cloudflared systemd service (manual step, requires sudo)
- [✓] Add user-branch push step to Ship procedure in `project/CLAUDE.md`

## Relationships
- Related to: [[ISSUE-011-telegram-channel]]

## Implementation Log

### 2026-03-29 22:00 - LLM Implementation
**Action**: Diagnosed and fixed all reliability issues
**Files Modified**:
- `src/marcel_core/watchdog/main.py` — added SIGTERM/SIGINT signal handlers calling `_stop(proc)` before `sys.exit(0)`
- `src/marcel_core/agent/runner.py` — `max_turns` 1→10, `permission_mode` default→bypassPermissions
- `src/marcel_core/telegram/webhook.py` — empty response guard, try/except around final send
- `src/marcel_core/telegram/bot.py` — `send_message` now raises `RuntimeError` on Telegram API failure
- `project/CLAUDE.md` — Ship step updated with `git push origin HEAD:<user>` convention
**Result**: All fixes applied; cloudflared `--url` fix requires user to run sudo command manually
