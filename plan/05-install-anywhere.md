# Install anywhere (F22) — decision doc

Written by the lead on 2026-10-15 for the owner. **Proposed**: nothing here is built until the owner OKs the
choices in [ADR-013](../project/decisions/ADR-013-install-anywhere.md). Stories: [F22](../project/features/F22.md).

## The goal

Anyone with a Claude subscription can run their own Marcel: the owner, a few family setups, colleagues who try it
as a proof of concept. **Each install is separate**: one person, their own computer (usually a **Mac laptop**), their
own Claude login. Nothing is shared between installs, except two things only the app's maker can provide: the
iPhone app (TestFlight) and its push key (a small push relay, below).

What a colleague does:

1. Installs Claude Code and logs in (`claude`, then `/login`) — the one thing Anthropic's terms require them to do
   themselves.
2. Installs the Marcel app from the TestFlight link.
3. Pastes one line in Terminal:
   `curl -fsSL https://raw.githubusercontent.com/shbunder/marcel/main/deploy/get.sh | bash`
4. Answers three questions (their name, their first buddy's name, "go ahead?"), types their Mac password once,
   and clicks one link to sign in to Tailscale (free).
5. Scans the QR code with the app. Done. `marcel uninstall` removes all of it.

No Docker, no domain, no router changes, no Apple developer account, no server to run.

## What forced the shape (research, 2026-10-15)

- **Terms.** A third-party app may not offer Claude login or route requests through someone's subscription. The
  carve-out: "an end user signing in to the unmodified Claude Code binary with their own Claude subscription"
  ([legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)). So Marcel stays "your computer
  plus the app"; the app never sees a Claude token.
- **The channel allowlist is admin-only.** `channelsEnabled` and `allowedChannelPlugins` are read "only from a managed
  source; placing them in user or project settings files has no effect"
  ([managed settings](https://code.claude.com/docs/en/managed-settings)). On a Mac that is
  `/Library/Application Support/ClaudeCode/`, on Linux `/etc/claude-code/`. Server-managed settings need a Team or
  Enterprise plan. **So one admin password prompt is unavoidable** while Marcel uses a custom channel in background
  sessions (ADR-001). The only no-password route is the development-channels flag in a terminal session, which takes
  the brain out of Claude's supervisor and is documented "for testing".
- **Push.** The app on TestFlight carries the owner's bundle id, so only the owner's APNs key can push to it. A
  colleague's computer can never hold that key.
- **Cloud quests report over public HTTPS** (`/api/session/report`, SP3). A private-network-only address would break
  them, so the hub needs a public address.
- **A laptop sleeps.** While it sleeps nothing on it runs, and nothing can push from it.

## The seven questions

### 1. Phone ↔ computer without a domain or port forwarding

| Option | Phone needs | Computer needs | Address | Verdict |
|---|---|---|---|---|
| **Tailscale Funnel**, run as the user (no admin) | nothing | a free Tailscale account; Marcel downloads `tailscaled` and runs it in user-space mode | stable `https://<computer>.<tailnet>.ts.net` | **Chosen** |
| Plain tailnet (Tailscale app on the phone) | the Tailscale VPN app | Tailscale | stable, private | Breaks cloud quest reports; one more app for family |
| Cloudflare quick tunnel (trycloudflare) | nothing | `cloudflared`, no account | **changes every restart** | Breaks the paired phone after every reboot; "testing only" |
| A relay the owner hosts (Happy-style, end-to-end encrypted) | nothing | nothing extra | stable | Owner runs a service every colleague depends on; the app and cloud reports need their own encryption layer |
| iroh / WebRTC peer-to-peer | — | — | — | Not HTTP: both ends need a bridge; cloud quests cannot use it; public relays "not for production" |
| Named Cloudflare tunnel + own domain (today) | nothing | domain, Cloudflare account | stable | **Kept as "advanced"** (`marcel connect cloudflare`); the NUC keeps it |

Funnel facts: free on the Personal plan, ports 443/8443/10000, a fixed bandwidth cap (no figure published), TLS ends
on the user's computer (Tailscale relays encrypted bytes). The Mac App Store Tailscale app does **not** support Funnel;
the open-source `tailscaled` in user-space mode should, without admin — **SP12 checks this on a Mac and on Linux**,
plus WebSockets, `/api`-only paths and recovery after sleep and a Wi-Fi change.
Fallback if SP12 fails: the owner-hosted encrypted relay (a Cloudflare Worker with Durable Objects, about $0–5 a month).

**The QR code carries:** the public address, the one-time pairing code (as today), and from S-02.13 the hub's push
key id. It never carries a Claude token.

### 2. Push notifications

| Option | Verdict |
|---|---|
| **A push relay the owner runs once** (a Cloudflare Worker holding the APNs key) | **Chosen.** The only way to reach the TestFlight build. One relay for every install; nothing per colleague |
| ntfy (public ntfy.sh) | **Kept as the no-relay fallback**: needs the separate ntfy app, topic name is the only secret, no encryption |
| Each install holds an APNs key | Impossible: the key belongs to the app's bundle id |
| Local notifications only | Only while the app is open; useless for "your quest finished" |

What the relay costs the owner: the Apple developer account ($99 a year, already needed for TestFlight) and a
Cloudflare Workers free plan (100,000 requests a day; one push is one request). Deployed once with `wrangler deploy`.

What the relay can see: a random handle per phone, the phone's APNs token, a daily counter, and **sealed bytes**. The
hub encrypts every notification with a key only that phone has (made on the phone, sent to its own hub when push is
turned on); a Notification Service Extension on the phone decrypts it. If the relay is down or gone, pushes stop and
the app still works.

**App distribution:** TestFlight external testing with a public link (up to 10,000 testers). Builds expire after 90
days, so the owner uploads a build at least every 90 days; the first build of each version needs Beta App Review
(usually 1–2 days). The App Store would need an always-on demo hub for Apple's reviewers (guideline 2.1): later, if
ever.

### 3. Packaging without Docker

**Hub and runner as two plain processes, one install, one `marcel` command.** Python 3.12 comes from `uv` (installed
into `~/.local/bin`, no admin). The hub runs from `~/marcel/src/hub` with `uv run`; the runner keeps running on that
Python with no dependencies.

Not merged into one process: the runner is exactly the helper a second computer needs (question 6), and a hub
restart must never touch running sessions. Trade-off: three background services instead of two (Claude's
supervisor, runner, hub), all handled by `marcel`.

### 4. Background services on macOS and Linux

| | macOS | Linux |
|---|---|---|
| Services | LaunchAgents in `~/Library/LaunchAgents` (no admin) | `systemd --user` units (no admin) |
| Redeploy trigger | `WatchPaths` on `~/marcel/state/redeploy.flag` | `marcel-redeploy.path` (today) |
| Nightly backup | `StartCalendarInterval` | `marcel-backup.timer` (today) |
| Runs when | the user is logged in (Mac mini: turn on automatic login) | always, with linger; `marcel install` tries `loginctl enable-linger` without sudo (allowed from a desktop session on most distros) and prints the one sudo line if not |
| Claude login | the login Keychain, readable by a LaunchAgent in the user's session (SP11 checks) | `~/.claude/.credentials.json` |

The redeploy and rollback flow (ADR-005, self-modification rule) stays the same: the hub writes the flag, the
service manager starts `deploy/redeploy.sh`, which checks health with the pre-change check and rolls back. It becomes
portable (no `flock`, GNU `stat` or `sha256sum`) and restarts through `marcel`, not Docker. On a colleague's install
(not the owner's repo) a rollback goes back to the previous commit locally and pushes nothing.

**On a laptop:** sleep, a closed lid and a new Wi-Fi are normal. Nothing has to stay awake. While the Mac sleeps the
app says "Your Mac is asleep or offline. Marcel will catch up when it wakes." When it wakes, Tailscale reconnects, the
sessions carry on and the app catches up from where it stopped (the event stream already resumes). A setting "Keep the
Mac awake while a quest runs" holds `caffeinate -i` (no admin) only while a quest works; a closed lid on battery still
sleeps. Cloud quests retry their report for up to an hour so a sleeping Mac loses nothing.

### 5. Claude Code version pin without the system-wide file

- **Auto-update off for Marcel only:** `DISABLE_AUTOUPDATER=1` in each Marcel service's environment (documented,
  [setup](https://code.claude.com/docs/en/setup)). The user's own `claude` keeps updating itself.
- **A private copy of the pinned version:** `marcel install` installs the pinned version with Claude's own installer
  and copies that binary to `~/marcel/claude/<version>/claude`. Marcel's services run that copy, so the user updating
  their own Claude Code no longer changes Marcel's (the installer keeps only two old versions, so pointing at its folder
  is not safe). SP11 checks a copied binary runs background sessions.
- The managed-settings file shrinks to the channel allowlist only, as a drop-in
  (`managed-settings.d/50-marcel.json`); it no longer turns updates off for the user's own sessions.

### 6. More computers

Second computers are the user's **own** (a desktop next to the laptop), not other people's.

- In the app: Settings › Computers › Add shows a join code. On the other computer, after installing Claude Code and
  logging in: `curl …/get.sh | bash -s join <code>`.
- That installs only the helper: Claude's supervisor, the runner and a **link** — one outbound WebSocket to the hub's
  public address. The hub sends runner requests down it; sessions on that computer reach their hub through a local
  stand-in on `127.0.0.1:7420` that forwards over the same link. No open ports, no tunnel on the second computer.
- Where a quest runs: the computer the user names; else the project's computer (where its repo lives); else the hub's
  computer; else an online one with room. Offline computers are skipped and said so.

### 7. Moving the owner's NUC

`marcel migrate` on the NUC: nightly-style backup first, stop the hub container, start the native hub on the same
`~/marcel/data` (same database, memory, phones), keep the Cloudflare named tunnel (same address, so phones stay
paired), health check. If unhealthy within two minutes: stop the native hub and start the container again. Docker
files are deleted only after the migration has run on the NUC.

## One person, many buddies (owner, 2026-10-15)

- **One install belongs to one person**, the Claude account holder. The people model from F17 (owner vs family
  members, `x-owner-only`, per-person isolation, pairing codes for someone else) goes.
- **A buddy** is a Pixel Zoo avatar with its own long-running brain session, chat, memory and look. That is today's
  *agent*: F17's "a brain per agent" (S-17.7), memory per agent (S-17.4) and avatar per agent (F19) all stay.
- **Each phone picks the buddy it shows**; switching is a tap on the avatar. A family member using one buddy is a side
  effect, not a login. Any paired phone can switch to any buddy.
- **Buddies message each other with Claude Code's own cross-session messages** (`SendMessage`, same machine, a local
  socket; [cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging)). Brains accept them;
  workers and side threads cannot send (the tools are denied), so a quest reading a web page cannot steer a brain.
  Marcel adds only what Claude Code lacks: no hook fires on an inbound message, so a plugin hook on the sender's
  `SendMessage` tells the hub, which shows "Marcel → Pip: …" in both chats. SP13 checks it.

| F17 piece | Fate |
|---|---|
| `users` table, `role`, `/users…` routes, `x-owner-only`, `require_owner` | **Retired**: one user row stays; routes go |
| Pairing codes for someone else (`/users/{id}/pairing-codes`) | Replaced by "pair another phone" (any paired phone) |
| Isolation per person (S-17.2, `test_isolation.py`) | Replaced by "a phone sees every buddy"; device-token checks stay |
| A brain per agent (S-17.7), memory per agent (S-17.4), avatars (F19) | **Kept**: they are buddies |
| Settings › People (S-17.6) | Becomes Settings › Buddies |
| ADR-006 (family on the owner's login) | Superseded: no family model; ADR-003 changed for brains |
