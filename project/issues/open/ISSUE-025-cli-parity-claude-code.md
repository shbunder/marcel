# ISSUE-025: CLI Parity with Claude Code

**Status:** Open
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
- [ ] ISSUE-025-a: Replace manual `--dev` flag parsing with `clap` — add flags: `-p/--print`, `--model`, `--user`, `--dev`, `--output-format {text,json,stream-json}`
- [ ] ISSUE-025-b: Implement print mode (`-p`) — send prompt from arg/stdin, stream response to stdout, exit. No TUI.
- [ ] ISSUE-025-c: Support stdin piping — detect `!isatty(stdin)` and read prompt from pipe when `-p` is used

### Phase 2 — Session management
- [ ] ISSUE-025-d: Track conversation ID in CLI (already partially done) — persist last conversation ID per user to `~/.marcel/cli_state.json`
- [ ] ISSUE-025-e: Add `-c/--continue` flag — resume most recent conversation
- [ ] ISSUE-025-f: Add `-r/--resume [id]` flag — resume specific conversation or show picker
- [ ] ISSUE-025-g: Add `/sessions` slash command — list recent conversations (requires new backend endpoint)
- [ ] ISSUE-025-h: Add `/new` slash command — start a fresh conversation without restarting CLI

### Phase 3 — Rich status bar
- [ ] ISSUE-025-i: Parse `cost_usd` and `turns` from WebSocket `done` messages — track cumulative session cost
- [ ] ISSUE-025-j: Redesign `StatusBar` to show: connection status, model, session cost, turn count, conversation ID
- [ ] ISSUE-025-k: Add `/cost` rendering in status bar (live update, not just slash command)

### Phase 4 — Input improvements
- [ ] ISSUE-025-l: Input history — up/down arrows cycle through previous messages (session-scoped)
- [ ] ISSUE-025-m: Multi-line input — `Ctrl+G` opens `$EDITOR` for composing longer messages
- [ ] ISSUE-025-n: Tab autocompletion for slash commands — pressing Tab after `/` shows matching commands
- [ ] ISSUE-025-o: `Ctrl+U` to clear input line, `Ctrl+W` to delete word backward (readline-style shortcuts)

### Phase 5 — Additional slash commands + output formats
- [ ] ISSUE-025-p: `/export [file]` — export conversation transcript to a file (markdown format)
- [ ] ISSUE-025-q: `--output-format json` for print mode — wrap response in JSON with cost/turns metadata
- [ ] ISSUE-025-r: `--output-format stream-json` for print mode — NDJSON streaming (one JSON object per token)

## Subtasks

- [ ] ISSUE-025-a: Add clap dependency and replace manual arg parsing
- [⚒] ISSUE-025-b: Print mode implementation
- [ ] ISSUE-025-c: Stdin pipe detection
- [ ] ISSUE-025-d: Persist conversation ID to disk
- [ ] ISSUE-025-e: Continue flag
- [ ] ISSUE-025-f: Resume flag with picker
- [ ] ISSUE-025-g: /sessions command (CLI + backend endpoint)
- [ ] ISSUE-025-h: /new command
- [ ] ISSUE-025-i: Parse cost/turns from done messages
- [ ] ISSUE-025-j: Redesign StatusBar widget
- [ ] ISSUE-025-k: Live cost in status bar
- [ ] ISSUE-025-l: Input history
- [ ] ISSUE-025-m: External editor (Ctrl+G)
- [ ] ISSUE-025-n: Tab autocompletion
- [ ] ISSUE-025-o: Readline shortcuts
- [ ] ISSUE-025-p: /export command
- [ ] ISSUE-025-q: JSON output format
- [ ] ISSUE-025-r: Stream-JSON output format

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
<!-- Append entries here when performing development work on this issue -->
