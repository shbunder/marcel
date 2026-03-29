# ISSUE-010: Rewrite CLI as Native Rust TUI

**Status:** Closed
**Created:** 2026-03-29
**Assignee:** Claude
**Priority:** High
**Labels:** feature, cli

## Capture
**Original request:** "New feature, let's completely redo the Marcel CLI, I've learned that many use react or rust or whatever to build the CLI, we will do the same. To get you inspired look at the codex-cli; you can find the repo here: /home/shbunder/repos/codex/codex-rs. I still want the same header as now, and connection to the Marcel backend, but with the new cli that has similar features to show updates etc from the backend."

**Follow-up:** User clarified they want the same stack as codex-cli (Rust + ratatui), not Python/Textual. "don't use python anymore unless it's needed or really makes sense, I'm aiming for a complete redesign."

**Resolved intent:** Replace the Python prompt_toolkit-based REPL with a native Rust TUI binary, using the same technology stack as codex-cli (ratatui + crossterm + tokio). The CLI compiles to a single binary, connects to the Marcel backend via WebSocket, and renders the familiar header with mascot art, streaming chat, input, and status bar. The old Python CLI (`src/marcel_cli/`) is fully removed.

## Requirements
1. Native Rust binary — single file, no runtime dependencies
2. Fixed header panel at top — same 3-column responsive layout with mascot, runtime info, and server info
3. Scrollable chat history with basic markdown rendering (headers, code blocks, lists)
4. Streaming token display — tokens arrive from WebSocket and render in real time
5. Text input with cursor editing
6. Status bar showing connection state and model
7. All slash commands (/help, /clear, /config, /model, /status, /cost, /memory, /compact, /reconnect, /exit)
8. Same config file (~/.marcel/config.toml) — read by Rust via serde + toml
9. Brand colors preserved (#cc5e76 blush rose, #2ec4b6 deep teal, etc.)
10. Makefile, install.sh, docs updated for Rust workflow
11. Old Python CLI (`src/marcel_cli/`) fully removed, deps cleaned from pyproject.toml

## Design

### Stack: Rust + ratatui (same as codex-cli)

- **ratatui** — TUI framework with retained-mode rendering
- **crossterm** — terminal backend (events, cursor, colors)
- **tokio** — async runtime for WebSocket + event loop
- **tokio-tungstenite** — WebSocket client (rustls for TLS)
- **reqwest** — HTTP client for /health check
- **pulldown-cmark** — markdown parsing (future full rendering)

### Architecture

```
main() -> App::run()
  |-- FlexLayout (Renderable trait)
  |     |-- Header         flex=0  -- mascot + runtime + server info
  |     |-- ChatView       flex=1  -- scrollable message history + streaming
  |     |-- InputBox       flex=0  -- text input with cursor
  |     +-- StatusBar      flex=0  -- connection state + model
  |-- ChatClient           -- async WebSocket send/receive via mpsc channel
  +-- Event loop           -- crossterm key events + streaming token drain
```

### Files

| File | Purpose |
|------|---------|
| `src/marcel_cli/Cargo.toml` | Rust crate with all dependencies |
| `src/marcel_cli/src/main.rs` | Entrypoint |
| `src/marcel_cli/src/app.rs` | Event loop, command routing, WebSocket integration |
| `src/marcel_cli/src/tui.rs` | Terminal init/restore (raw mode, alt screen) |
| `src/marcel_cli/src/render.rs` | Renderable trait, FlexLayout, ColumnLayout |
| `src/marcel_cli/src/header.rs` | Header panel with responsive column layout |
| `src/marcel_cli/src/ui.rs` | ChatView, InputBox, StatusBar |
| `src/marcel_cli/src/chat.rs` | WebSocket client, streaming token receiver |
| `src/marcel_cli/src/config.rs` | TOML config loader |

## Tasks
- [✓] ISSUE-010-a: Create Rust crate scaffold (Cargo.toml, src/main.rs)
- [✓] ISSUE-010-b: Implement Renderable trait and FlexLayout
- [✓] ISSUE-010-c: Implement terminal setup (tui.rs)
- [✓] ISSUE-010-d: Implement Header with responsive columns and mascot
- [✓] ISSUE-010-e: Implement ChatView with streaming and markdown
- [✓] ISSUE-010-f: Implement InputBox with cursor editing
- [✓] ISSUE-010-g: Implement StatusBar
- [✓] ISSUE-010-h: Implement WebSocket client with async mpsc streaming
- [✓] ISSUE-010-i: Wire up App event loop — commands, chat, streaming
- [✓] ISSUE-010-j: Clean compile (0 errors, 0 warnings)
- [✓] ISSUE-010-k: Update Makefile, install.sh, docs
- [✓] ISSUE-010-l: Remove old Python CLI and clean up dependencies
- [✓] ISSUE-010-m: Version bump (deferred — CLI ships at v0.1.0 as initial release)

## Implementation Log

### 2026-03-29 — Attempt 1: Python Textual (abandoned)
**Action**: Attempted rewrite using Python's Textual framework
**Result**: Working code but user rejected approach — wanted native Rust like codex-cli, not more Python

### 2026-03-29 — Attempt 2: Rust + ratatui (complete)
**Action**: Complete CLI rewrite as native Rust binary
**Files Created**:
- `src/marcel_cli/Cargo.toml` — Rust crate with ratatui, crossterm, tokio, tokio-tungstenite, reqwest, serde, toml, pulldown-cmark
- `src/marcel_cli/src/main.rs` — Entrypoint, loads config and runs app
- `src/marcel_cli/src/app.rs` — Event loop with keyboard handling, command routing, WebSocket streaming via mpsc
- `src/marcel_cli/src/tui.rs` — Terminal init (raw mode + alt screen) and restore
- `src/marcel_cli/src/render.rs` — Renderable trait, FlexLayout with flex allocation, ColumnLayout
- `src/marcel_cli/src/header.rs` — 3/2/1-column responsive header with mascot art, brand colors
- `src/marcel_cli/src/ui.rs` — ChatView (messages + streaming tokens), InputBox (cursor editing), StatusBar
- `src/marcel_cli/src/chat.rs` — Async WebSocket client spawning tokio task, streaming ChatEvents via mpsc channel
- `src/marcel_cli/src/config.rs` — serde + toml loader for ~/.marcel/config.toml
**Files Modified**:
- `Makefile` — Added cli, cli-build, cli-dev, install-cli targets; updated test/format/lint for Rust
- `install.sh` — Builds Rust binary via cargo install instead of uv tool install
- `docs/cli.md` — Rewritten for Rust architecture
- `docs/architecture.md` — Added CLI section
**Build**:
- Debug: 84MB, Release: 3.6MB (with LTO + strip)
- 0 errors, 0 warnings

### 2026-03-29 — Cleanup: remove old Python CLI
**Action**: Removed the entire old Python CLI and cleaned up references
**Files Removed**:
- `src/marcel_cli/` — entire directory (app.py, chat.py, config.py, main.py, mascot.py, __init__.py)
- `tests/cli/test_cli.py` — old Python CLI tests (8 tests)
**Files Modified**:
- `pyproject.toml` — removed `rich`, `prompt-toolkit`, `websockets` dependencies; removed `[project.scripts]` entry; removed `src/marcel_cli` from wheel packages
- `Makefile` — removed `cli-legacy` target
- `uv.lock` — regenerated after dependency removal
**Result**: 83 Python tests still passing. Rust CLI is the sole CLI.
