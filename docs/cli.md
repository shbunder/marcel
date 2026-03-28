# Marcel CLI

The Marcel CLI is a Textual-based terminal interface for chatting with the Marcel agent. It connects to the `marcel-core` backend over WebSocket and streams responses in real time.

## Installation

Install the package with `uv` (recommended):

```bash
uv tool install .
```

Or with pip:

```bash
pip install .
```

After installation the `marcel` command is available on your `PATH`.

## Configuration

Marcel reads its configuration from `~/.marcel/config.toml`. If the file does not exist, it is created automatically with defaults on first run.

```toml
# Marcel server address
host = "localhost"
port = 8000

# Your user slug
user = "shaun"

# Long-lived developer token (auth not yet enforced in Phase 1)
token = ""
```

### Connecting to a remote server

To connect to Marcel running on your NUC or another host, update `host` (and optionally `port`) in the config file:

```toml
host = "192.168.1.50"
port = 8000
```

## CLI Flags

Flags override values in the config file for the current session only.

| Flag | Description |
|------|-------------|
| `--host HOST` | Marcel server hostname |
| `--port PORT` | Marcel server port |
| `--user USER` | User slug sent with each message |

Examples:

```bash
# Connect to Marcel on the NUC
marcel --host 192.168.1.50

# Use a non-default port
marcel --host localhost --port 9000

# Send messages as a different user
marcel --user alice
```

## Usage

Run `marcel` to launch the TUI:

```bash
marcel
```

The interface shows:

- **Conversation panel** — scrollable chat history with the Marcel mascot displayed on connect.
- **Status bar** — shows `connected ●`, `connecting…`, or `disconnected ○`.
- **Input bar** — type your message and press `Enter` to send.

Marcel's responses appear in full once the server has finished streaming them.

## Key Bindings

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `Ctrl+Q` | Quit |
| `Ctrl+C` | Quit |
