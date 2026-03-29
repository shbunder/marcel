# ISSUE-009: Fixed Header with Live Resize — Alternate Screen + Scroll Region

**Status:** Closed (superseded by ISSUE-010 — Rust CLI rewrite)
**Created:** 2026-03-29
**Assignee:** Claude
**Priority:** High
**Labels:** feature, cli, ux

## Capture

**Original request:** "I want you to make a fix in the cli: the header should dynamically change and adapt when I change the terminal width. also currently when I change a new input tag is added, this doesn't look nice."

**Follow-up (after several iterations):**
- "it still looks terrible in my vscode terminal" — duplicate ❯ prompts flooding the screen on resize
- "I see multiple headers stacked, with some strange input field artifacts below" — debounce not collapsing drag into single redraw
- "also my chathistory is suddenly gone" — screen clear wiping conversation
- "I'm expecting the top thing to be the header in this screen, nothing else above the header, no old headers, no 'shbunder@bunderserver:...' the cli should run in its own screen"
- Final requirements: "1) header is always on top (nothing else above) 2) header can reshape if the window width changes 3) chat history is below the header, even after reshaping"

**Resolved intent:** The CLI must run in an isolated alternate screen buffer (like vim/htop) with a fixed header that reflows its responsive layout when the terminal is resized, while preserving the scrolling chat history below it. This requires alternate screen (`\033[?1049h`), a scroll region that confines chat output below the header, and an in-place header redraw on resize that does not touch the scroll region content.

## Description

The previous SIGWINCH-based resize handler fought with prompt_toolkit's own SIGWINCH handler, causing dozens of duplicate ❯ prompt renders per drag. Switching to polling fixed detection, but the `_draw_header` function used `\033[J` (clear to end of screen) which wiped chat history on every resize. The CLI also ran in the normal screen buffer, so shell artifacts (make output, bash prompt) bled through.

### Architecture

- **Alternate screen buffer** — isolates the CLI from the shell; on exit, the original terminal is restored
- **Scroll region** — rows 1..N are the fixed header; rows N+1..bottom are the scrollable chat area
- **In-place header redraw** — on resize, each header line is overwritten individually (`\033[{row};1H\033[2K{line}`) without clearing the scroll region
- **Polling resize monitor** — background asyncio task checks terminal width every 200ms; debounces until stable
- **prompt_toolkit SIGWINCH disabled** — `session.app.handle_sigwinch = False` prevents prompt duplication

## Tasks

- [✓] Rewrite `_draw_header` → split into `_setup_screen` (initial) and `_refresh_header` (resize-safe)
- [✓] `_refresh_header`: write header lines in-place, update scroll region bounds, no content clear
- [✓] `_full_redraw`: clear entire alt screen (for `/clear`, `/reconnect`, `/config`)
- [ ] Verify chat history preserved after resize
- [ ] Verify header adapts layout tiers (3-col / 2-col / 1-col) on width change
- [ ] Verify no duplicate ❯ prompts on resize
- [ ] Verify clean exit restores original terminal
- [✓] Update docs

## Relationships

- Follows from: [[ISSUE-007-cli-overhaul-scrolling-repl]]

## Implementation Log

### 2026-03-29 - Claude

**Action:** Rewrote CLI screen management — alternate screen buffer, scroll regions, in-place header redraw

**Files Modified:**
- `src/marcel_cli/app.py` — alt screen + scroll region approach
- `docs/cli.md` — updated docs

---

### 2026-03-29 - Claude

**Action:** Complete overhaul — removed alternate screen + scroll regions (broken with prompt_toolkit), replaced with simple scrolling REPL

The alternate screen buffer and ANSI scroll regions fundamentally conflict with prompt_toolkit's rendering model:
- DECSTBM (set scroll region) resets cursor to (1,1), breaking prompt_toolkit cursor positioning
- Scroll regions confuse patch_stdout's coordination of output above the prompt
- Alternate screen has no scrollback, so chat history is lost entirely

**New approach:** pure scrolling REPL, no alternate screen, no scroll regions. Works WITH prompt_toolkit instead of fighting it.

**Files Modified:**
- `src/marcel_cli/app.py` — complete overhaul:
  - Removed `_enter_alt_screen()`, `_leave_alt_screen()`, `_write_header_lines()`, `_setup_screen()`, `_refresh_header()`, `_full_redraw()`
  - Startup: clear screen + scrollback (`\033[2J\033[3J\033[H`), print header via `sys.__stdout__`
  - `_print_header()`: clear visible screen (`\033[2J\033[H`) + print header, all via `sys.__stdout__`
  - Resize: `_print_header()` → `renderer.reset()` + `invalidate()` for single clean ❯
  - `session.app.handle_sigwinch = False` prevents prompt_toolkit ❯ spam during drag
  - All screen writes go to `sys.__stdout__` (bypass `patch_stdout` proxy)
  - Debounce increased to 250ms for smoother resize
- `docs/cli.md` — updated architecture notes, removed alt screen / scroll region references
