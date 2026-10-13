# Runner — what Claude Code does for it

ADR-010: if Claude Code does it, Marcel uses it. What the runner takes from Claude Code, and for what:

| Need | Claude Code feature | Where |
|---|---|---|
| Start a session that outlives the runner | `claude --bg --name …` under Claude's own supervisor (`claude daemon run`, `marcel-claude-daemon.service`) | `sessions.py` |
| Know each session's state | `claude agents --json --all` (`state`, `status`, `waitingFor`), polled every 2 s | `sessions.py`, `watch.py` |
| Wake a stopped session | `claude --bg --resume <full uuid> -- "<prompt>"`: Claude keeps the options a session was started with and reuses them (2.1.296) | `resume.py` |
| A side thread with the brain's history | `--resume <uuid> --fork-session` | `fork.py` |
| Only Marcel's settings in a session | `--setting-sources project,local` (the owner's `~/.claude/settings.json` is left out) | `sessions.py` |
| A worker may not restart Marcel, nor take cross-session messages; it may fan out | `--settings role_settings/worker.json` (`permissions.deny`, `crossSessionInbound: refuse`, `permissions.allow: Workflow`, `subagentPromptCacheTtl: 1h`) | `role_settings/` |
| Replies without a permission prompt | `--allowedTools mcp__plugin_marcel_marcel__reply …` | `sessions.py` |
| Steering a running session | the marcel plugin's channel (`--channels plugin:marcel@marcel-local`); the hub talks to it | the plugin |
| One tested version | `runner/claude-version`; `/health` compares it with `claude --version`; every start answers `422 claude_version` on another one; deploy installs it with Claude Code's installer and sets `DISABLE_AUTOUPDATER=1` | `health.py`, `deploy/` |
| A trusted folder | the trust dialog, answered on a pseudo-terminal (only that dialog) | `workspaces.py` |

Not used, and why:

- **`--agent` for workers.** On Claude Code it replaces Claude Code's own system prompt entirely (docs: sub-agents §
  invoke subagents explicitly), so a worker would lose how to use its tools. The worker keeps its opening prompt and
  the `worker-protocol` skill instead.
- **`SendMessage` / `ListAgents`.** They are tools inside a session; the runner has no CLI for them, and the brain and
  workers refuse cross-session messages (ADR-003). Steering stays hub → channel.
- **A `Notification` hook.** Approvals already come through the channel's permission relay, and `blocked` with
  `waiting_for` comes from `claude agents --json`.
