# Marcel CLI

The Marcel CLI is a native Rust terminal interface for chatting with the Marcel agent. It connects to the `marcel-core` backend over WebSocket and streams responses in real time with markdown rendering.

Built with **ratatui** + **crossterm** — the same stack used by [codex-cli](https://github.com/openai/codex). The CLI compiles to a single ~3.6MB binary with zero runtime dependencies.

## Installation

Build and install from source (requires Rust toolchain):

```bash
./install.sh
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

Marcel reads its configuration from `~/.marcel/config.toml`. If the file does not exist, it is created automatically with defaults on first run (via `install.sh`).

```toml
host = "localhost"
port = 7420
user = "shaun"
model = "claude-sonnet-4-6"
token = ""
```

### Connecting to a remote server

To connect to Marcel running on another host, edit the config file:

```toml
host = "192.168.1.50"
port = 7420
```

Or pass flags to the installer:

```bash
./install.sh --host 192.168.1.50 --port 7420
```

## Usage

Run `marcel` to launch the TUI:

```bash
marcel
```

The interface consists of four areas:

- **Header** — fixed panel at the top with mascot art, runtime info (CLI version, user, model), and server status. Adapts between 3-column, 2-column, and 1-column layouts depending on terminal width.
- **Chat view** — scrollable conversation history. User messages prefixed with `❯`, assistant responses rendered with basic markdown (headers, code blocks, lists). Streams tokens as they arrive.
- **Input** — bordered text field at the bottom with cursor editing. Shows placeholder text when empty.
- **Status bar** — connection state, current model, and `/help` hint.

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Send message or execute command |
| `Ctrl+C` / `Ctrl+D` | Quit |
| `PageUp` / `PageDown` | Scroll chat history |
| `Left` / `Right` | Move cursor in input |
| `Home` / `End` | Jump to start/end of input |
| `Backspace` / `Delete` | Delete characters |

## Slash Commands

Type `/` to use commands.

| Command | Description |
|---------|-------------|
| `/clear` | Clear the chat history |
| `/compact` | Compact conversation context (requires server) |
| `/config` | Show config (editing via `~/.marcel/config.toml`) |
| `/cost` | Show token usage and cost (requires server) |
| `/help` | Show available commands |
| `/memory` | Show Marcel's memory (requires server) |
| `/model` | Show or set the current model (`/model claude-opus-4-6`) |
| `/reconnect` | Reconnect to the Marcel server |
| `/status` | Show connection and server status |
| `/exit` | Exit Marcel |
| `/quit` | Exit Marcel |

## Terminal Resize

The header reflows automatically when the terminal is resized:

- **≥ 88 columns** — 3-column layout: mascot + runtime + server
- **≥ 60 columns** — 2-column layout: mascot + runtime
- **< 60 columns** — 1-column layout: mascot only

## Architecture

The CLI is a native Rust binary built on **ratatui** (TUI framework) and **crossterm** (terminal backend), modeled after codex-cli's architecture.

### Component hierarchy

```
main() → App::run()
  ├── Header        — fixed top panel (Renderable)
  ├── ChatView      — scrollable chat history (Renderable, flex=1)
  ├── InputBox      — text input with cursor (Renderable, flex=0)
  └── StatusBar     — footer line (Renderable, flex=0)
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
| `src/marcel_cli/src/main.rs` | Entrypoint |
| `src/marcel_cli/src/app.rs` | Event loop, command routing, WebSocket integration |
| `src/marcel_cli/src/tui.rs` | Terminal init/restore (raw mode, alt screen) |
| `src/marcel_cli/src/render.rs` | `Renderable` trait, `FlexLayout`, `ColumnLayout` |
| `src/marcel_cli/src/header.rs` | Header panel with responsive column layout |
| `src/marcel_cli/src/ui.rs` | `ChatView`, `InputBox`, `StatusBar` |
| `src/marcel_cli/src/chat.rs` | WebSocket client, streaming token receiver |
| `src/marcel_cli/src/config.rs` | TOML config loader |

### Dependencies

| Crate | Purpose |
|-------|---------|
| `ratatui` | Terminal UI framework |
| `crossterm` | Terminal backend (events, cursor, colors) |
| `tokio` | Async runtime |
| `tokio-tungstenite` | WebSocket client |
| `pulldown-cmark` | Markdown parsing (future) |
| `reqwest` | HTTP client (health check) |
| `serde` / `toml` | Config and message serialization |
