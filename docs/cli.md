# Marcel CLI

The Marcel CLI is a prompt_toolkit-based terminal interface for chatting with the Marcel agent. It connects to the `marcel-core` backend over WebSocket and streams responses in real time.

The CLI is a scrolling REPL with a responsive header. On startup the screen is cleared for a clean view. When the terminal is resized the header reflows its layout automatically. Chat history is pushed to terminal scrollback during resize (scroll up to see it).

## Installation

Install the package with `uv` (recommended):

```bash
uv tool install .
```

Or use the install script for standalone deployment:

```bash
bash install.sh
```

After installation the `marcel` command is available on your `PATH`.

For development, run directly from source without installing:

```bash
make cli
```

## Configuration

Marcel reads its configuration from `~/.marcel/config.toml`. If the file does not exist, it is created automatically with defaults on first run.

```toml
host = "localhost"
port = 7420
user = "shaun"
model = "claude-sonnet-4-6"
token = ""
```

Edit the config interactively from inside the CLI with `/config` (opens `nano`), or set individual fields with `/config <field> <value>`.

### Connecting to a remote server

To connect to Marcel running on another host, update `host` (and optionally `port`) in the config file:

```toml
host = "192.168.1.50"
port = 7420
```

## CLI Flags

Flags override values in the config file for the current session only.

| Flag | Description |
|------|-------------|
| `--host HOST` | Marcel server hostname |
| `--port PORT` | Marcel server port |
| `--user USER` | User slug sent with each message |
| `--model MODEL` | Model to use for this session |

Examples:

```bash
marcel --host 192.168.1.50
marcel --host localhost --port 9000
marcel --user alice
marcel --model claude-sonnet-4-6
```

## Usage

Run `marcel` (or `make cli` during development) to launch the CLI:

```bash
marcel
```

The interface shows:

- **Fixed header** — responsive panel with mascot, runtime info (CLI version, user, model), and server status. Adapts between 3-column, 2-column, and 1-column layouts depending on terminal width.
- **Chat area** — scrolling conversation history below the header. User messages are prefixed with `❯`, assistant responses with `●`.
- **Input prompt** — type your message and press `Enter` to send.

On exit (`/exit`, `/quit`, `Ctrl+C`, or `Ctrl+D`), Marcel disconnects and returns to the shell.

## Slash Commands

Type `/` to see available commands with tab completion.

| Command | Description |
|---------|-------------|
| `/clear` | Clear the chat area and redraw the header |
| `/compact` | Compact conversation context (requires server) |
| `/config` | Show or set config (`/config host <value>`) |
| `/cost` | Show token usage and cost (requires server) |
| `/help` | Show available commands |
| `/memory` | Show Marcel's memory (requires server) |
| `/model` | Show or set the current model (`/model claude-sonnet-4-6`) |
| `/reconnect` | Reconnect to the Marcel server |
| `/status` | Show connection and server status |
| `/exit` | Exit Marcel |
| `/quit` | Exit Marcel |

## Terminal Resize

The header reflows automatically when the terminal is resized:

- **≥ 88 columns** — 3-column layout: mascot + runtime + server
- **≥ 60 columns** — 2-column layout: mascot + runtime
- **< 60 columns** — 1-column layout: mascot only

On resize the screen is cleared, the header is reprinted at the new width, and prompt_toolkit redraws the prompt. Previous conversation is pushed to terminal scrollback (scroll up to see it).

## Architecture Notes

- **prompt_toolkit** handles the input prompt, completions, and styled input
- **Rich** renders the header panel with responsive column layout
- **Polling resize monitor** checks terminal width every 250ms, debounces until stable, then clears screen and reprints the header
- **prompt_toolkit SIGWINCH disabled** (`session.app.handle_sigwinch = False`) to prevent duplicate prompt rendering during resize drags
- All screen management writes to `sys.__stdout__` to bypass `patch_stdout` and avoid interfering with prompt_toolkit's rendering
