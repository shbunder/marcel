# Marcel CLI

The Marcel CLI is a prompt_toolkit-based terminal interface for chatting with the Marcel agent. It connects to the `marcel-core` backend over WebSocket and streams responses in real time.

The CLI runs in the terminal's **alternate screen buffer** (like vim or htop), giving it a clean, isolated screen. A fixed header at the top displays connection status and runtime info, while the chat area below scrolls independently. When the terminal is resized, the header reflows its responsive layout without disturbing chat history.

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

On exit (`/exit`, `/quit`, `Ctrl+C`, or `Ctrl+D`), the alternate screen is closed and the original terminal is restored.

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

Chat history below the header is preserved during resize. The header area is redrawn in-place using absolute cursor addressing and ANSI scroll regions to avoid disturbing the conversation.

## Architecture Notes

- **Alternate screen buffer** (`\033[?1049h`) isolates the CLI from the shell
- **Scroll region** (`DECSTBM`) pins the header at the top; chat scrolls below
- **prompt_toolkit** handles the input prompt, completions, and styled input
- **Rich** renders the header panel with responsive column layout
- **Polling resize monitor** checks terminal width every 200ms, debounces until stable, then redraws the header in-place without clearing chat content
- **prompt_toolkit SIGWINCH disabled** to prevent duplicate prompt rendering during resize drags
