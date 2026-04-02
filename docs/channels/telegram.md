# Telegram Channel

Marcel can receive and respond to messages via Telegram. This works like any other channel: the same agent loop runs, the same memory and conversation history is used, and responses are formatted for Telegram's MarkdownV2 syntax.

## How it works

```
Telegram user
    │  sends message
    ▼
Telegram Bot API
    │  POST /telegram/webhook
    ▼
Marcel server (webhook.py)
    │  look up user from TELEGRAM_USER_MAP
    │  load/create conversation
    ▼
Agent loop (same as CLI)
    │  stream_response(user_slug, channel="telegram", ...)
    ▼
bot.send_message()
    │  POST to Telegram sendMessage
    ▼
Telegram user
    receives reply
```

Messages are buffered (not streamed) before sending — Telegram does not support real-time streaming. Responses use MarkdownV2 with an automatic plain-text fallback if formatting is rejected.

## Commands

| Command | Effect |
|---------|--------|
| `/start` | Show your chat ID for account linking |
| `/code <request>` | Enter coder mode — Marcel spawns a Claude Code agent with full file/shell/git capabilities to implement your request |
| `/done` | Exit coder mode early (back to normal assistant) |
| `/new` | Start a fresh conversation and exit coder mode |

### Coder mode

When you send `/code <describe the change>`, Marcel enters **coder mode**: it spawns a dedicated Claude Code agent that can read/write files, run shell commands, interact with git, and follow the full issue lifecycle (create → implement → test → ship).

Follow-up messages are routed to the same coder session automatically — no need to prefix every message with `/code`. The coder agent remembers the full conversation context. When the task is done or you send `/done`, Marcel reverts to normal assistant mode.

**Safety guardrails:**
- Only one coder task runs at a time (global lock prevents concurrent git conflicts).
- Auth files and CLAUDE.md files are protected from writes via `can_use_tool` callback.
- Coder tasks have a 10-minute timeout.

### Auto-new on inactivity

If no message is sent for 6 hours, Marcel automatically starts a fresh conversation on the next message. This prevents stale context from accumulating in long-lived Telegram chats.

## Prerequisites

- A running Marcel server reachable via a public HTTPS URL (required for webhooks).
- A Telegram bot token from [@BotFather](https://t.me/BotFather).

## Setup

### 1. Create a bot

Open Telegram and message **@BotFather**:

```
/newbot
```

Follow the prompts. At the end you'll receive a token like:

```
1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
```

### 2. Configure environment variables

Add to your `.env.local` (gitignored, never committed):

```env
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ

# Optional but recommended: validates incoming webhook requests
TELEGRAM_WEBHOOK_SECRET=some-random-secret
```

### 3. Find your Telegram chat ID and link your account

Start your bot in Telegram and send `/start`. It will reply with your chat ID. Then run this once to link it to your Marcel user:

```bash
uv run python - <<'EOF'
from dotenv import load_dotenv; load_dotenv('.env.local')
from marcel_core.telegram.sessions import link_user
link_user('shaun', 556632386)   # replace with your actual chat ID
print('Linked.')
EOF
```

This writes `~/.marcel/users/shaun/telegram.json` — local to your server.

### 4. Expose Marcel via Cloudflare Tunnel

Marcel must be reachable via a public HTTPS URL. The recommended approach for a home server is a Cloudflare Tunnel — no open ports or static IP required.

```bash
# Install cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# Authenticate (opens browser)
cloudflared tunnel login

# Create and route the tunnel
cloudflared tunnel create marcel
cloudflared tunnel route dns marcel your-domain.com
```

#### Run as a systemd service (starts on boot)

```bash
sudo tee /etc/systemd/system/cloudflared-marcel.service > /dev/null <<EOF
[Unit]
Description=Cloudflare Tunnel - Marcel
After=network.target marcel.service

[Service]
Type=simple
User=YOUR_USER
ExecStart=/usr/bin/cloudflared tunnel run marcel
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared-marcel
```

### 5. Run Marcel as a systemd service

For production use, run Marcel as a systemd service so it starts on boot and restarts on failure:

```bash
sudo tee /etc/systemd/system/marcel.service > /dev/null <<EOF
[Unit]
Description=Marcel Personal Agent
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/projects/marcel
ExecStart=/home/YOUR_USER/projects/marcel/.venv/bin/python -m marcel_core.watchdog.main
EnvironmentFile=/home/YOUR_USER/projects/marcel/.env
EnvironmentFile=/home/YOUR_USER/projects/marcel/.env.local
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now marcel
```

The service runs the **watchdog**, not uvicorn directly. The watchdog starts uvicorn as a subprocess, monitors it, and handles self-modification restarts with automatic git rollback if the new code fails to start. See [Self-Modification](../self-modification.md) for details.

**After a manual code deployment, restart the service:**

```bash
sudo systemctl restart marcel
```

Self-modification triggered by Marcel itself goes through the watchdog's restart flag mechanism — no manual restart needed in that case.

### 6. Register the webhook

Run this once after the tunnel and service are both running:

```bash
cd ~/projects/marcel && uv run python -c "
from dotenv import load_dotenv; load_dotenv('.env.local')
from marcel_core.telegram.bot import set_webhook
import asyncio, os
result = asyncio.run(set_webhook(
    'https://your-domain.com/telegram/webhook',
    secret=os.environ.get('TELEGRAM_WEBHOOK_SECRET', '')
))
print(result)
"
```

You should see:

```json
{"ok": true, "result": true, "description": "Webhook was set"}
```

Send a message to your bot — Marcel will reply.

## Adding more users

Each family member or household user needs their own Telegram account linked. The process is the same:

1. They send `/start` to the bot and share their chat ID with you.
2. Run `link_user` for their slug:

```bash
uv run python -c "
from marcel_core.telegram.sessions import link_user
link_user('alice', 987654321)
"
```

No restart needed — the lookup reads from disk on every message.

Each user gets their own conversation history and memory, just like the CLI.

## Removing the webhook

To switch back to polling mode or disconnect the webhook:

```bash
python - <<'EOF'
import asyncio
from dotenv import load_dotenv
load_dotenv()
from marcel_core.telegram.bot import delete_webhook
print(asyncio.run(delete_webhook()))
EOF
```

## Security

`TELEGRAM_WEBHOOK_SECRET` is **required**. Marcel returns `503` if it is not set and `403` if the request header does not match.

Telegram sends the secret as the `X-Telegram-Bot-Api-Secret-Token` header on every webhook request. Generate one with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Module reference

::: marcel_core.telegram.bot

::: marcel_core.telegram.sessions

::: marcel_core.telegram.webhook

::: marcel_core.agent.coder
