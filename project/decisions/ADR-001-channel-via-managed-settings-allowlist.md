---
id: ADR-001
title: The marcel channel loads through a managed-settings allowlist
status: Accepted
date: 2026-10-04
decided_by: owner
evidence: scratch/spikes/SP1-bg-channel/RESULT.md
---

# ADR-001 — The marcel channel loads through a managed-settings allowlist

## Context

Marcel's brain, side threads and NUC workers are background Claude Code sessions (`claude --bg`).
They receive the owner's messages and relay permission prompts through the marcel plugin's
channel. During the channels research preview, a custom channel is not on Anthropic's default
allowlist. The plan assumed `--dangerously-load-development-channels` would work. SP1 showed it
cannot: a background session never shows the confirmation dialog, silently skips the channel, and
the supervisor strips the flag from the session's saved options. Accepting the dialog once persists
nothing.

## Options

| | Option | Verdict |
|---|---|---|
| **A** | Install the plugin from a local marketplace (`marcel-local`) and allowlist it in `/etc/claude-code/managed-settings.json` (`channelsEnabled: true`, `allowedChannelPlugins`) | **Chosen.** Passed in `--bg` with no terminal: message in, relayed approvals, reply out in 4.5 s, survives `claude respawn` |
| B | The runner drives an interactive `claude` on a pty and accepts the dialog | Works, but leaves the supervisor (no stop, respawn or attach) and depends on matching a dialog meant for people |
| C | `PermissionRequest` hook for approvals, cross-session messages for steering | Untested; no push channel for user messages |
| D | The official Telegram channel as the brain's transport | Brain I/O would go through Telegram, not the app |

## Decision

Option A. Brain, side threads and workers start with `--channels plugin:marcel@marcel-local`, never
the development flag. The managed-settings file is installed once, by root, on the NUC (F14).

## Consequences

- The policy file applies to every `claude` on the NUC and **replaces** the default channel
  allowlist, so it re-lists `telegram` and `imessage` from `claude-plugins-official`.
- A directory-source marketplace plugin runs in place: the marketplace must point at a deployed
  copy of `plugins/marcel/`, or editing the working tree changes every live session.
- Env on the `claude --bg` command line never reaches the plugin's server. Sessions are identified
  by `CLAUDE_CODE_SESSION_ID` and mapped to their role by the hub.
- Option B stays the fallback for the brain if the policy file ever causes trouble.
