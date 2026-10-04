---
id: SP1
title: A custom channel in an unattended background session
feature: F00
wave: 0
runs_on: NUC
size: S
status: Done
depends: 
touches: scratch/spikes/SP1-*
behaviours: 
---

# SP1 — A custom channel in an unattended background session

Blocks: F05, F06

**Question:** Can a `claude --bg` session load our custom two-way channel with permission relay
through `--dangerously-load-development-channels`, with no human at a terminal?

**Steps:**
1. Build the minimal fakechat-style channel from the docs, with:
   - `claude/channel` and `claude/channel/permission` capabilities;
   - a `reply` tool;
   - an HTTP endpoint that pushes a message in.
2. Start the session with `claude --bg --name sp1 --dangerously-load-development-channels server:sp1 "wait for messages"`.
3. Check whether the startup confirmation prompt blocks (`claude agents --json` → `waitingFor`).
   If it does, find out whether answering it once persists (settings key, or approval stored per entry).
4. Push a message, get a reply, then trigger a `Bash` permission prompt and answer it through the relay.
5. Repeat with the channel packaged as a plugin in a local marketplace
   (`plugin:marcel@marcel-local`).

**Pass:** messages in and out, plus a relayed approval, all work in a background session with no terminal attached.

**Fallbacks, in order:**
1. Accept the confirmation once with `claude attach` at deploy time, if it persists.
2. Use a `PermissionRequest` hook that calls the hub and blocks until the app answers (approvals),
   plus cross-session messaging (`crossSessionInbound: accept`) for steering.
3. Use the official Telegram channel as the transport for the brain only.

## Result

PASS. See [`scratch/spikes/SP1-bg-channel/RESULT.md`](../../scratch/spikes/SP1-bg-channel/RESULT.md) and `plan/03-spikes.md` § Results.
