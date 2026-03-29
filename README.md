# Marcel

A self-adapting personal agent built on top of Claude Code that can observe its own behavior, identify gaps, and rewrite the code and configuration that governs how it works.

## Install the CLI

The Marcel CLI is a native Rust terminal interface. Install it with a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/shbunder/marcel/main/install.sh | bash
```

To configure the server connection during install:

```bash
curl -fsSL https://raw.githubusercontent.com/shbunder/marcel/main/install.sh | bash -s -- \
  --host 192.168.1.50 \
  --port 7420 \
  --user alice
```

This will:
1. Install the Rust toolchain (if not already present)
2. Clone the repository and build the `marcel` binary
3. Install it to `~/.cargo/bin/marcel`
4. Create a config file at `~/.marcel/config.toml`

After installation, run:

```bash
marcel
```

### Requirements

- Linux or macOS
- Git
- Internet connection (for Rust toolchain and crate downloads)

### Manual install (from source)

```bash
git clone https://github.com/shbunder/marcel.git
cd marcel
./install.sh
```

## Configuration

Edit `~/.marcel/config.toml` to configure the server connection:

```toml
host = "localhost"
port = 7420
user = "shaun"
model = "claude-sonnet-4-6"
token = ""
```

## Goal

This project tests the limits of vibe-coding by building a light agent framework on top of Claude Code. The agent has access to its own codebase and can rewrite and redeploy itself.

The idea originated with creating an agent that assists families in their day-to-day organization:
- tracking family events
- setting group reminders
- organizing day planning
- giving reminders for activities

By giving the agent the ability to rewrite itself, non-developer users in the family can more easily adapt Marcel to their needs.

## Architecture

Marcel is structured as a central API server (`marcel-core`) that all clients connect to via REST and WebSocket. The agent runs server-side; clients are thin.

```
src/
  marcel_core/    # Python — FastAPI backend, agent engine, skills, watchdog
  marcel_cli/     # Rust — native TUI client (ratatui + crossterm)
```

See [docs/architecture.md](docs/architecture.md) for the full architecture overview and [docs/cli.md](docs/cli.md) for CLI documentation.

## Development

```bash
make serve      # Start the backend server
make cli-dev    # Build and run CLI (debug mode)
make cli        # Build and run CLI (release mode)
make check      # Format, lint, typecheck, and test
```

## License

MIT
