# Marcel Setup Guide

This guide is written for the **admin** — the technically inclined person who installs Marcel and manages the household or organisation account. Once setup is complete, other users don't need to read this: they just open Telegram or run `marcel` in a terminal.

## What you'll need

| Requirement | Notes |
|-------------|-------|
| **Anthropic API key** | Marcel uses Claude. Get one at [console.anthropic.com](https://console.anthropic.com/) — a payment method is required |
| **Always-on machine** | A Raspberry Pi, NUC, old laptop, or any Linux machine. macOS also works |
| **Docker + Docker Compose** | The server runs in a container |
| **Git** | For cloning the repo and self-modification support |
| **Telegram account** *(optional)* | To let family members chat with Marcel from their phones |

---

## Step 1: Clone and configure

```bash
git clone https://github.com/shbunder/marcel.git ~/projects/marcel
cd ~/projects/marcel
cp .env.example .env
```

Open `.env` and set your API key:

```bash
nano .env
```

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

You can also use `.env.local` (not checked into git) to keep secrets separate from the shared `.env`:

```bash
echo "ANTHROPIC_API_KEY=sk-ant-your-key-here" >> .env.local
```

## Step 2: Set up security

### API token (recommended)

Generate a shared secret that all clients must send to authenticate:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Add it to `.env.local`:

```
MARCEL_API_TOKEN=paste-your-token-here
```

Each family member's CLI config (`~/.marcel/config.toml`) needs the same token:

```toml
token = "paste-your-token-here"
```

### Credential encryption (recommended)

If you plan to connect Marcel to iCloud or other services, enable encryption for stored credentials:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

```
MARCEL_CREDENTIALS_KEY=paste-your-key-here
```

**Keep this key safe.** If you lose it, stored credentials must be re-entered.

## Step 3: Install and start the server

```bash
./scripts/setup.sh
```

This will:
1. Check prerequisites (Docker, Docker Compose, systemd, docker group membership)
2. Build the Docker image and start the container
3. Install user-level systemd units so Marcel restarts on reboot
4. Run a health check at `http://localhost:7420/health`

If any prerequisite is missing, the script tells you exactly what to fix.

To verify prerequisites without starting anything:

```bash
./scripts/setup.sh --check
```

## Step 4: Add family members

Each user needs a directory under `~/.marcel/users/`:

```bash
mkdir -p ~/.marcel/users/alice
mkdir -p ~/.marcel/users/bob
```

User slugs must be lowercase letters, numbers, hyphens, or underscores.

## Step 5: Distribute the CLI

On each family member's computer, run:

```bash
curl -fsSL https://raw.githubusercontent.com/shbunder/marcel/main/scripts/install.sh | bash -s -- \
  --host 192.168.1.50 \
  --port 7420 \
  --user alice
```

Replace `192.168.1.50` with your server's local IP and `alice` with the user's slug. This installs the `marcel` binary and creates `~/.marcel/config.toml`.

## Step 6: Set up Telegram (optional)

Telegram lets non-technical family members chat with Marcel from their phones without installing anything.

### 6a. Create a bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the token BotFather gives you (format: `123456789:ABCdef...`)

### 6b. Configure the bot

Add to `.env.local`:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdef-your-token
TELEGRAM_WEBHOOK_SECRET=<generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))">
```

### 6c. Link family members

1. Have each person message your bot with `/start` — the bot replies with their chat ID
2. On your server, link the chat ID to a user slug:

```bash
cd ~/projects/marcel
python3 -c "
from marcel_core.channels.telegram.sessions import link_user
link_user('alice', 123456789)  # replace with actual slug and chat ID
"
```

### 6d. Expose Marcel to the internet

Telegram needs to reach your server. The easiest option is a [Cloudflare tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/):

```bash
cloudflared tunnel --url http://localhost:7420
```

Then register the webhook:

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://your-tunnel.trycloudflare.com/telegram/webhook&secret_token=<YOUR_WEBHOOK_SECRET>"
```

## Day-to-day operations

```bash
# Check status
systemctl --user status marcel

# View logs
docker compose logs -f marcel

# Restart after a config change
systemctl --user restart marcel

# Rebuild and restart (after a code change)
./scripts/redeploy.sh

# Stop Marcel
systemctl --user stop marcel

# Remove everything (does not delete data)
./scripts/teardown.sh
```

## Troubleshooting

**Marcel won't start**
- Check `ANTHROPIC_API_KEY` is set: `grep ANTHROPIC_API_KEY .env .env.local`
- Check Docker logs: `docker compose logs -f marcel`
- Check systemd: `journalctl --user -u marcel.service`

**Telegram bot doesn't respond**
- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` are set
- Check webhook registration: `curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo`
- Make sure your tunnel is running

**"Invalid or missing API token" error**
- The `token` in `~/.marcel/config.toml` must match `MARCEL_API_TOKEN` in `.env.local`

**"No user specified" error**
- Set `user = "yourname"` in `~/.marcel/config.toml`
- Or set `MARCEL_DEFAULT_USER=yourname` in `.env.local`

## Configuration reference

### Server environment (`.env` / `.env.local`)

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude |
| `MARCEL_API_TOKEN` | Recommended | Token clients must send to authenticate |
| `MARCEL_CREDENTIALS_KEY` | Recommended | Passphrase for encrypting stored credentials |
| `MARCEL_DEFAULT_USER` | No | Fallback user when client doesn't specify one |
| `TELEGRAM_BOT_TOKEN` | For Telegram | Bot token from @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | For Telegram | Secret for validating webhook requests |
| `MARCEL_PORT` | No | Server port (default: `8000`) |
| `MARCEL_PUBLIC_URL` | For Mini App | Public HTTPS URL for Telegram Mini App buttons |
| `MARCEL_DATA_DIR` | No | Data directory (default: `~/.marcel/`) |
| `MARCEL_IDLE_SUMMARIZE_MINUTES` | No | Idle threshold before auto-summarization (default: `60`) |
| `MARCEL_TRACING_ENABLED` | No | Enable OpenTelemetry LLM tracing (default: `false`) |
| `MARCEL_TRACING_ENDPOINT` | No | OTLP endpoint for traces (default: `http://localhost:6006`) |

#### Model tiers (optional — three-tier ladder with per-tier backups)

Marcel runs fine with just `ANTHROPIC_API_KEY` set. The variables below add
cross-provider resilience: each tier has its own OpenAI-compatible backup,
and a shared local fallback explains outages in plain language instead of
showing a stack trace. Full details in
[docs/model-tiers.md](docs/model-tiers.md); session classifier config in
[docs/routing.md](docs/routing.md).

> **Breaking change (ISSUE-e0db47):** `MARCEL_BACKUP_MODEL` was removed.
> Migrate to the matching per-tier variable — typically
> `MARCEL_STANDARD_BACKUP_MODEL`.

| Variable | Required | Description |
|----------|----------|-------------|
| `MARCEL_FAST_MODEL` | No | FAST tier primary — used for simple lookups. Default: `anthropic:claude-haiku-4-5-20251001`. |
| `MARCEL_FAST_BACKUP_MODEL` | No | Cross-cloud backup for FAST. Example: `openai:gpt-4o-mini`. |
| `MARCEL_STANDARD_MODEL` | No | STANDARD tier primary — the daily driver. Default: `anthropic:claude-sonnet-4-6`. |
| `MARCEL_STANDARD_BACKUP_MODEL` | No | Cross-cloud backup for STANDARD. Example: `openai:gpt-4o`. |
| `MARCEL_POWER_MODEL` | No | POWER tier primary — used by the `power` subagent and skills with `preferred_tier: power`. Default: `anthropic:claude-opus-4-6`. |
| `MARCEL_POWER_BACKUP_MODEL` | No | Cross-cloud backup for POWER. Example: `openai:gpt-4o`. |
| `MARCEL_FALLBACK_MODEL` | No | Shared local fallback. Explains outages in turns; retries the task in jobs. Example: `local:ministral-3:14b`. Requires `MARCEL_LOCAL_LLM_URL` + `MARCEL_LOCAL_LLM_MODEL`. |
| `MARCEL_LOCAL_LLM_URL` | For local tier | OpenAI-compatible base URL of your local LLM (e.g. Ollama at `http://127.0.0.1:11434/v1`). See [docs/local-llm.md](docs/local-llm.md). |
| `MARCEL_LOCAL_LLM_MODEL` | For local tier | Model tag the local server serves (e.g. `ministral-3:14b`). |

### Client config (`~/.marcel/config.toml`)

```toml
host  = "192.168.1.50"       # Marcel server address
port  = 7420                 # Server port
user  = "alice"              # Your user slug
token = ""                   # API token (must match MARCEL_API_TOKEN)
model = "anthropic:claude-sonnet-4-6"  # Fully-qualified pydantic-ai model
```
