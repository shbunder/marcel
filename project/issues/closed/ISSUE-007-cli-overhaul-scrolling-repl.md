# ISSUE-007: CLI Overhaul — Scrolling REPL, Claude-style UI, Model Selection

**Status:** Closed
**Created:** 2026-03-28
**Closed:** 2026-03-28
**Assignee:** Claude
**Priority:** High
**Labels:** feature, cli, ux

## Capture

**Original requests (in order):**
1. "how do I start the marcel_cli using make?" → no `cli` target existed
2. "create a target for the cli"
3. "test it, make the ports an environment variable in .env"
4. "/bin/bash: line 1: uv: command not found (I think you need to install uv)"
5. "test the cli, it's not working"
6. "I really don't like the cli, make it look a lot more like the claude cli. show some information about the app (the version, which model we are using, which user is connected)"
7. "also, make the rest look more like this claude output: [screenshot of claude CLI with ❯ prompt, ● responses, header with mascot]"
8. "I don't like this window approach, I want it to be pure text like the claude code cli"
9. "put the user on a different line, vertically center better and put a horizontal line top and bottom of the header"
10. "also, use 5 rows of the marcel mascotte, the model it's showing can I change that? In claude if I type '/' it starts showing commands are available, I want the same"
11. "the user messages are shown twice, and most commands don't do anything, fix this"
12. "I should be able to change the model"

**Resolved intent:** A complete overhaul of the Marcel CLI, starting from a missing Makefile target and a broken TOML config, through replacing the Textual full-screen TUI with a pure scrolling REPL modelled on the Claude Code CLI. The result is a prompt_toolkit-based REPL with a styled header, ❯/● messaging, slash command completions, and end-to-end model selection (config → CLI flag → /model command → WebSocket payload → ClaudeAgentOptions).

## Description

ISSUE-006 delivered a Textual TUI. The user wanted something closer to the Claude Code CLI experience: a scrolling terminal, not a full-screen window. This issue captures all work done to get there, including several bugs discovered along the way.

## Tasks

- [✓] Add `make cli` Makefile target
- [✓] Add `MARCEL_HOST` / `MARCEL_PORT` to `.env`, use them in `make cli` and `make serve`
- [✓] Install `uv` and fix `pyproject.toml` build system so the `marcel` entrypoint is installed
- [✓] Fix invalid TOML section header in `_write_default_config` (`[Marcel CLI config]` → removed)
- [✓] Replace Textual TUI with prompt_toolkit scrolling REPL
- [✓] Claude-style header: 5-line mascot beside version / model / user / host:port, framed with `─` rules
- [✓] `❯` prompt (prompt_toolkit), `●` prefix for assistant responses
- [✓] Gray background on user input via prompt_toolkit `Style` (no double-print)
- [✓] Slash command completions with descriptions on `/`
- [✓] Fix double-print bug (prompt_toolkit already renders input; removed `_print_user` reprint)
- [✓] Fix commands doing nothing (`/status` made local; server commands show clear error when offline)
- [✓] `model` field added to `Config` and `~/.marcel/config.toml`
- [✓] `--model` CLI flag added to `marcel` entrypoint
- [✓] `/model [name]` command updates model live in session
- [✓] Model threaded through WebSocket payload → chat endpoint → `stream_response` → `ClaudeAgentOptions`

## Implementation Log

### 2026-03-28 - Claude

**Action:** Added `make cli` target and environment variable support
**Files Modified:**
- `Makefile` — added `cli` target (`uv run marcel --host $(MARCEL_HOST) --port $(MARCEL_PORT)`), updated `serve` to use `$(MARCEL_PORT)`
- `.env` — added `MARCEL_HOST=localhost`, `MARCEL_PORT=8000`

---

**Action:** Fixed uv not found; fixed `marcel` entrypoint not being installed
**Files Modified:**
- `pyproject.toml` — added `[build-system]` (hatchling), `[tool.hatch.build.targets.wheel]`, `tool.uv.package = true`

---

**Action:** Fixed crash on startup due to invalid TOML config written by `_write_default_config`
**Files Modified:**
- `src/marcel_cli/config.py` — removed `[Marcel CLI config]` section header (TOML disallows spaces in table names); deleted stale `~/.marcel/config.toml`

---

**Action:** Replaced Textual TUI with Claude-style scrolling REPL
**Files Modified:**
- `pyproject.toml` — replaced `textual` dependency with `prompt-toolkit>=3.0.0`
- `src/marcel_cli/app.py` — full rewrite: `prompt_toolkit.PromptSession` + `patch_stdout`, `_print_header` with mascot+info, `❯`/`●` message style, `_SlashCompleter`, `_handle_command`
- `src/marcel_cli/main.py` — replaced `MarcelApp().run()` with `asyncio.run(run(config))`
- `src/marcel_cli/config.py` — added `model` field (default `claude-sonnet-4-6`), `--model` override in `load_config`
- `src/marcel_cli/main.py` — added `--model` argparse flag

---

**Action:** Fixed double-print of user messages; fixed commands doing nothing
**Files Modified:**
- `src/marcel_cli/app.py` — removed `_print_user` reprint; applied gray background via `_SESSION_STYLE` on the `PromptSession`; moved `/status` to local handler; added clear "requires server" error for `/compact`, `/cost`, `/memory`

---

**Action:** Wired model selection end-to-end
**Files Modified:**
- `src/marcel_cli/chat.py` — added `model` param to `__init__`, included in WebSocket payload
- `src/marcel_cli/app.py` — passes `config.model` to `ChatClient`; `/model <name>` updates both `config.model` and `client._model`
- `src/marcel_core/api/chat.py` — reads `model` from payload, passes to `stream_response`
- `src/marcel_core/agent/runner.py` — added `model` param to `stream_response`, passed to `ClaudeAgentOptions`
