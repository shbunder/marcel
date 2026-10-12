# Connectors

A connector gives Marcel's Claude sessions tools from another service. There are two kinds:

- **An MCP server in the plugin** (`plugins/marcel/.mcp.json`). Every session on the NUC that has the
  Marcel plugin gets it: the brain, its side threads and NUC workers.
- **A claude.ai connector**, added by the owner on claude.ai. Cloud workers (claude.ai/code) get it.

A cloud worker cannot reach anything on `localhost` of the NUC, so a service that runs on the NUC needs
both if cloud workers should use it too.

## Harry

Harry runs on the NUC and serves MCP at `http://localhost:7430/mcp`. The plugin registers it as the
server `harry`:

```json
"harry": {
  "type": "http",
  "url": "http://localhost:7430/mcp",
  "headers": { "Authorization": "Bearer ${MARCEL_HARRY_TOKEN}" }
}
```

- **The token** is Harry's bearer token. It lives only in the environment, as `MARCEL_HARRY_TOKEN`, in
  `deploy/.env.local` on the NUC. Claude Code fills in `${MARCEL_HARRY_TOKEN}` when it starts the server.
  It is never written in git.
- **How it reaches the session.** The runner removes every `CLAUDE*` and `ANTHROPIC_*` variable before it
  starts `claude` (`runner/src/marcel_runner/claude_env.py`) and keeps the rest, so `MARCEL_HARRY_TOKEN`
  passes through. Give it to both `marcel-runner.service` and `marcel-claude-daemon.service` (Claude's
  supervisor starts the background sessions). Do not rename it with a `CLAUDE` or `ANTHROPIC_` prefix:
  the runner would drop it.
- **Without the token**, Claude Code warns and sends the text `${MARCEL_HARRY_TOKEN}` instead. Harry
  answers 401, the session runs without Harry's tools, and everything else still works.
- **Tool names.** Claude Code names them `mcp__plugin_marcel_harry__<tool>`, for example
  `mcp__plugin_marcel_harry__harry_find_tools`. Harry shows only a few tools at first; `harry_find_tools`
  brings in the others.
- **Who may call them without asking.** The brain runs in `auto` mode, so its calls go through Claude
  Code's own check. Harry's tools are not in the runner's `--allowedTools` list (`contracts/runner-api.yaml`);
  adding them is a contract change, asked for in `project/contract-requests/S-15.1.md`. NUC workers have
  the server but are never pre-allowed its tools, like Marcel's own brain tools.
- **Cloud workers** do not get the plugin's Harry: `localhost:7430` is the NUC, and the token never
  leaves it (a cloud session starts on claude.ai with its own environment). If the owner adds Harry as a
  claude.ai connector, cloud workers see it as `mcp__claude_ai_harry__<tool>`.
- **Check it on the NUC:** `cd plugins/marcel/tools && uv run pytest -m live tests/test_harry_live.py`
  runs one test, which starts one short turn in the brain workspace and checks that Harry is
  connected and its tools are listed. It needs the real `claude`, Harry running and the token, so it is
  never part of `make check`.

## Add another MCP server to the plugin

1. Add an entry under `mcpServers` in `plugins/marcel/.mcp.json`. Use a name that is not already in
   `.claude-plugin/plugin.json` (`marcel`, `tools`): a server with the same name replaces that one.
   - A server on the NUC over HTTP: `"type": "http"`, `"url"`, and `"headers"` for its token.
   - A local command: `"command"`, `"args"`, and `"env"`; `${CLAUDE_PLUGIN_ROOT}` is the plugin folder.
2. Put every secret in a `${VAR}` reference, never the value. Name the variable `MARCEL_<SERVICE>_TOKEN`
   and add it to `deploy/.env.local` on the NUC (see `deploy/README.md`).
3. `tests/test_plugin_mcp.py` (in `plugins/marcel/tools/`) fails on a literal secret, a header that
   names no variable, a variable the runner would drop, or a name that clashes with `plugin.json`.
4. Its tools are named `mcp__plugin_marcel_<server>__<tool>`. To let the brain call one without a
   check, it has to be added by name to the runner's list, which is a contract change (only the lead).
5. Redeploy the plugin copy on the NUC. Running sessions pick up the server when they next start.

## Add a claude.ai connector (cloud workers)

1. On claude.ai, Settings → Connectors, add the service's MCP URL. It must be reachable from the
   internet; a `localhost` address will not work.
2. Sign in or give it its token there. claude.ai keeps the token; nothing goes into this repo or the NUC.
3. New cloud workers see its tools as `mcp__claude_ai_<name>__<tool>`.
