# Runner and Claude Code's own features (S-03.10)

- **`--setting-sources project,local` does not cover MCP servers in `~/.claude.json`.** It leaves out the owner's
  `~/.claude/settings.json` (allow rules, hooks, `env`, user-enabled plugins) and `~/.claude/CLAUDE.md`, user skills
  and agents. But user-scope MCP servers live in `~/.claude.json`, which Claude Code always reads (docs:
  agent-sdk/claude-code-features § what settingSources does not control). So the brain and workers still see the
  owner's own MCP servers. Their tools still need permission, or pass auto mode's check. `--strict-mcp-config`
  would drop them, but check first that it keeps the marcel plugin's own MCP servers; nobody has tried it yet.
  To see what a session gets: attach, run `/status` ("Setting sources") and `/mcp`.
