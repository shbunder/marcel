# ISSUE-052: Rich Content Delivery

**Status:** WIP
**Created:** 2026-04-10
**Assignee:** Claude
**Priority:** High
**Labels:** feature, telegram, tools

## Capture
**Original request:** "Rich content delivery — chart generation tool, native Telegram photos, tighten View in app button"

**Follow-up Q&A:** User found that the "View in app" button was showing plain formatted text, not actual images or interactive elements. The mini-app was just re-rendering markdown — no real value over the Telegram bubble. User chose Option A: make it real by adding server-side image generation, sending photos natively in Telegram, and restricting the "View in app" button to genuinely interactive content only.

**Resolved intent:** Close the gap between the artifact infrastructure (ISSUE-050) and actual rich content delivery. Add a matplotlib-based `generate_chart` tool so Marcel can create real images, send them as native Telegram photos via `sendPhoto`, and tighten the "View in app" button to only appear for interactive content (checklists) where the mini-app genuinely adds value.

## Description

ISSUE-050 built the artifact system and mini-app redesign, but the content pipeline still only produces text. When the "View in app" button opens, users see the same markdown in a slightly different wrapper — not worth a tap.

This issue adds three things:
1. **Chart generation tool** (`generate_chart`) — executes matplotlib code server-side, renders to PNG, stores as image artifact, and sends directly as a Telegram photo during the agent turn.
2. **Telegram `sendPhoto`** — new bot.py function for native photo delivery (multipart upload or file_id/URL).
3. **Tighter button logic** — "View in app" only appears for checklists (interactive checkboxes). Calendars and tables render well enough in Telegram's native HTML and no longer trigger the button.

## Tasks
- [✓] Add `matplotlib>=3.9.0` to pyproject.toml dependencies
- [✓] Add `send_photo()` to `src/marcel_core/channels/telegram/bot.py`
- [✓] Create `src/marcel_core/tools/charts.py` with `generate_chart` tool
- [✓] Register `generate_chart` in `src/marcel_core/harness/agent.py`
- [✓] Add `needs_mini_app()` to bot.py — only returns True for checklists
- [✓] Update `_format_response()` in webhook.py to use `needs_mini_app()` for button decisions
- [✓] Verify: tests pass (679), frontend builds

## Relationships
- Depends on: [[ISSUE-050-artifact-mini-app]] (artifact storage and mini-app viewer)
- Related to: [[ISSUE-026-agui-rich-content]] (rich content system)

## Comments

## Implementation Log
