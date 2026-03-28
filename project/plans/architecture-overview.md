# Marcel Architecture Plan

> Written: 2026-03-26
> Status: Active — all open questions resolved

---

## Resolved Decisions

| # | Question | Decision |
|---|----------|----------|
| Q1 | Server hosting | NUC at home + Cloudflare Tunnel for external access |
| Q2 | CLI architecture | TUI (Textual) on NUC; thin API client with configurable host:port |
| Q3 | Encryption | Defer to Phase 5 — plain SQLite for Phase 1–4, no performance overhead |

### Q1 detail — NUC + Cloudflare Tunnel

Marcel runs on a home NUC connected to a Telenet router (Belgium). Telenet uses CGNAT, meaning port forwarding is unreliable. **Cloudflare Tunnel** (`cloudflared`) is the solution:

- Install `cloudflared` on the NUC as a system service
- Creates an outbound tunnel to Cloudflare's edge — no port forwarding, works behind CGNAT
- Stable public URL: `https://marcel.yourdomain.com` → NUC:8000
- Free tier is sufficient for personal use
- Telegram webhooks, iOS away-from-home, and React app access all go through this URL
- On home network: clients can also connect directly to `http://nuc-local-ip:8000` to skip the tunnel

### Q2 detail — CLI as TUI

The CLI is a **Terminal User Interface** (using `Textual` or `rich` + `prompt_toolkit`) rather than a one-shot CLI tool. It runs interactively, with a scrollable conversation view, streaming responses, and optional multi-panel layout.

Key requirement: the CLI takes a `--host` / `--port` config (or reads from `~/.marcel/config.toml`), so it can be installed on the developer's laptop and pointed at the NUC remotely.

---

## Architecture Vision

```
┌─────────────────────────────────────────────────────────────────┐
│                          CLIENTS                                │
│                                                                 │
│  marcel-cli (TUI)   marcel-app (React)   Telegram   marcel-ios  │
│  NUC / laptop       NUC localhost         bot        iPhone     │
│  WebSocket          WebSocket + REST    long-poll   WebSocket   │
└──────────┬──────────────────┬──────────────┬──────────┬─────────┘
           │  home network    │              │  Cloudflare Tunnel  │
           └──────────────────┴──────────────┴──────────┴─────────┘
                                       │
                              FastAPI (port 8000)
                                       │
┌──────────────────────────────────────▼──────────────────────────┐
│                        MARCEL-CORE (NUC)                        │
│                                                                 │
│  ┌───────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ Agent Engine  │  │  User Store  │  │    Memory Store       │ │
│  │ claude_agent_ │  │  SQLite      │  │    SQLite             │ │
│  │    sdk        │  │              │  │  (conversations +     │ │
│  └───────┬───────┘  └──────────────┘  │   distilled memory)  │ │
│          │                            └───────────────────────┘ │
│  ┌───────▼────────────────────────────────────────────────────┐ │
│  │             Skill + Integration Registry                   │ │
│  │      cmd-tool → skills.json → HTTP executor                │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─────────────────────────┐  ┌─────────────────────────────┐  │
│  │    Telegram Bot         │  │   Self-Modification         │  │
│  │   (aiogram, inside      │  │   Watchdog + git rollback   │  │
│  │    marcel-core)         │  │                             │  │
│  └─────────────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Breakdown

### `marcel-core` (Python, `src/marcel_core/`)

FastAPI server wrapping a `claude_agent_sdk` agent loop. Runs as a systemd service on the NUC.

**Key modules:**
```
src/marcel_core/
  api/           # FastAPI routes (chat WS, REST for auth/users/skills)
  agent/         # claude_agent_sdk agent setup, tool registration
  storage/       # Flat-file read/write helpers (users, conversations, memory)
  skills/        # Skill registry, cmd-tool dispatcher, HTTP executor
  auth/          # JWT issue/verify, user identity, channel ID mapping
  watchdog/      # Self-modification safety, git commit/revert/restart
  telegram/      # aiogram bot, message routing into agent
```

**Runtime:** `uvicorn marcel_core.main:app --host 0.0.0.0 --port 8000`

The Telegram bot runs as a background task within the same process (aiogram's async loop alongside FastAPI's).

---

### `marcel-cli` (Python, `src/marcel_cli/`)

An interactive TUI built with **Textual** (or `rich` + `prompt_toolkit` if Textual proves heavy).

Features:
- Scrollable conversation panel with streaming response rendering
- Markdown rendering inline (bold, code blocks, lists)
- Input bar at the bottom (multi-line with shift+enter)
- Optional side panel: current session memory summary
- Config: reads `~/.marcel/config.toml` for `host`, `port`, `token`
- Accepts `--host` / `--port` flags to override config at launch

Install on laptop: `pip install marcel-cli`, then `marcel --host <nuc-ip>` or `marcel --host marcel.yourdomain.com`.

The CLI identifies as channel `cli` and authenticates with a long-lived developer token (generated once, stored in `~/.marcel/config.toml`).

---

### `marcel-app` (React + Vite, `src/marcel_app/`)

A React SPA served locally (or via the NUC on home network). Talks to `marcel-core` via REST + WebSocket.

**Phase 2:** Plain React chat UI with conversation history, WebSocket streaming.

**Phase 3:** CopilotKit integration:
- `@copilotkit/react-ui` for the chat panel (CopilotSidebar or full-screen)
- `@copilotkit/react-core` for agent state streaming
- CopilotKit speaks AG-UI protocol natively — pairs with the `marcel-core` AG-UI adapter endpoint

---

### `marcel-ios` (Swift + WKWebView, `src/marcel_ios/`)

**Strategy:** WKWebView shell wrapping the React web app. This gives us CopilotKit's full UI without reimplementing it in Swift. Native Swift handles everything the web can't:

- **Keychain**: JWT token storage (the WebView talks to a Swift bridge to read/write tokens)
- **Push notifications**: APNs registration + display, deep-link into conversation on tap
- **OAuth callbacks**: Universal Links (`marcel.yourdomain.com/auth/callback`) caught by the app
- **App Store / TestFlight**: Distribution

The WKWebView loads `https://marcel.yourdomain.com` (the React app via Cloudflare Tunnel). On home WiFi it can optionally load the local IP for lower latency.

**JavaScript ↔ Swift bridge** (`WKScriptMessageHandler`) for:
- Token reads/writes (so the web app never stores JWTs in localStorage — they live in Keychain)
- Requesting push notification permission
- Haptic feedback on message send/receive

This avoids reimplementing the chat UI twice. If native feel becomes critical later, SwiftUI + AG-UI protocol is the upgrade path — the API contract doesn't change.

---

### Telegram Bot (inside `marcel-core`)

Uses `aiogram` (async, well-maintained). Runs as a background task inside the FastAPI process.

- **Long-polling** in Phase 2 (works behind Cloudflare Tunnel too, no public webhook needed)
- **Webhook** in Phase 3 (faster, uses the Cloudflare Tunnel URL)
- First `/start` from unknown Telegram user → creates a new Marcel user, links Telegram ID
- Output: Telegram MarkdownV2 formatting, inline keyboards for action confirmations
- No rich UI — plain text + structured messages only

---

## Multi-User Identity & Authentication

### Identity model

Each user is a directory under `data/users/{user_slug}/`. The slug is a short human-readable identifier (e.g. `shaun`, `marie`).

```
data/users/shaun/
  profile.md          # display name, known facts, preferences
  auth.json           # hashed password, refresh token (Phase 2)
  channel_ids.json    # {"telegram": "123456789", "cli": "mac-fingerprint"}
  oauth/
    google.json       # {access_token, refresh_token, expires_at}
  conversations/
    index.md          # one line per conversation: date, filename, short description
    2026-03-26T14-32.md   # full turn-by-turn transcript
  memory/
    index.md          # one liner per memory file (mirrors MEMORY.md pattern)
    calendar.md       # distilled facts about calendar preferences
    family.md         # family members, relationships
    shopping.md       # habits, preferences
```

Channel identity lookup: to find which user owns a Telegram ID, scan `channel_ids.json` files. With ~10 users this is trivial; no index needed.

### Auth per channel

| Channel  | Mechanism |
|----------|-----------|
| CLI      | Long-lived signed JWT in `~/.marcel/config.toml` — generated once by `marcel auth init` |
| React app | Email + password → short-lived JWT + refresh token (httpOnly cookie) |
| iOS app  | Email + password → JWT stored in Keychain via Swift bridge |
| Telegram | Telegram `user_id` is the identity — no separate password. First `/start` creates account. |

---

## Memory Architecture

### Two layers

**1. Conversation history** — one markdown file per session, append-only.

**2. Distilled memory** — topic-scoped markdown files extracted from conversations. Free-form prose + bullet lists, not key-value rows.

### Conversation file format

```markdown
# Conversation — 2026-03-26T14:32 (channel: cli)

**User:** What's on my calendar this week?
**Marcel:** You have a dentist appointment Tuesday at 10am and a team lunch Thursday.

**User:** Move the dentist to Thursday afternoon.
**Marcel:** Done — dentist moved to Thursday at 3pm.
```

### Conversation index (`conversations/index.md`)

```markdown
- [2026-03-26T14-32](2026-03-26T14-32.md) — calendar check, moved dentist appointment
- [2026-03-25T09-11](2026-03-25T09-11.md) — set up Google Calendar connection
- [2026-03-24T20-44](2026-03-24T20-44.md) — weekly schedule overview
```

### Memory file format

```markdown
# Calendar Preferences

Shaun prefers dentist appointments in the afternoon.
Shaun's team lunch is recurring every Thursday.
Work calendar: primary Google Calendar account.
```

### Memory index (`memory/index.md`)

```markdown
- [calendar.md](calendar.md) — appointment preferences, recurring events
- [family.md](family.md) — family members, relationships, birthdays
- [shopping.md](shopping.md) — shopping habits, preferred stores
```

### Agent memory loop

On each turn:
1. Read `memory/index.md` — load all relevant memory files into context
2. Read last N turns from the current conversation file
3. Inject as system context block before the Claude call
4. After response: agent optionally appends new facts to memory files or creates new topic files, updates `memory/index.md` if new file created
5. Append this turn to the conversation file

Concurrent writes from two channels for the same user: write-then-rename for atomic file updates + per-user asyncio lock in the server process.

**Claude Code's `~/.claude/` memory is NOT used for user memory.** It remains exclusively for Marcel's coder-mode self-improvement context (the developer's notes, patterns, project state).

---

## Skill + Integration System

### Design

Integrations are **data, not code**. No new Python needed to add an integration — just a JSON config entry.

```
src/marcel_core/skills/
  registry.py       # loads skills.json, validates entries
  executor.py       # resolves auth, builds HTTP request, applies transform
  skills.json       # all integration configs
  descriptions/     # one .md file per skill, injected into system prompt
```

The agent has one registered tool:

```python
async def cmd(skill: str, **kwargs: str) -> str:
    """Execute a registered integration. skill is the dotted skill name."""
```

### `skills.json` entry shape

```json
{
  "calendar.list_events": {
    "description": "List upcoming calendar events",
    "method": "GET",
    "url": "https://www.googleapis.com/calendar/v3/calendars/primary/events",
    "auth": {
      "type": "oauth2",
      "provider": "google",
      "scopes": ["https://www.googleapis.com/auth/calendar.readonly"]
    },
    "params": {
      "maxResults": { "from": "args.limit", "default": 10 },
      "timeMin":    { "from": "args.from_date", "transform": "iso8601" }
    },
    "response_transform": "jq:.items[] | {title: .summary, start: .start.dateTime}"
  }
}
```

The executor: look up config → resolve user's OAuth token → build request → call → transform → return string.

### OAuth flow per channel

1. User: "connect my Google Calendar"
2. Agent calls `cmd("auth.oauth_start", provider="google")`
3. Executor generates authorization URL + PKCE verifier
4. **React/iOS**: open URL in WebView → catch `marcel.yourdomain.com/auth/callback` → exchange code → store token
5. **CLI**: print URL → user visits → pastes authorization code back
6. **Telegram**: send URL as message → user visits → Marcel polls for completion
7. Token stored in `oauth_tokens (user_id, provider, access_token, refresh_token, expires_at)`

---

## Self-Modification + Git Rollback

### Flow

```
1. Agent proposes code changes
2. User approves ("yes, do it")
3. Marcel: git add -A && git commit -m "checkpoint: pre-modification $(date)"
4. Apply changes
5. Marcel: git add -A && git commit -m "feat: <description>"
6. Watchdog: send SIGTERM to uvicorn → wait → start new process
7. Watchdog: GET /health every 2s for up to 30s
8a. 200 OK → notify user: "Done. Marcel is running the new version."
8b. Timeout / non-200:
    → git revert HEAD --no-edit
    → git commit -m "revert: auto-rollback after failed restart"
    → restart from reverted code
    → notify user: "Something went wrong — Marcel rolled back to the previous version."
```

### Watchdog

Separate lightweight Python process (`src/marcel_core/watchdog/main.py`). Manages `uvicorn` as a subprocess. The watchdog binary is **never modified by Marcel** (enforced by self-modification safety rules in CLAUDE.md and by code review).

On NUC: watchdog runs as a systemd service. Marcel's service unit runs inside the watchdog, not directly.

---

## Channel-Adaptive Output

The agent always knows which channel it's serving (injected in system prompt). Each channel gets different instructions:

| Channel  | Format instructions |
|----------|-------------------|
| CLI (TUI) | Rich markdown — headers, bold, code blocks, bullet lists |
| React app | Full markdown + optional structured JSON for card rendering |
| iOS (WebView) | Same as React app |
| Telegram | Telegram MarkdownV2 only — no HTML, no unsupported syntax |

The API response envelope includes `"channel"` so clients can apply additional rendering logic.

---

## Implementation Phases

### Phase 1 — Core Foundation

**Goal:** Marcel runs on the NUC, answers questions, remembers things, developer can chat via TUI.

**Deliverables:**
- `marcel-core`: FastAPI + claude_agent_sdk agent loop
- Flat-file data layout: `data/users/{slug}/` with profile, conversations, memory
- Single developer user, no auth (localhost only, port 8000)
- Per-turn memory load + post-turn memory extraction + conversation file append
- `marcel-cli`: Textual TUI, WebSocket streaming, `--host/--port` config
- `cmd` tool + `skills.json` plumbing (no real integrations yet)
- Watchdog process + git rollback flow
- `make serve` starts watchdog → starts uvicorn

**Out of scope:** multi-user auth, Telegram, React, iOS, OAuth

---

### Phase 2 — Multi-Channel + Auth

**Goal:** Family can use Marcel. Telegram works. React web app live. Proper user accounts.

**Deliverables:**
- JWT auth (register, login, refresh) — email + password
- Channel identity table + per-channel auth flows
- Cloudflare Tunnel setup on NUC (`cloudflared` as systemd service)
- Telegram bot (aiogram, long-polling) with `/start` registration flow
- `marcel-app`: React SPA, Vite, WebSocket streaming chat, conversation history sidebar
- Channel-adaptive output formatting in agent system prompt

---

### Phase 3 — Skills + Integrations + Rich UI

**Goal:** Marcel does things. First real integrations. CopilotKit in web app.

**Deliverables:**
- Full `cmd` executor: auth resolution, HTTP calls, response transform
- OAuth2 PKCE flow (web + CLI + Telegram paths)
- `oauth_tokens` table with per-user encrypted storage (light encryption — not full Phase 5)
- First integrations (priority TBD): Google Calendar, Home Assistant, shopping list
- CopilotKit in `marcel-app` (AG-UI adapter endpoint in marcel-core)
- Switch Telegram to webhook mode

---

### Phase 4 — iOS App

**Goal:** Native-feeling iPhone app with push notifications.

**Deliverables:**
- SwiftUI shell + WKWebView loading React app (+ CopilotKit UI)
- Swift ↔ JS bridge: Keychain token storage, haptics, notification permission
- APNs push notifications: new message while app backgrounded
- Universal Links for OAuth callbacks
- TestFlight beta distribution

---

### Phase 5 — Hardening + Privacy

**Goal:** Encryption at rest, resilience, observability.

**Deliverables:**
- Per-user Argon2-derived encryption key for memory + oauth_tokens rows
- Key recovery design (iCloud Keychain backup or recovery phrase)
- Rate limiting on API endpoints
- Structured logging (structlog) + log rotation
- Health metrics endpoint for Grafana/Prometheus (optional)
- E2E encryption feasibility assessment and decision

---

## Critical Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Self-modification breaks Marcel | Watchdog + git rollback (Phase 1) |
| User memory cross-contamination | All DB queries scoped by `user_id`; no shared query paths |
| Telegram exposes private family data | Inform users at setup: Telegram bots are not E2E encrypted |
| OAuth tokens stored in plaintext Phase 1–3 | Light column encryption in Phase 3; full Argon2 keys in Phase 5 |
| Watchdog gets modified by Marcel | Watchdog is in the off-limits self-modification boundary |
| NUC goes offline → all channels dead | Out of scope for now; future: UPS + auto-restart on power recovery |
| Cloudflare Tunnel goes down | `cloudflared` auto-reconnects; home network users fall back to direct IP |
