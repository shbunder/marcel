# ISSUE-041: CLI v2 harness routing and header simplification

**Status:** Closed
**Created:** 2026-04-09
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** cli, refactor

## Capture
**Original request:** Implicit — follow-on cleanup from v2 harness migration (ISSUE-037) and CLI scrolling work (ISSUE-040).

**Resolved intent:** Two small CLI improvements bundled together:
1. Route the CLI WebSocket connection through the v2 harness endpoint (`/v2/chat`) by default. The legacy `/ws/chat` path is retained via a `--v2` flag for compatibility testing. This makes the CLI consistent with Telegram, which was already routed through v2 in ISSUE-037.
2. Simplify the header widget: the welcome greeting was previously rendered inside the header box, taking up 2 extra rows. It was already moved to the first assistant chat message in ISSUE-040; the header widget still had the dead `welcome` field and the extra height. Removed both to reclaim vertical space.

## Description

### v2 harness routing
`config.rs`: `ws_url()` previously always connected to `/ws/chat`. The signature was updated to `ws_url(dev_mode: bool, use_v2: bool)` so callers can select the endpoint. In `app.rs` `use_v2 = true` is hardcoded (v2 is now the default); the `--v2` flag in `main.rs` is retained for compatibility but currently a no-op (the app always uses v2).

### Header simplification
`header.rs`: removed the `welcome: String` field from `Header`, the welcome-text rendering from `render_mascot_col`, and reduced `desired_height` from `MASCOT_LINES + 4` to `MASCOT_LINES + 2`. Made `WELCOMES` pub so `app.rs` can pick a random welcome for the first assistant message.

## Files changed
- `src/marcel_cli/src/config.rs` — `ws_url` signature adds `use_v2: bool`
- `src/marcel_cli/src/main.rs` — `--v2` CLI flag added
- `src/marcel_cli/src/header.rs` — remove `welcome` field, shrink height, pub `WELCOMES`

## Implementation Log
- 2026-04-09: Changes landed as part of v2 harness migration + ISSUE-040 scrolling work; committed together as cleanup.
