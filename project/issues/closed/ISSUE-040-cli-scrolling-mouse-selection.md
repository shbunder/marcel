# ISSUE-040: CLI scrolling and mouse text selection

**Status:** Closed
**Created:** 2026-04-09
**Assignee:** Unassigned
**Priority:** High
**Labels:** bug, cli, ux

## Capture
**Original request:** "there is an issue in the cli, when the conversation gets to long, it does not allow to scroll up (instead it scrolls in the new message window) can we fix this. optional: it would be nice that if we say something the input automatically scrolls to top and makes rooms for the new output (like most modern chat interfaces)"

**Follow-up:** User later reported: "there is still something wrong, the TUI seems to freeze, and when I type a new message then I can see the rest of the bot response. also I cannot select the text anymore and copy it here." And: "how does claude code solve this — being able to select text is a must, and being able to navigate long conversations easily as well."

**Resolved intent:** Three separate problems in the Rust TUI CLI:
1. Scroll wheel moved the input box cursor instead of scrolling the chat view.
2. When mouse capture was enabled for scrolling, the terminal's native text selection was broken. The freeze was caused by the view never auto-scrolling to the bottom after streaming ended.
3. After streaming ends, `finish_stream()` reflows the streamed text into multi-line markdown — which is taller than the single-line streaming representation — but `scroll_to_bottom()` was never called after that reflow, leaving the view stranded mid-conversation.

## Description

The CLI uses ratatui 0.29 + crossterm 0.28. The chat view (`ChatView`) is the scrollable upper pane; the input box is fixed at the bottom.

**Root causes:**

1. **Scroll not wired to chat view** — mouse scroll events were not handled; PageUp/PageDown were missing.
2. **Content height underestimated** — `content_height()` counted logical lines, not visual rows after word-wrap, causing `max_scroll` to be too small.
3. **Mouse capture vs. native selection** — enabling `EnableMouseCapture` (needed for scroll wheel events) intercepts all mouse input, breaking the terminal's built-in drag-to-select.
4. **Scroll stranded after stream end** — `finish_stream()` converts the streaming line to multi-line markdown but `scroll_to_bottom()` was not called, leaving the view frozen mid-conversation. The per-frame auto-follow was also missing, so any path that forgot a `scroll_to_bottom()` call would strand the view.

## Implementation

### Scroll events and follow mode
- Added `scroll_up()`, `scroll_down()`, `scroll_to_bottom()` methods to `ChatView`.
- Added `following: bool` flag — auto-scroll only when the user hasn't manually scrolled up. Disabled on `scroll_up()`; re-enabled when scrolling back to the bottom or sending a message.
- Added `area_height: u16` and `area_width: u16` fields, updated each frame from `terminal.size()`.
- PageUp / PageDown keyboard shortcuts added.

### Accurate content height
- Rewrote `content_height()` to measure visual rows per logical line using `unicode_width`, accounting for word-wrap at the terminal width.

### Mouse capture + custom selection
- Re-enabled `EnableMouseCapture`/`DisableMouseCapture` in `tui.rs`.
- Implemented app-level drag-to-select: on drag-end, render a selection highlight overlay directly into the ratatui buffer, extract the selected text from a buffer snapshot captured each frame, and copy to clipboard via `wl-copy` / `xclip` / `xsel` / `pbcopy`.
- A "copied" notification appears in the status bar for 1.5 s.

### Per-frame auto-follow
- Added an unconditional `scroll_to_bottom()` call at the top of each render loop iteration when `following == true`. This is the catch-all that prevents the view from ever getting stranded regardless of event ordering.
- Added explicit `scroll_to_bottom()` call after `finish_stream()` in the `Done` event handler.

## Files changed
- `src/marcel_cli/src/tui.rs` — re-enable mouse capture
- `src/marcel_cli/src/ui.rs` — `ChatView` scroll methods, `content_height`, `StatusBar.notification`, 16 unit tests
- `src/marcel_cli/src/app.rs` — mouse events, drag-to-select overlay, clipboard copy, per-frame auto-follow, post-stream scroll

## Implementation Log
- 2026-04-09: Fixed scroll events, follow mode, content height, word-wrap measurement
- 2026-04-09: Enabled mouse capture; implemented drag-to-select with buffer overlay and clipboard copy
- 2026-04-09: Fixed scroll-stranded-after-stream bug: added per-frame `scroll_to_bottom` when `following`, and explicit call after `finish_stream`
- 2026-04-09: User confirmed: "working!"
