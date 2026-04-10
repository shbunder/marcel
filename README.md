# Marcel

Marcel is a personal assistant for families and small organisations. A technically inclined person sets it up once on a home server or NUC; everyone else — partners, kids, parents, colleagues — uses it through Telegram on their phone or a terminal on their laptop.

Marcel is built on Claude (Anthropic's AI) and can understand natural language, access calendars, track family events, and adapt over time. Because it has access to its own codebase, the person who runs it can ask Marcel to add new behaviours or integrations, and Marcel will modify and redeploy itself.

## Who is it for?

| Role | What they do |
|------|-------------|
| **Admin** (tech-savvy) | Installs the server once, adds family members, connects integrations |
| **Users** (non-technical) | Chat with Marcel over Telegram or the CLI — no setup required |

For the full admin setup walkthrough, see [SETUP.md](SETUP.md).

## Architecture

Marcel runs as a central server. All clients are thin — they connect over WebSocket and stream responses back.

```
scripts/            # All setup and deployment scripts
src/
  marcel_core/      # Python — FastAPI backend, agent engine, skills, watchdog
  marcel_cli/       # Rust  — native TUI client (ratatui + crossterm)
```

See [docs/architecture.md](docs/architecture.md) for the full architecture overview.

## Quick start (server)

### Prerequisites

- Linux with Docker and Docker Compose
- `docker` group membership (`sudo usermod -aG docker $USER`)
- Systemd user session with lingering enabled (`sudo loginctl enable-linger $USER`)

### 1. Clone and configure

```bash
git clone https://github.com/shbunder/marcel.git
cd marcel
cp .env.example .env
nano .env          # add your ANTHROPIC_API_KEY and other secrets
```

### 2. Install and start

```bash
./scripts/setup.sh
```

This checks prerequisites, installs systemd units, builds the Docker image, and starts Marcel. Marcel will be running at `http://localhost:7420`.

Run `./scripts/setup.sh --check` to verify prerequisites without starting anything.

### 3. Add users

```bash
mkdir -p ~/.marcel/users/alice
mkdir -p ~/.marcel/users/bob
```

Then distribute the CLI to each user (see below).

## Scripts

All operational scripts live in `scripts/`:

| Script | What it does |
|--------|-------------|
| `scripts/install.sh` | Install the Rust CLI binary on a client machine |
| `scripts/setup.sh` | Full server setup — systemd + Docker + health check |
| `scripts/teardown.sh` | Stop Marcel and remove systemd units |
| `scripts/redeploy.sh` | Rebuild and restart the Docker container (with rollback) |

The systemd unit templates (`*.tmpl`) in `scripts/` are rendered and installed to `~/.config/systemd/user/` by `setup.sh`.

## Install the CLI (on client machines)

The Marcel CLI is a native Rust terminal interface. Install it with:

```bash
curl -fsSL https://raw.githubusercontent.com/shbunder/marcel/main/scripts/install.sh | bash
```

To pre-configure the server connection:

```bash
curl -fsSL https://raw.githubusercontent.com/shbunder/marcel/main/scripts/install.sh | bash -s -- \
  --host 192.168.1.50 \
  --port 7420 \
  --user alice
```

Or from a local clone:

```bash
./scripts/install.sh --host 192.168.1.50 --user alice
```

After installation, run `marcel` to connect.

### CLI config (`~/.marcel/config.toml`)

```toml
host  = "192.168.1.50"       # Marcel server address
port  = 7420
user  = "alice"              # Your user slug
token = ""                   # API token (must match MARCEL_API_TOKEN on the server)
model = "claude-sonnet-4-6"
```

## Common operations

```bash
# View logs
docker compose logs -f marcel

# Check service status
systemctl --user status marcel

# Restart (manual)
systemctl --user restart marcel

# Rebuild and restart (e.g. after a code change)
./scripts/redeploy.sh

# Stop Marcel
systemctl --user stop marcel

# Remove all systemd units
./scripts/teardown.sh
```

Or via Make:

```bash
make setup          # Install and start
make teardown       # Stop and remove units
make docker-restart # Rebuild and restart container
make docker-logs    # Tail logs
```

## Development

```bash
make serve          # Start the backend dev server (uvicorn --reload on :7421)
make cli-dev        # Build and run CLI (debug mode)
make cli            # Build and run CLI (release mode)
make check          # Format, lint, typecheck, and test
```

## License

MIT
