# ISSUE-f60b09: Habitats as UDS bubbles — kernel-side mechanism (Phase 1)

**Status:** Open
**Created:** 2026-04-22
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, isolation, architecture

## Capture

**Original request (user):** "I prefer the habitats to be as isolated as possible, being different bubbles marcel consults / consumes. A single pattern. Clear and simple with easy setup to handle dependencies. Make a clear issue and proceed."

**Follow-up discussion:** Assistant evaluated four IPC patterns (HTTP/gRPC, stdio JSON-RPC, Unix domain sockets, message queue) against Marcel's constraints (single-family NUC, zero-ops, response-latency-sensitive, habitats authored by trusted zoo keeper). User picked **UDS** after a critical walkthrough of pros/cons and architecture.

**Resolved intent:** Adopt Unix domain socket sidecars as the **single transport** for every python-code habitat (integrations, channels, jobs). Each such habitat becomes its own OS process with its own venv, listening on a socket file under `~/.marcel/sockets/`. The kernel connects as a client for each handler invocation. Markdown-only habitats (skills, agents) stay in-process because they're data, not code — this is a natural boundary, not a pattern exception.

This issue ships **Phase 1**: the kernel-side mechanism. A single test-fixture habitat exercises the mechanism end-to-end. No zoo habitats migrate yet. Follow-up issues handle the migration and the eventual removal of the in-process path.

## Description

### The single pattern

One python habitat = one OS process = one `.venv` = one UDS socket.

```
marcel-core (single kernel process)
  │
  ├── proxy("docker.list")   ──→  ~/.marcel/sockets/docker.sock   ──→  docker habitat (.venv: requires.packages)
  ├── proxy("icloud.events") ──→  ~/.marcel/sockets/icloud.sock   ──→  icloud habitat (.venv: caldav, vobject, ...)
  ├── proxy("news.fetch")    ──→  ~/.marcel/sockets/news.sock     ──→  news habitat (.venv: feedparser, ...)
  └── proxy("bank.balance")  ──→  ~/.marcel/sockets/banking.sock  ──→  banking habitat (.venv: ...)
```

The kernel does not import habitat code, does not share a Python heap with habitat code, and does not install habitat deps into its own venv. Each habitat is a bubble Marcel consults by opening its socket.

### Why UDS over stdio / HTTP / message queue

Decided in-conversation; captured here for future readers:

- **Stdio**: parent-owns-child semantics are tighter than the "kernel consults habitat" mental model the user wants. Serialised calls per pipe. Debugging via captured stderr only. Good for MCP-shaped tools; worse fit here.
- **HTTP / gRPC over localhost**: needs port management, auth hygiene even on 127.0.0.1, a web server per habitat. Marcel is already an HTTP server; stacking more HTTP inside habitats collapses stack layering. Wrong scale for a home NUC.
- **Message queue / broker**: solves fan-out for asynchronous events. For synchronous call-and-await RPC it is a distributed procedure call dressed up as pub/sub — worst of both patterns. A future event bus for habitat-emitted events is a separate concern.
- **UDS (picked)**: real process + memory + dep isolation, filesystem permissions for auth (socket file mode 0600), concurrent calls via an accept loop on the habitat side, no port management, scales cleanly to "one pattern for everything."

### Protocol

**JSON-RPC 2.0 framed on UDS** with a 4-byte big-endian length prefix per message:

```
  [4-byte BE length][JSON body]
```

Request body:
```json
{"jsonrpc": "2.0", "id": 7, "method": "docker.list", "params": {"params": {...}, "user_slug": "alice"}}
```

Response body (success):
```json
{"jsonrpc": "2.0", "id": 7, "result": "container list text"}
```

Response body (error):
```json
{"jsonrpc": "2.0", "id": 7, "error": {"code": -32000, "message": "docker daemon unreachable"}}
```

One connection per call in Phase 1 (no pooling). Concurrent calls work because each `asyncio.open_unix_connection` is independent and the habitat's `start_unix_server` accepts connections in parallel. Pool + keepalive are a Phase 5 optimization if latency data shows it matters.

### Lifecycle

Kernel owns habitat lifetimes in Phase 1:

1. `lifespan()` startup: loader spawns each UDS habitat as a subprocess, records `(pid, socket_path)`, polls the socket file until it's ready (≤5 s), then registers proxy coroutines under each declared handler name.
2. Supervisor task (asyncio) polls `Popen.poll()` on each habitat every few seconds. On unclean exit: log loudly, respawn with exponential backoff (1 s → 2 s → 4 s → … capped at 60 s). Surface "habitat unavailable" errors from proxies to the operator during backoff.
3. `lifespan()` teardown: supervisor stops, each subprocess gets SIGTERM, 5 s grace, then SIGKILL.

Not using systemd-supervised habitats: ownership is simpler kernel-side. If a habitat later needs to outlive kernel restarts, that change is scoped on its own.

### Dependency management

Each habitat owns its deps. The existing `integration.yaml` schema already has a `requires.packages:` list (see [src/marcel_core/skills/integrations/__init__.py:332-344](../../src/marcel_core/skills/integrations/__init__.py#L332-L344)). UDS habitats reuse it: `make zoo-setup` creates `<habitat>/.venv` and runs `uv pip install <packages>`.

The kernel venv contains zero zoo deps. `scripts/zoo-setup.sh`'s current flat-install mode (reading `<zoo>/pyproject.toml`) is retained as a Phase 2 migration tolerance and removed in Phase 4.

**Phase 1 does not touch zoo-setup.sh** because no real habitats use `isolation: uds` yet. The fixture habitat in Phase 1 tests uses `sys.executable` (the kernel's venv) — its "dep list" is empty, so venv creation is unnecessary for the end-to-end test. Real per-habitat venv creation logic lives in Phase 2.

### Credential plumbing

Kernel decrypts credentials, passes them as part of `params.params` on the JSON-RPC call. The habitat never touches the encryption key.

Accepted exposure increase: credentials now cross a UDS boundary, visible via `strace` to a process with `ptrace` capability on the marcel user. That requires local root (or the marcel user itself), which means the threat actor already owns Marcel. Not a practical regression over today's in-process access.

A "habitat requests credential on demand" bidirectional protocol is possible but deferred — simpler first.

### What counts as a "python habitat" for this issue

Three habitat kinds contain python code and are therefore candidates for UDS:

| Habitat kind | Kernel hook | Phase 1 treatment |
|---|---|---|
| Integrations (`<zoo>/integrations/<name>/`) | `@register("name.action")` decorator → registry → `integration` tool dispatch | **Scope of Phase 1 mechanism** |
| Channels (`<zoo>/channels/<name>/`) | `register_channel(plugin)` at import; exposes FastAPI router for webhook; kernel calls `send_message` / `send_photo` | Deferred — Phase 3 (channels are bidirectional; need HTTP→UDS webhook proxy on kernel side) |
| Jobs (`<zoo>/jobs/<name>/template.yaml`) | YAML-only today — no python handler on the zoo side | Deferred — Phase 3 re-evaluates whether jobs need a sidecar at all |

Skills and agents are markdown files read into the system prompt; no python execution happens outside the kernel. They stay in-process, not because of an isolation exception but because there is no code to isolate.

Phase 1 therefore ships the mechanism for integrations. The loader fork sits inside `src/marcel_core/skills/integrations/__init__.py`. Channels/jobs adopt the same mechanism in Phase 3 with a documented, symmetric extension.

### Phased rollout

- **Phase 1 — this issue**: kernel-side UDS mechanism + fixture habitat + tests + docs. In-process integration path remains intact (no habitat uses `isolation: uds` yet, so there is no behaviour change in production).
- **Phase 2 — [follow-up issue]**: migrate existing zoo integrations (`docker`, `icloud`, `news`, `banking`) to `isolation: uds`. Extend `scripts/zoo-setup.sh` to create per-habitat venvs. Ships the dep-isolation win.
- **Phase 3 — [follow-up issue]**: extend UDS pattern to channel habitats (HTTP→UDS webhook proxy on kernel side, symmetric send methods via UDS). Migrate Telegram. Evaluate jobs.
- **Phase 4 — [follow-up issue]**: remove the in-process path for python habitats. Enforce `isolation: uds` in schema. Prune dead code in the integration loader. Single-pattern end state.

The "single pattern" commitment is Phase 4. Phases 1–3 are a disciplined rollout, not a coexistence feature. Each phase is shippable independently with `make check` green.

## Tasks

- [ ] Design JSON-RPC wire format (method / params / result / error) and document it in `docs/plugins.md` under a new "Isolation modes" section
- [ ] Implement `src/marcel_core/plugin/_uds_bridge.py` — per-habitat entry point that runs inside the habitat's venv, imports the habitat, starts a UDS server, routes JSON-RPC calls to registered handlers
- [ ] Implement the loader fork in `src/marcel_core/skills/integrations/__init__.py` — when `integration.yaml` declares `isolation: uds`, spawn the habitat subprocess, wait for the socket, register proxy coroutines under each handler name in `provides:`
- [ ] Implement `src/marcel_core/plugin/_uds_supervisor.py` — asyncio task that watches spawned habitats, respawns on crash with exponential backoff, shuts them down cleanly on kernel teardown
- [ ] Wire the supervisor into `lifespan()` in `src/marcel_core/main.py` (startup + teardown)
- [ ] Add `tests/fixtures/uds_habitat/` — a minimal test habitat (integration.yaml + `__init__.py`) that registers a simple handler
- [ ] Add `tests/core/test_uds_integrations.py` — end-to-end tests covering: successful spawn + RPC call, concurrent calls on one habitat, habitat crash + respawn, clean teardown, error propagation (handler raises), invalid-method error shape
- [ ] Update `docs/plugins.md` — new "Isolation modes" section explaining UDS as the target pattern; note that Phase 1 ships the mechanism only
- [ ] Straggler grep — terms: "in-process integration", "first-party habitats", anywhere the docs imply in-process is the permanent model, soften to "current" / "today" language
- [ ] `make check` green (coverage target: the new modules contribute ≥90 % to their own coverage; no regressions elsewhere)
- [ ] File Phase 2, Phase 3, Phase 4 follow-up issues in `open/` with clear scopes
- [ ] `/finish-issue` → merged close commit on main

## Non-scope (explicitly deferred)

- Migrating real zoo habitats (Phase 2)
- Channel / job habitat UDS support (Phase 3)
- Removing in-process path (Phase 4)
- systemd-supervised habitat lifecycle
- Event bus / pub-sub for habitat-emitted events
- Connection pooling / keepalive on UDS (evaluate after phase 3 with real latency data)
- Bidirectional UDS channel for habitat-→-kernel callbacks (e.g. on-demand credential fetch)
- WASM / container-per-habitat isolation

## Relationships

- Supersedes the implicit "in-process is forever" assumption in [src/marcel_core/skills/integrations/__init__.py](../../src/marcel_core/skills/integrations/__init__.py) (rewritten by ISSUE-792e8e Session C.2 of ISSUE-63a946)
- Related: [[ISSUE-63a946]] (marcel-zoo extraction) — this issue builds on the clean kernel/zoo split that extraction shipped
- Related: [[ISSUE-792e8e]] (zoo-docker-deps + startup summary) — this issue replaces the flat-dep model that ISSUE-792e8e wired end-to-end for the prod container

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
