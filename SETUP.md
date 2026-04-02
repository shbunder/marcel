# Marcel Setup Guide

A step-by-step guide for setting up Marcel as your family's personal assistant.

## What You'll Need

Before you start, make sure you have:

1. **An Anthropic API key** — Marcel uses Claude as its AI brain. Get one at [console.anthropic.com](https://console.anthropic.com/). This is a paid service (you'll need to add a payment method).

2. **A computer that stays on** — Marcel runs as a server. A Raspberry Pi, old laptop, or any always-on machine works. It needs Linux or macOS.

3. **Git** installed on that computer.

4. **(Optional) A Telegram account** — if you want to chat with Marcel through Telegram on your phone.

## Step 1: Install Marcel

On your server machine, run:

```bash
curl -fsSL https://raw.githubusercontent.com/shbunder/marcel/main/install.sh | bash -s -- \
  --user yourname
```

Replace `yourname` with a short name for yourself (lowercase, no spaces — e.g. `alice`, `dad`, `marco`).

This installs the Marcel CLI. To also start the background server:

```bash
curl -fsSL https://raw.githubusercontent.com/shbunder/marcel/main/install.sh | bash -s -- \
  --user yourname --server
```

## Step 2: Configure Your API Key

Edit the environment file:

```bash
nano ~/.marcel/.env.local
```

Add your Anthropic API key:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Save the file (in nano: `Ctrl+O`, then `Enter`, then `Ctrl+X`).

## Step 3: Set Up Security

### API Token (recommended)

Generate an API token so only your family can access Marcel:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the output and add it to `~/.marcel/.env.local`:

```
MARCEL_API_TOKEN=paste-your-token-here
```

Then update each family member's CLI config (`~/.marcel/config.toml`):

```toml
token = "paste-your-token-here"
```

### Credential Encryption (recommended)

If you plan to connect Marcel to iCloud or other services that store passwords, enable encryption:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Add the output to `~/.marcel/.env.local`:

```
MARCEL_CREDENTIALS_KEY=paste-your-key-here
```

**Keep this key safe!** If you lose it, you'll need to re-enter all stored credentials.

## Step 4: Add Family Members

Each person who will use Marcel needs a user directory:

```bash
mkdir -p ~/.marcel/users/alice
mkdir -p ~/.marcel/users/bob
```

User names must be lowercase letters, numbers, hyphens, or underscores only.

## Step 5: Set Up Telegram (optional)

If you want your family to chat with Marcel via Telegram:

### 5a. Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Choose a name (e.g., "Marcel Family Bot")
4. Choose a username (e.g., `marcel_family_bot`)
5. BotFather gives you a token like `123456789:ABCdef...` — copy it

### 5b. Configure the Bot

Add to `~/.marcel/.env.local`:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdef-your-token
```

Generate and add a webhook secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

```
TELEGRAM_WEBHOOK_SECRET=paste-your-secret-here
```

### 5c. Link Family Members

1. Have each person message your bot on Telegram with `/start`
2. The bot replies with their chat ID (a number)
3. On your server, open a Python shell:

```bash
cd ~/projects/marcel  # or wherever you cloned Marcel
python3 -c "
from marcel_core.telegram.sessions import link_user
link_user('alice', 123456789)  # replace with actual name and chat ID
"
```

### 5d. Expose Marcel to the Internet

Telegram needs to reach your server. The easiest way is a Cloudflare tunnel:

1. Install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
2. Run: `cloudflared tunnel --url http://localhost:7420`
3. Register the webhook with Telegram:

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://your-tunnel-url.trycloudflare.com/telegram/webhook&secret_token=<YOUR_WEBHOOK_SECRET>"
```

## Step 6: Start Marcel

### With Docker (recommended for production)

```bash
cd ~/projects/marcel
docker compose up -d
```

### Without Docker (development)

```bash
cd ~/projects/marcel
make serve
```

## Step 7: Test It

Open a terminal and run:

```bash
marcel
```

Type "Hello!" and Marcel should respond.

If using Telegram, send a message to your bot — it should reply.

## Troubleshooting

**Marcel won't start:**
- Check that `ANTHROPIC_API_KEY` is set in `~/.marcel/.env.local`
- Run `docker compose logs` to see error messages

**Telegram bot doesn't respond:**
- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` are set
- Check that the webhook is registered: `curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo`
- Make sure your tunnel is running

**"Invalid or missing API token" error:**
- Make sure the `token` in `~/.marcel/config.toml` matches `MARCEL_API_TOKEN` in `.env.local`

**"No user specified" error:**
- Set `user = "yourname"` in `~/.marcel/config.toml`
- Or set `MARCEL_DEFAULT_USER=yourname` in `.env.local`

## Configuration Reference

### Server environment (`.env.local`)

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Your Anthropic API key |
| `MARCEL_API_TOKEN` | Recommended | Token clients must send to authenticate |
| `MARCEL_CREDENTIALS_KEY` | Recommended | Passphrase for encrypting stored credentials |
| `MARCEL_DEFAULT_USER` | No | Fallback user when client doesn't specify one |
| `TELEGRAM_BOT_TOKEN` | For Telegram | Bot token from @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | For Telegram | Secret for validating webhook requests |
| `MARCEL_HOST` | No | Server bind address (default: `localhost`) |
| `MARCEL_PORT` | No | Server port (default: `7420`) |

### Client config (`~/.marcel/config.toml`)

```toml
host = "localhost"     # Server address
port = 7420            # Server port
user = "alice"         # Your user name
token = ""             # API token (must match MARCEL_API_TOKEN)
model = "claude-sonnet-4-6"  # AI model to use
```
