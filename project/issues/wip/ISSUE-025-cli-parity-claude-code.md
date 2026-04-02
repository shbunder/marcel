# ISSUE-025: CLI Parity with Claude Code

**Status:** WIP
**Created:** 2026-04-02
**Assignee:** Marcel
**Priority:** High
**Labels:** feature, cli

## Capture

**Original request:** "what can you learn from ~/repos/claude-code to improve the Marcel CLI? Except for the header I want as much of the similar capabilities in Marcel's CLI"

**Follow-up Q&A:** None — user confirmed to proceed with implementation.

**Resolved intent:** Bring Marcel's Rust TUI CLI to feature parity with Claude Code's CLI capabilities. The header is excluded (already implemented). The goal is a CLI that supports scripting/piping, session management, rich status feedback, comfortable input editing, and the slash commands users expect from a modern terminal agent.

## Description

Marcel's CLI (`marcel-cli`) is a Rust TUI built on ratatui + crossterm. It currently supports basic chat, a handful of slash commands, and simple keyboard input. Claude Code's CLI (TypeScript, Ink-based) has significantly more features. This issue ports the most impactful ones to Marcel.

The backend already provides most of the data needed (cost, turns, conversation IDs via WebSocket protocol). The work is primarily CLI-side (Rust).

## Tasks

### Phase 1 — CLI arg parsing + print mode
- [✓] ISSUE-025-a: Replace manual `--dev` flag parsing with `clap` — add flags: `-p/--print`, `--model`, `--user`, `--dev`, `--output-format {text,json,stream-json}`
- [✓] ISSUE-025-b: Implement print mode (`-p`) — send prompt from arg/stdin, stream response to stdout, exit. No TUI.
- [✓] ISSUE-025-c: Support stdin piping — detect `!isatty(stdin)` and read prompt from pipe when `-p` is used

### Phase 2 — Session management
- [✓] ISSUE-025-d: Track conversation ID in CLI (already partially done) — persist last conversation ID per user to `~/.marcel/cli_state.json`
- [✓] ISSUE-025-e: Add `-c/--continue` flag — resume most recent conversation
- [✓] ISSUE-025-f: Add `-r/--resume [id]` flag — resume specific conversation or show picker
- [✓] ISSUE-025-g: Add `/sessions` slash command — list recent conversations (requires new backend endpoint)
- [✓] ISSUE-025-h: Add `/new` slash command — start a fresh conversation without restarting CLI

### Phase 3 — Rich status bar
- [✓] ISSUE-025-i: Parse `cost_usd` and `turns` from WebSocket `done` messages — track cumulative session cost
- [✓] ISSUE-025-j: Redesign `StatusBar` to show: connection status, model, session cost, turn count, conversation ID
- [✓] ISSUE-025-k: Add `/cost` rendering in status bar (live update, not just slash command)

### Phase 4 — Input improvements
- [✓] ISSUE-025-l: Input history — up/down arrows cycle through previous messages (session-scoped)
- [✓] ISSUE-025-m: Multi-line input — `Ctrl+G` opens `$EDITOR` for composing longer messages
- [✓] ISSUE-025-n: Tab autocompletion for slash commands — pressing Tab after `/` shows matching commands
- [✓] ISSUE-025-o: `Ctrl+U` to clear input line, `Ctrl+W` to delete word backward (readline-style shortcuts)

### Phase 5 — Additional slash commands + output formats
- [✓] ISSUE-025-p: `/export [file]` — export conversation transcript to a file (markdown format)
- [✓] ISSUE-025-q: `--output-format json` for print mode — wrap response in JSON with cost/turns metadata
- [✓] ISSUE-025-r: `--output-format stream-json` for print mode — NDJSON streaming (one JSON object per token)

## Subtasks

- [✓] ISSUE-025-a: Add clap dependency and replace manual arg parsing
- [✓] ISSUE-025-b: Print mode implementation
- [✓] ISSUE-025-c: Stdin pipe detection
- [✓] ISSUE-025-d: Persist conversation ID to disk
- [✓] ISSUE-025-e: Continue flag
- [✓] ISSUE-025-f: Resume flag with picker
- [✓] ISSUE-025-g: /sessions command (CLI + backend endpoint)
- [✓] ISSUE-025-h: /new command
- [✓] ISSUE-025-i: Parse cost/turns from done messages
- [✓] ISSUE-025-j: Redesign StatusBar widget
- [✓] ISSUE-025-k: Live cost in status bar
- [✓] ISSUE-025-l: Input history
- [✓] ISSUE-025-m: External editor (Ctrl+G)
- [✓] ISSUE-025-n: Tab autocompletion
- [✓] ISSUE-025-o: Readline shortcuts
- [✓] ISSUE-025-p: /export command
- [✓] ISSUE-025-q: JSON output format
- [✓] ISSUE-025-r: Stream-JSON output format

## Relationships

- Related to: [[ISSUE-024-agent-reimplementation]] (agent sessions provide the backend plumbing)

## Comments

### 2026-04-02 - Marcel
Gap analysis performed by comparing ~/repos/claude-code (TypeScript CLI) against marcel-cli (Rust TUI). Features deliberately excluded:
- **Header** — user explicitly excluded this; already implemented
- **Permission system** — Marcel uses server-side auth, not client-side tool permissions
- **Background sessions / daemon** — Marcel's server already runs persistently; CLI is a thin client
- **Image paste** — not supported by Marcel's backend currently
- **Vim mode** — deferred; can be added later without architectural changes
- **Customizable keybindings** — deferred; nice-to-have after core features land

## Implementation Log

### 2026-04-02 14:00 - LLM Implementation
**Action**: Phase 1 implementation — clap, print mode, status bar, input history, readline shortcuts, /new command
**Files Modified**:
- `src/marcel_cli/Cargo.toml` — added clap dependency, bumped version to 0.2.0
- `src/marcel_cli/src/main.rs` — rewrote with clap `Cli` struct, branching to print vs TUI mode
- `src/marcel_cli/src/config.rs` — removed `parse_dev_flag()` (replaced by clap)
- `src/marcel_cli/src/chat.rs` — added `TurnMeta` struct, `ChatEvent::Done(TurnMeta)` and `ChatEvent::Connected(TurnMeta)` to carry cost/turns/conversation_id; added `set_conversation_id()` and `clear_conversation()` to `ChatClient`
- `src/marcel_cli/src/print.rs` — new file: non-interactive mode with text, json, stream-json output formats; stdin pipe support
- `src/marcel_cli/src/app.rs` — updated to accept `Cli` ref; handles initial CLI prompt; tracks cost/turns in status bar; added `/new` command; added readline shortcuts (Ctrl+U/W/A/E); wired up input history (Up/Down)
- `src/marcel_cli/src/ui.rs` — `StatusBar` now tracks `session_cost` and `turn_count` with live rendering; `InputBox` gained history (prev/next), `clear()`, `delete_word_back()`
- `src/marcel_cli/src/header.rs` — version bump to v0.2.0
**Commands Run**: `cargo build` — clean compile, no warnings
**Result**: Success — all Phase 1, 3, and most of Phase 4/5 subtasks complete
**Next**: Phase 2 (session persistence + resume) and remaining items (tab completion, external editor, /export)

### 2026-04-02 15:00 - LLM Implementation
**Action**: Phase 2 + remaining — session resume, tab completion, external editor, /export, /sessions, backend endpoint
**Files Modified**:
- `src/marcel_core/api/conversations.py` — new REST endpoint `GET /conversations?user=&limit=` for listing recent conversations
- `src/marcel_core/main.py` — registered conversations router
- `src/marcel_cli/src/state.rs` — new module: persists last conversation ID per user to `~/.marcel/cli_state.json`
- `src/marcel_cli/src/main.rs` — registered state module
- `src/marcel_cli/src/app.rs` — added: `-c`/`-r` session resume logic, `/sessions` command (fetches from backend), `/resume <id>` command, `/export` command (writes markdown), tab autocompletion for slash commands with common prefix, `Ctrl+G` external editor support, `fetch_conversations()` HTTP helper, `export_conversation()` file writer
**Commands Run**: `cargo build` — clean compile; `uv run pytest tests/core/test_storage.py` — 50 passed
**Result**: All 18 subtasks complete
**Next**: Close issue — docs + version bump in closing commit
