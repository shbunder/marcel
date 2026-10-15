---
id: ADR-013
title: Marcel installs on anyone's own computer — one person, many buddies, no Docker, no domain
status: Proposed
date: 2026-10-15
decided_by: lead (proposed); the owner decides
evidence: plan/05-install-anywhere.md; Claude Code docs read 2026-10-15 (legal-and-compliance, managed-settings, settings-reference, channels, setup, agent-view, cross-session-messaging, authentication); Tailscale Funnel and userspace-networking docs; Apple TestFlight and APNs key docs; owner's direction 2026-10-15 (chat, three messages)
---

# ADR-013 — Marcel installs on anyone's own computer

## Context

The owner wants colleagues and family setups to install Marcel themselves, each on their own computer (mostly a Mac
laptop) with their own Claude subscription. Each install is separate. Today an install needs Docker, the owner's
domain and Cloudflare tunnel, several sudo steps, Linux systemd and the owner's APNs key (`docs/operating.md`).

Anthropic does not let a third-party app offer Claude login or hold a user's tokens; a user signing in to the
unmodified Claude Code on their own machine is allowed. So Marcel stays "your computer plus the app".

The owner also narrowed the model (2026-10-15): one install is one person; that person has several **buddies**; each
phone picks which buddy it shows; buddies may message each other, preferably with Claude Code's own messaging.

Full research and option tables: [plan/05-install-anywhere.md](../../plan/05-install-anywhere.md).

## Options and decisions

| # | Question | Decision | Trade-off |
|---|---|---|---|
| 1 | Phone ↔ computer | **Tailscale Funnel**, `tailscaled` run as the user in user-space mode, only `/api/*` published. Named Cloudflare tunnel stays as `marcel connect cloudflare` (advanced; the NUC keeps it) | Each person needs a free Tailscale account and one sign-in click; the hub is on the public internet, guarded by device tokens as today |
| 2 | Push | **One push relay the owner runs** (a Cloudflare Worker with the APNs key); the hub seals each notification for the phone, so the relay sees only a handle, an APNs token and sealed bytes. ntfy stays as the no-relay fallback | The owner keeps one free-tier Worker running for everyone; if it is down, pushes stop, nothing else |
| 3 | Packaging | **Hub and runner as two native processes**, Python from `uv`, one `marcel` command; no Docker | Three services instead of two, but the runner doubles as the second-computer helper and a hub restart never stops sessions |
| 4 | Services | **LaunchAgents on macOS, `systemd --user` on Linux**, from one service table in `marcel`. Redeploy keeps the flag file, the pre-change health check and the rollback; it becomes portable and restarts through `marcel` | A Mac runs Marcel only while its user is logged in; Linux without linger needs one sudo line, printed only when the no-sudo attempt fails |
| 5 | Claude Code pin | **A private copy of the pinned binary** in `~/marcel/claude/<version>/`, `DISABLE_AUTOUPDATER=1` in Marcel's services. The channel allowlist goes in a managed-settings **drop-in** (`managed-settings.d/50-marcel.json`): the one admin password prompt | One password prompt stays (the allowlist is admin-only in Claude Code); the user's own `claude` updates freely |
| 6 | More computers | **`marcel join <code>`** installs the helper (supervisor, runner, an outbound **link** to the hub); the hub picks: named computer → project's computer → hub's computer → an online one with room | One more contract (the link) and a local stand-in for the hub on the second computer |
| 7 | Migration | **`marcel migrate`** on the NUC: backup, stop the container, native hub on the same `~/marcel/data`, same tunnel, health check, container back if unhealthy | Docker files stay in the tree until the migration has run |

Rejected, one line each:

- Cloudflare quick tunnels: the address changes on every restart, so the phone loses its hub after a reboot.
- A Happy-style relay the owner hosts for all traffic: the owner would run what every colleague depends on, and the
  app would need its own encryption layer. Kept as the fallback if SP12 fails.
- iroh / WebRTC: not HTTP; cloud quests cannot reach it; both ends need a bridge.
- Plain tailnet with the Tailscale app on each phone: cloud quests cannot report, and every phone needs a VPN app.
- Merging hub and runner: the second computer would carry the whole hub.
- Development-channels flag in a terminal session to avoid the password: takes the brain out of Claude's supervisor
  (no `claude agents`, no respawn) and is documented "for testing" (ADR-001 option B stays the fallback).

## One person, many buddies

**Decision.** One install belongs to one person, the Claude account holder. A **buddy** is today's *agent*: a Pixel
Zoo avatar with its own long-running brain, chat, memory and look. The user can have several. Each paired phone
chooses which buddy it shows and can switch with a tap; there are no per-person logins, roles or permissions.

- **Retired:** `users` beyond the one row, `role`, the `/users…` routes, `x-owner-only` and `require_owner`, pairing
  codes for another person, per-person isolation (S-17.2). A phone sees every buddy.
- **Kept:** a brain per agent (S-17.7), memory per agent (S-17.4), avatars per agent (F19), device tokens, Face ID.
- **Pushes** go to the phones showing that buddy; Marcel-wide notices (redeploy, backup, usage, logged out) go to
  every phone.
- **Existing data:** each family member's agent becomes a buddy; their phones stay paired, set to show that buddy.

**Buddies talk with Claude Code's cross-session messages.** Brains are named sessions on one computer, so
`SendMessage` goes over a local socket and never through anyone's server. Brains set
`crossSessionInbound: accept`; workers and side threads have `SendMessage` and `ListAgents` denied, so a quest
cannot steer a brain (the reason for ADR-003). Claude Code fires no hook on an inbound message, so Marcel adds one
thing: a plugin `PostToolUse` hook on the sender's `SendMessage` tells the hub, which shows "Marcel → Pip: …" in
both buddies' chats. SP13 checks the hook and the deny rules before S-02.16 and S-22.13 build on them.

Trade-off: anyone holding a paired phone can open any buddy, the owner's own included. A per-phone "stay on this
buddy" lock is an open question for the owner, not built.

## Consequences

- **Supersedes ADR-006** (family on the owner's login) and **changes ADR-003** for brains only (workers and side
  threads still refuse), once Accepted.
- ADR-001 stands: the channel allowlist is still a managed setting; it moves to a drop-in file and stops setting
  `DISABLE_AUTOUPDATER` for everyone.
- ADR-005 stands for the owner's install (`shbunder/marcel`). A colleague's install follows the published `main`,
  updates with `marcel update` through the same flag and rollback, and never pushes a revert.
- Contracts change three times, lead only: S-02.13 (one person, buddies on phones, relay push), S-02.16 (buddy
  messages, after SP13) and S-02.14 (computers and the link).
- `marcel-bot.com` stops being a constant: the hub's `public_url` setting names the address everywhere.
- New lane `relay/` (the push relay, TypeScript on Cloudflare Workers); the lead adds it to the lanes table in
  `CLAUDE.md` when this ADR is accepted.
- B-27 and B-37…B-40 in the functional spec change; B-47…B-52 are added.
- Docs ship with each story: `docs/install.md` for colleagues, `docs/operating.md` rewritten for the native install.
- Open for the owner: see `plan/05-install-anywhere.md` and the F22 feature page, "Open decisions".
