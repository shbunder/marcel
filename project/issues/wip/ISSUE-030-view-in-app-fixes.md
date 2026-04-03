# ISSUE-030: Fix "View in App" — Content Truncation & Wrong Message

**Status:** WIP
**Created:** 2026-04-03
**Assignee:** Unassigned
**Priority:** High
**Labels:** bug

## Capture
**Original request:** the "view in app button" on telegram always shows the last result, also it does not show to a very nice and fancy visualisation, it more or less looks the same as the orginal answer. I was hoping for rich visual UI elements to slide up showing table or registries with Icons and everything. Either through generative UI I hoped for a very nice visualisation adapted to the data shown. Or if this is not possible, with precreated rich elements ready for each integration.

**Follow-up Q&A:**
- User showed screenshot: calendar response ("Sunday 6 April — Kids return from Weekend VdB at 10:00…") renders fine in the Telegram bubble, but "View in app" only shows the intro line "Here's what's on for next week (6-12 April)" — all events are missing.
- Salary estimate response also has a "View in app" button, but tapping it shows the calendar response (the last one) instead of the salary data.

**Resolved intent:** Two bugs in the Viewer / Mini App flow. (1) `_extract_last_assistant` in `conversations.py` uses `raw.find('\n\n**', start)` to detect the end of a turn, but this matches any bold text after a blank line (e.g. `**Sunday 6 April**`), truncating the response to just the intro line. (2) Every "View in app" button embeds only the conversation ID, and the API always returns the last assistant message (`rfind`), so older buttons within the same conversation all resolve to the newest response. A third concern — richer visual widgets — is captured separately in ISSUE-031.

## Description

### Bug 1 — Content truncation (critical)

`_extract_last_assistant()` in `src/marcel_core/api/conversations.py:77-86` finds the end of the assistant turn with:

```python
next_turn = raw.find('\n\n**', start)
```

This incorrectly matches bold markdown within the response itself (date headers like `**Sunday 6 April**`, bold event titles). The fix: match only actual turn markers — `\n\n**Marcel:**` or `\n\n**User:**`.

### Bug 2 — Always shows last message

`rich_content_markup()` in `bot.py` builds the URL as `?conversation={conversation_id}`. The API endpoint uses `rfind('**Marcel:** ')` to always return the last assistant message. When a conversation has multiple rich responses, every "View in app" button shows the same (latest) content.

Fix: include a turn index in the URL (e.g. `?conversation=...&turn=3`) and make `_extract_last_assistant` accept an optional turn number to extract a specific message instead of always the last.

## Tasks
- [✓] ISSUE-030-a: Fix `_extract_last_assistant` turn delimiter to only match `**Marcel:**` / `**User:**`
- [✓] ISSUE-030-b: Add `turn` parameter to `/api/message/{conversation_id}` endpoint
- [✓] ISSUE-030-c: Include turn index in `rich_content_markup()` URL and count assistant turns in webhook
- [✓] ISSUE-030-d: Update `Viewer.tsx` to pass `turn` param to the API
- [✓] ISSUE-030-e: Test both fixes end-to-end (266 tests pass, 7 new extraction tests)

## Relationships
- Related to: ISSUE-031 (rich visual widgets — separate scope)

## Implementation Log
### 2026-04-03 — LLM Implementation
**Action**: Fixed both View-in-app bugs
**Files Modified**:
- `src/marcel_core/api/conversations.py` — Renamed `_extract_last_assistant` → `_extract_assistant_message`; fixed turn delimiter from `\n\n**` (matches any bold) to regex matching only `**Marcel:**` / `**User:**` turn markers; added `turn` param for extracting specific messages; added `turn` query param to API endpoint
- `src/marcel_core/telegram/bot.py` — `rich_content_markup()` accepts `turn` param, embeds in URL
- `src/marcel_core/telegram/formatting.py` — `web_app_url_for()` accepts `turn` param
- `src/marcel_core/telegram/webhook.py` — Counts assistant turns before appending, passes turn index to `_format_response`; updated callback handler import
- `src/web/src/App.tsx` — Reads `turn` query param from URL, passes to `<Viewer>`
- `src/web/src/components/Viewer.tsx` — Accepts `turn` prop, includes in API fetch URL
- `tests/core/test_telegram.py` — Added 7 tests for `_extract_assistant_message` covering truncation bug, turn indexing, out-of-range, and multi-turn extraction
**Commands Run**: `make check`, `pytest tests/`
**Result**: 266 tests pass; only pre-existing pyright errors in icloud/watchdog remain
