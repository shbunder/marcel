# Runner and Claude Code's own features (S-03.10)

- **`--setting-sources project,local` does not cover MCP servers in `~/.claude.json`.** It leaves out the owner's
  `~/.claude/settings.json` (allow rules, hooks, `env`, user-enabled plugins) and `~/.claude/CLAUDE.md`, user skills
  and agents. But user-scope MCP servers live in `~/.claude.json`, which Claude Code always reads (docs:
  agent-sdk/claude-code-features § what settingSources does not control). So the brain and workers still see the
  owner's own MCP servers. Their tools still need permission, or pass auto mode's check. `--strict-mcp-config`
  would drop them, but check first that it keeps the marcel plugin's own MCP servers; nobody has tried it yet.
  To see what a session gets: attach, run `/status` ("Setting sources") and `/mcp`.

## From the NUC install (2026-10-13, Claude Code 2.1.296)

- `blocked` in `claude agents --json` is Claude's own reading of the session's last turn, from its job state
  (`~/.claude/jobs/<short>/state.json`: `detail`, `needs`), not a permission prompt. The brain shows `blocked` when its
  last turn ended waiting on the owner ("draft awaiting your approval"). For a worker it maps to `needs_you`; for the
  brain it means nothing, and the keeper treats it as alive. It is not caused by `claude attach` and Ctrl+Z.
- A `claude --bg` session's MCP servers get the environment of the `claude` that started it (the runner), not the
  supervisor's. So `MARCEL_HARRY_TOKEN` and node 22 on `PATH` must be in `marcel-runner.service`; the live runner in
  `runner/tests/live/` sets the same `PATH`.
- A stopped session keeps the options it was started with (`respawnFlags`, `--setting-sources` included). Any flag on
  `claude --bg --resume`, or a short id, starts a copy under a new id (the note is on stderr). The runner resumes with
  the full id and no flags (S-03.8).
