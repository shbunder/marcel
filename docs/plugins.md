# Toolkit habitats — retired

The **toolkit habitat is retired** (FEAT-260718-c232d9). Integrations are now
**[connectors](connectors.md)** — MCP servers with per-user authentication.
This page remains only as a signpost for readers following old links; the
connector page is the current reference for everything that used to live here.

What retired, concretely:

- **`toolkit/<name>/` habitats no longer exist.** The four parks that lived
  there (`news`, `docker`, `icloud`, `banking`) are now `connectors/<name>/`
  habitats in marcel-zoo, exposing native MCP tools.
- **The `toolkit(id="family.action")` dispatcher tool is gone.** The model
  calls a connector's tools directly — they are native MCP tools, resolved
  per user at capability-build time and disclosed on demand.
- **`toolkit.yaml` is replaced by `connector.yaml`** (see
  [the manifest](connectors.md#the-manifest)), including the
  `scheduled_jobs:` block, which kept its entry shape (see
  [Jobs](jobs.md#2-scheduled_jobs-in-a-connector-declarative)).
- **Skills no longer resolve `metadata.marcel-connectors` against
  toolkits.** The key names connector habitats only; a skill's aggregated
  credential requirements come from each connector's
  `auth.credential_keys`.

## The deprecation shim

`@marcel_tool` survives **one release** as an import shim, so a straggler
habitat or extension fails soft with directions instead of an `ImportError`:

- `from marcel_core.plugin import marcel_tool` still imports, but the
  decorator **does not register anything** — it emits a `DeprecationWarning`
  pointing at [Connectors](connectors.md) and returns the function unchanged.
- The extension API's `marcel.tool(name)` is likewise a deprecated no-op.
  Extensions register connectors instead, via `marcel.connector(path)` — see
  [Extensions](extensions.md).

A handler decorated through either shim is silently absent from the agent's
tool surface — the warning in the logs is the only trace. Port it.

## Migrating a toolkit to a connector

The four zoo parks all took the same route; model a migration on them.

1. **Handlers become native MCP tools.** Write a bundled server module
   (e.g. `server.py`, FastMCP) exposing either a module-level `mcp` server
   object — for a shared, credential-free server — or a `build(user_slug)`
   factory when the server must be closed over one user (per-user storage,
   per-user credentials).
   Each `@marcel_tool("news.sync")` handler becomes an `@mcp.tool` function
   named `sync`; params turn into typed function arguments instead of a
   stringly `params: dict`.
2. **`toolkit.yaml` becomes `connector.yaml`.** `requires: {credentials: …}`
   maps to `auth.credential_keys` (resolved from the user's vault);
   env / files / packages prose moves to the paired skill's `SETUP.md`.
   The `provides:` list is replaced by the optional `tools:` allowlist.
3. **`scheduled_jobs:` moves into `connector.yaml`.** The entry shape is
   field-compatible, and the scheduler materialises entries through the same
   `habitat:<name>` template and stable job id — a park keeps its job
   identity, cadence, and notify policy across the migration. The `handler:`
   ref (`news.sync`) now means *connector `news`, MCP tool `sync`*.
4. **Skill bodies call tools directly.** Replace
   `toolkit(id="news.sync", params={})` instructions with the tool's own
   name — the paired skill's `metadata.marcel-connectors` activates the
   connector's tools in the same `load_capability` step, so the model has
   them in hand when the skill's guidance arrives.
5. **Dependencies.** A park with real PyPI deps declares them in the park's
   `pyproject.toml`; `make zoo-deps` (or `make env-sync`) provisions a
   per-park dep-venv. For a `stdio` park that dep-venv's interpreter *is*
   the committed entry point — `command: [.venv/bin/python, server.py]`,
   resolved relative to the park directory.

## See also

- [Connectors](connectors.md) — the current integration habitat kind:
  manifest schema, transports, auth modes, trust model.
- [Habitats](habitats.md) — the five-kind taxonomy (skill, connector,
  subagent, channel, job).
- [Extensions](extensions.md) — registering habitats through
  `register(marcel)`.
- [Jobs](jobs.md) — `dispatch_type: tool` refs and declarative
  `scheduled_jobs:`.
