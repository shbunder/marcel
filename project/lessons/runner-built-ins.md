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
- `claude --agent <name>` (a subagent as the main thread) replaces Claude Code's own system prompt entirely (docs:
  sub-agents § invoke subagents explicitly), so workers do not run as a named agent (S-03.10, ADR-010).
- `--settings` is a saved option too (recorded 2026-10-13): pass it on spawn only; a flag-less resume keeps it.
- `DISABLE_AUTOUPDATER` in the units' environment covers Marcel's processes, but the `claude` binary is shared: the
  owner's own sessions update it unless the managed settings' `env` sets it. The runner's version check is what makes
  a drift loud.
- `Workflow` is the exact tool and permission name for dynamic workflows, and `Agent` for subagents (2.1.296, checked
  on the NUC): a plain `-p` session lists both; with `"deny": ["Workflow"]` the `Workflow` tool is gone from its list.
  The brain denies `Agent`, `Task` and `Workflow`; a worker allows `Workflow` (`Agent` allows are dropped in auto mode).
- **Telling the owner that Claude Code updated itself** (for the hub, 2026-10-14). Claude's supervisor logs it in
  `~/.claude/daemon.log`: `[supervisor] binary at …/claude changed (…/versions/2.1.296 → …/versions/2.1.299) —
  self-restarting`, then `shutting down (cause=upgrade, uptime=…, leases=0, live_workers=5)` and a new
  `─── daemon start ─── version=2.1.299`. `live_workers` is how many sessions it ended; the runner sees the version
  change in `claude --version` (and `/health` turns `ok:false` against `runner/claude-version`).
- **What an unpinned update cost on 2026-10-14** (the NUC, before managed settings turned updates off machine-wide).
  02:51 UTC: another `claude` process updated the shared binary to 2.1.299; the supervisor self-restarted
  (`cause=upgrade, live_workers=5`) and ended Marcel's brain and four other sessions. Until 05:24 (when
  `make -C deploy install` put 2.1.296 back) the runner refused every start (`422 claude_version`), so the keeper could
  not start a new brain, and at 05:30 the hub posted "The morning digest was not written: Marcel did not publish it within
  30 minutes. In short: 3 threads failed; 4 finished." No push reached the owner: none was configured.
