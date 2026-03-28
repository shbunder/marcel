# ISSUE-006: marcel-cli TUI

**Status:** Open
**Created:** 2026-03-26
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, phase-1

## Capture
**Original request:** A Terminal UI where I can chat with Marcel on the NUC (service running on NUC). Also installable on laptop with configurable address and port.

**Resolved intent:** Build an interactive TUI using Textual that connects to the `marcel-core` WebSocket API, streams responses in real time, and is configurable via `~/.marcel/config.toml` or `--host`/`--port` CLI flags.

## Description

The TUI is the primary developer interface in Phase 1. It should feel like a polished chat app in the terminal — not a simple REPL.

### Layout

```
┌─────────────────────────────────────────────────────────┐
│ Marcel                                    connected ●   │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  You: What's on my calendar this week?                  │
│                                                         │
│  Marcel: You have a dentist appointment Tuesday at      │
│  10am and a team lunch Thursday.                        │
│                                                         │
│  You: Move the dentist to Thursday afternoon.           │
│                                                         │
│  Marcel: Done — moved to Thursday at 3pm. ▌             │
│                                                         │
│                                                         │
├─────────────────────────────────────────────────────────┤
│ > _                                          [Enter ↵]  │
└─────────────────────────────────────────────────────────┘
```

- Scrollable conversation panel (top)
- Single-line input (bottom), Enter to send
- Status indicator: `connected ●` / `connecting...` / `disconnected ○`
- Marcel responses stream token-by-token (cursor ▌ advances)
- Markdown rendering in conversation panel (bold, code blocks, bullet lists)

### Configuration

`~/.marcel/config.toml`:
```toml
host = "localhost"
port = 8000
user = "shaun"
token = "..."   # long-lived developer token (Phase 1: not yet validated by server)
```

CLI flags override config:
```
marcel --host 192.168.1.50 --port 8000
marcel --host marcel.example.com
```

In Phase 1 the `token` field exists in the config but the server doesn't validate it yet (auth is Phase 2). It's wired up now so Phase 2 can flip it on without changing the CLI.

### WebSocket protocol

Follows the protocol defined in ISSUE-003:
- Connect to `ws://{host}:{port}/ws/chat`
- Send `{"text": "...", "user": "shaun", "conversation": null}` to start new conversation
- Server responds with `{"type": "started", "conversation": "2026-03-26T14-32"}` then `{"type": "token", "text": "..."}` stream, ending with `{"type": "done"}`
- Subsequent messages include `"conversation": "2026-03-26T14-32"` to continue the session

### Module layout

```
src/marcel_cli/
  __init__.py
  main.py       # entrypoint: load config, start TUI
  config.py     # load ~/.marcel/config.toml, apply CLI flag overrides
  app.py        # Textual App definition, layout, key bindings
  chat.py       # WebSocket client, message send/receive, streaming handler
```

### `pyproject.toml` entry point

```toml
[project.scripts]
marcel = "marcel_cli.main:main"
```

## Tasks
- [ ] Add `textual`, `websockets`, `tomllib` (stdlib 3.11+) to dependencies
- [ ] `config.py`: load `~/.marcel/config.toml`, create default if missing, apply flag overrides
- [ ] `chat.py`: async WebSocket client — connect, send message, yield tokens as they arrive
- [ ] `app.py`: Textual App with conversation panel + input bar + status indicator
- [ ] `main.py`: parse `--host`/`--port` flags, load config, launch app
- [ ] Add `[project.scripts]` entry in `pyproject.toml`
- [ ] Tests: config loading with overrides; WebSocket client message parsing (mock server)
- [ ] Docs: `docs/cli.md` — installation, config file, usage

## Relationships
- Depends on: [[ISSUE-003-agent-loop]]

## Implementation Log
