# Marcel CLI

The Marcel CLI is a native Rust terminal interface for chatting with the Marcel agent. It connects to the `marcel-core` backend over WebSocket and streams responses in real time with markdown rendering.

Built with **ratatui** + **crossterm** — the same stack used by [codex-cli](https://github.com/openai/codex). The CLI compiles to a single ~3.6MB binary with zero runtime dependencies.

## Installation

Build and install from source (requires Rust toolchain):

```bash
./scripts/install.sh
```

Or build manually:

```bash
cd src/marcel_cli && cargo install --path .
```

The `marcel` binary is placed in `~/.cargo/bin/` (which should be on your `PATH`).

For development, build and run without installing:

```bash
make cli-dev    # debug build + run
make cli        # release build + run
```

## Configuration

Marcel reads its configuration from `~/.marcel/config.toml`. If the file does not exist, it is created automatically with defaults on first run (via `scripts/install.sh`).

```toml
host = "localhost"
port = 7420
user = "alice"
model = "claude-sonnet-4-6"
token = ""
```

The `token` field must match the `MARCEL_API_TOKEN` environment variable on the server (if set). The `user` field identifies which user profile to load.

### Connecting to a remote server

To connect to Marcel running on another host, edit the config file:

```toml
host = "192.168.1.50"
port = 7420
```

Or pass flags to the installer:

```bash
./scripts/install.sh --host 192.168.1.50 --port 7420
```

## Usage

```
marcel [OPTIONS] [PROMPT]
```

### Options

| Flag | Description |
|------|-------------|
| `[PROMPT]` | Send this prompt immediately (enters interactive mode unless `-p` is given) |
| `-p, --print` | Print response to stdout and exit (non-interactive) |
| `-c, --continue` | Continue the most recent conversation |
| `-r, --resume [ID]` | Resume a specific conversation, or show picker if no ID given |
| `-m, --model <MODEL>` | Override the model from config |
| `-u, --user <USER>` | Override the user from config |
| `--dev` | Connect to dev server (port from `dev_port` in config) |
| `--output-format <FMT>` | Output format for print mode: `text` (default), `json`, `stream-json` |

### Examples

```bash
# Interactive TUI
marcel

# Send a prompt and enter interactive mode
marcel "What's on my calendar today?"

# Non-interactive: print response and exit
marcel -p "What time is it in Tokyo?"

# Pipe input
echo "Summarize this" | marcel -p

# JSON output for scripting
marcel -p --output-format json "List my tasks"

# Stream JSON (NDJSON) for real-time processing
marcel -p --output-format stream-json "Tell me a story"

# Continue last conversation
marcel -c

# Resume a specific conversation
marcel -r 2026-04-01T14-32
```

### Interactive mode

The interface consists of four areas:

- **Header** — fixed panel at the top with mascot art, runtime info (CLI version, user, model), and server status. Adapts between 3-column, 2-column, and 1-column layouts depending on terminal width.
- **Chat view** — scrollable conversation history. User messages prefixed with `❯`, assistant responses rendered with basic markdown (headers, code blocks, lists). Streams tokens as they arrive.
- **Input** — bordered text field at the bottom with cursor editing. Shows placeholder text when empty.
- **Status bar** — connection state, model, session cost, turn count, and `/help` hint. Cost and turns update live after each response.

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Send message, or accept selected suggestion |
| `Ctrl+C` / `Ctrl+D` | Quit |
| `Up` / `Down` | Navigate suggestion dropdown, or cycle input history |
| `Tab` | Accept selected suggestion |
| `Escape` | Dismiss suggestion dropdown |
| `Ctrl+G` | Open `$EDITOR` for multi-line input |
| `Ctrl+U` | Clear input line |
| `Ctrl+W` | Delete word backward |
| `Ctrl+A` | Jump to start of input |
| `Ctrl+E` | Jump to end of input |
| `PageUp` / `PageDown` | Scroll chat history |
| `Left` / `Right` | Move cursor in input |
| `Home` / `End` | Jump to start/end of input |
| `Backspace` / `Delete` | Delete characters |

### Print mode

Print mode (`-p`) sends a prompt and streams the response to stdout, then exits. This enables piping and scripting:

```bash
# Plain text (default)
marcel -p "hello" > response.txt

# JSON with metadata
marcel -p --output-format json "hello"
# {"response": "Hi there!", "cost_usd": 0.0012, "turns": 1}

# NDJSON streaming
marcel -p --output-format stream-json "hello"
# {"type":"started","conversation":"2026-04-02T14-32"}
# {"type":"token","text":"Hi"}
# {"type":"token","text":" there!"}
# {"type":"done","cost_usd":0.0012,"turns":1}
```

In text mode, cost is printed to stderr so it doesn't pollute piped output.

## Slash Commands

Typing `/` opens a suggestion dropdown above the input. Use `Up`/`Down` to navigate, `Tab` or `Enter` to accept, `Escape` to dismiss. The dropdown scrolls to keep the selection visible (max 6 rows at a time).

| Command | Description |
|---------|-------------|
| `/clear` | Clear the chat history |
| `/compact` | Compact conversation context (requires server) |
| `/config` | Show config (editing via `~/.marcel/config.toml`) |
| `/cost` | Show token usage and cost (requires server) |
| `/export [path]` | Export conversation to markdown file (default: `marcel-export.md`) |
| `/help` | Show available commands |
| `/memory` | Show Marcel's memory (requires server) |
| `/model [name]` | Show or set the current model (`/model claude-opus-4-6`) |
| `/new` | Start a new conversation (clears chat, resets cost) |
| `/reconnect` | Reconnect to the Marcel server |
| `/resume <id>` | Resume a specific conversation by ID |
| `/sessions` | List recent conversations (requires server) |
| `/status` | Show connection and server status |
| `/exit` / `/quit` | Exit Marcel |

## Session Management

Marcel tracks conversations across CLI sessions:

- **Automatic tracking**: The CLI persists the last conversation ID to `~/.marcel/cli_state.json` whenever a new conversation starts.
- **Continue** (`-c`): Resumes the most recent conversation for the current user.
- **Resume** (`-r [ID]`): Resumes a specific conversation by ID. If no ID is given, lists recent conversations and auto-resumes the most recent.
- **Sessions** (`/sessions`): Lists recent conversations from the server with their IDs and channels.
- **New** (`/new`): Starts a fresh conversation without restarting the CLI.

## Terminal Resize

The header reflows automatically when the terminal is resized:

- **≥ 88 columns** — 3-column layout: mascot + runtime + server
- **≥ 60 columns** — 2-column layout: mascot + runtime
- **< 60 columns** — 1-column layout: mascot only

## Architecture

The CLI is a native Rust binary built on **ratatui** (TUI framework) and **crossterm** (terminal backend), modeled after codex-cli's architecture.

### Component hierarchy

```
main() → Cli::parse() → App::run() or Print::run()
  ├── Header        — fixed top panel (Renderable)
  ├── ChatView      — scrollable chat history (Renderable, flex=1)
  ├── InputBox      — text input with cursor + history (Renderable, flex=0)
  └── StatusBar     — footer: connection, model, cost, turns (Renderable, flex=0)
```

### Render system

A custom `Renderable` trait (inspired by codex-cli) provides:

```rust
pub trait Renderable {
    fn render(&self, area: Rect, buf: &mut Buffer);
    fn desired_height(&self, width: u16) -> u16;
    fn cursor_pos(&self, area: Rect) -> Option<(u16, u16)>;
}
```

Layout primitives:

- **FlexLayout** — vertical flex distribution (like Flutter's Flex). Children with `flex=0` get their `desired_height()`, flex children share remaining space.
- **ColumnLayout** — simple vertical stack.

### Event loop

```
loop {
    terminal.draw(|frame| { FlexLayout.render() })
    drain streaming tokens from WebSocket channel
    poll crossterm keyboard events
    handle input → command routing or WebSocket send
}
```

### Files

| File | Purpose |
|------|---------|
| `src/marcel_cli/src/main.rs` | Entrypoint, clap argument parsing, mode branching |
| `src/marcel_cli/src/app.rs` | TUI event loop, command routing, session resume, tab completion |
| `src/marcel_cli/src/print.rs` | Non-interactive print mode (text, json, stream-json) |
| `src/marcel_cli/src/state.rs` | Persistent CLI state (last conversation ID per user) |
| `src/marcel_cli/src/tui.rs` | Terminal init/restore (raw mode, alt screen) |
| `src/marcel_cli/src/render.rs` | `Renderable` trait, `FlexLayout`, `ColumnLayout` |
| `src/marcel_cli/src/header.rs` | Header panel with responsive column layout |
| `src/marcel_cli/src/ui.rs` | `ChatView`, `InputBox` (with history), `StatusBar` (with cost) |
| `src/marcel_cli/src/chat.rs` | WebSocket client, streaming token receiver, `TurnMeta` |
| `src/marcel_cli/src/config.rs` | TOML config loader |

### Dependencies

| Crate | Purpose |
|-------|---------|
| `ratatui` | Terminal UI framework |
| `crossterm` | Terminal backend (events, cursor, colors) |
| `clap` | CLI argument parsing |
| `tokio` | Async runtime |
| `tokio-tungstenite` | WebSocket client |
| `pulldown-cmark` | Markdown parsing (future) |
| `reqwest` | HTTP client (health check, conversations API) |
| `serde` / `toml` | Config and message serialization |
