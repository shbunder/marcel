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

- [✓] Design JSON-RPC wire format (method / params / result / error). Chose JSON-RPC 2.0 framed with 4-byte big-endian length prefix. Documented in `docs/plugins.md` under the new "Isolation modes" section.
- [✓] Implement `src/marcel_core/plugin/_uds_bridge.py` — per-habitat entry point. Loads the habitat via `spec_from_file_location`, starts `asyncio.start_unix_server` at mode `0600`, dispatches JSON-RPC frames to registered handlers, handles SIGTERM cleanly.
- [✓] Implement the loader fork in `src/marcel_core/skills/integrations/__init__.py`. New `_load_uds_habitat()` + `_declared_isolation()` + `_make_uds_proxy()` + `_uds_connect_with_retry()` + `_habitat_socket_path()` + `_bridge_command()`. Rejects out-of-namespace `provides:`, handler-name collisions, missing/empty `provides:`. Proxy retries transient ECONNREFUSED/ENOENT for ≤3 s to mask the supervisor's unlink-then-bind respawn window.
- [✓] Implement `src/marcel_core/plugin/_uds_supervisor.py` — `HabitatHandle` dataclass, module-level state, `spawn_habitat` / `start_supervisor` / `_check_and_respawn` / `stop_supervisor`. Exponential backoff 1→60 s. Process-group signalling so grandchildren go down with the parent. `habitat_python()` prefers `<habitat>/.venv/bin/python` with `sys.executable` fallback.
- [✓] Wire supervisor into `lifespan()` in `src/marcel_core/main.py`. `start_supervisor()` after `discover_integrations()`; `await stop_supervisor()` before shutdown-complete log.
- [✓] `tests/fixtures/uds_habitat/` — 3-handler fixture (`echo` / `add` / `boom`) exercising success, concurrency, and error shapes.
- [✓] `tests/core/test_uds_integrations.py` — 10 end-to-end subprocess-spawning tests. `tests/core/test_uds_bridge.py` — 12 in-process unit tests for bridge framing + dispatch (compensates for coverage.py's Popen blindspot: the bridge module is 0 % under pytest-cov of the parent process).
- [✓] Updated `docs/plugins.md` with a new "Isolation modes" section — two modes described, JSON-RPC wire format documented, pros/cons honest, phased-rollout narrative clear.
- [✓] Straggler grep for "in-process integration", "isolation:", `_uds_bridge`, `_uds_supervisor` across docs + `.claude` + src + README + SETUP. All live references are either in the new code or the new docs section. No stale copy.
- [✓] `make check` green: 1356 tests pass, coverage 90.46 % (back over the 90 % floor after the in-process bridge tests).
- [✓] File Phase 2, Phase 3, Phase 4 follow-up issues in `open/`:
  - [[ISSUE-14b034]] — migrate docker/icloud/news/banking to UDS + per-habitat venvs in zoo-setup
  - [[ISSUE-931b3f]] — channels (Telegram, bidirectional HTTP proxy) and any python-carrying jobs
  - [[ISSUE-807a26]] — delete the in-process path; single-pattern end state
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

### 2026-04-22 — Phase 1: kernel-side UDS mechanism

Shipped as two commits on `issue/f60b09-habitats-as-uds-bubbles`:

- **impl 1** — full Phase 1 mechanism: bridge + supervisor + loader fork + fixture + 22 new tests (10 subprocess e2e + 12 in-process bridge unit) + docs + three Phase 2/3/4 follow-up issues filed. `make check` 1356 tests, 90.46 % coverage, ruff + pyright clean.

**Design decisions captured during implementation** (for the next reader):

- **Registry in the bridge is the habitat's own `_registry`, not the kernel's.** The bridge imports `marcel_core.skills.integrations` in its subprocess — Python's module system gives it a fresh `_registry` dict per process. The habitat's `@register` calls populate the bridge-local registry; the kernel's registry only holds proxies. This is the right answer because it preserves the existing `@register` API unchanged — habitat authors don't learn a new decorator when their habitat migrates to UDS.
- **Proxy retry window is ≤3 s with exponential backoff (50 ms → 100 ms → 200 ms → 400 ms → 500 ms cap).** Long enough to cover the bridge's `unlink-then-bind` race during supervisor respawn; short enough that a genuinely-down habitat fails fast.
- **Process-group signalling (`start_new_session=True` + `os.killpg`)** so a habitat that spawns its own grandchildren (hypothetical: a habitat shelling out to `rg`) has its whole subtree torn down on kernel teardown.
- **Coverage workaround via in-process unit tests** for the bridge. `pytest-cov` doesn't trace through `Popen` boundaries; the subprocess end-to-end tests execute every bridge line, but coverage-py never sees them. Rather than wire up `coverage run --parallel` (an ergonomic headache), I added `tests/core/test_uds_bridge.py` that exercises the bridge's framing + dispatch helpers directly via an `asyncio` socketpair. Same logic paths, visible to coverage, easier to reason about.
- **`isolation:` key defaults to `inprocess` (current behaviour) in Phase 1.** Phase 4 flips the default to `uds` and removes the fork entirely. Phase 1 is a **no-op in production** because no real habitat declares `isolation: uds` yet — the mechanism is ready, awaiting Phase 2 migration.

### 2026-04-22 — verifier-driven fixup (second 🔧 impl)

Pre-close-verifier returned APPROVE with two non-blocking notes. One addressed here:

- **Socket-mode race (tightened):** `src/marcel_core/plugin/_uds_bridge.py:_serve` wraps `start_unix_server` + `chmod` inside a temporary `os.umask(0o077)` block. Closes the microsecond window where the socket file was born under the process default umask before the explicit chmod landed. Umask is restored immediately after the bind so habitat code creating files later uses its caller's umask, not ours. Defense-in-depth — the single-user-NUC threat model already makes this theoretical, but the fix is one line and the cost is zero.

The other note (three Phase 2/3/4 issue files created inside the Phase 1 impl commit rather than as standalone `📝` commits on main) is accepted as convention drift — see Lessons Learned below.

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE (with two non-blocking notes — both addressed or accepted)
- Coverage: 11/11 tasks addressed, all mapped to specific file/function references
- Shortcuts found: none
- Scope drift: one convention drift (follow-up issues in impl commit) — accepted + noted
- Stragglers: none — grep clean
- Dormancy confirmed: existing zoo habitats fall through to the unchanged `_load_external_integration` path
- Focus-area gaps identified for Phase 2 (async `_wait_for_socket`, untested robustness paths) — correctly deferred

## Lessons Learned

### What worked well

- **Phasing a substantial refactor.** The user asked for a single holistic pattern; the right response was not to try to migrate everything in one go. Phase 1 = mechanism + fixture. Phases 2/3/4 = migration, channels, teardown. Each phase ships green and independently, so a family NUC is never mid-refactor. This is how the marcel-zoo extraction (ISSUE-63a946) went too — same lesson, reaffirmed.
- **Fixture habitat validates the end-to-end contract without touching marcel-zoo.** A single `tests/fixtures/uds_habitat/` directory (integration.yaml + 3-handler `__init__.py`) exercises every branch the mechanism handles. Real zoo habitats only migrate in Phase 2, isolated from "does the kernel work."
- **JSON-RPC 2.0 as the wire format.** Zero bikeshedding about the protocol — there is a well-documented spec, MCP and LSP both use it, error codes are standard. The only implementation choice was the framing (4-byte BE length prefix), which is also a well-trodden path.
- **Compensating for coverage's subprocess blindspot with unit tests.** Could have wired up `coverage run --parallel`; didn't. The `test_uds_bridge.py` file is easier to understand than subprocess coverage plumbing, and it also runs ≈200× faster (no Popen per test).

### What to do differently

- **Write the fixture habitat first, then the loader, then the bridge.** I started with the bridge, then the supervisor, then the loader, then the fixture. The order works but the bridge had to be re-read twice when the loader contract firmed up. "Mock-first" (here: fixture-first) would have forced the contract to settle earlier.
- **Pyright's bytes-or-None return type on `_read_frame`** bit the tests five times in a row before I added the `_read_response` helper. Should have added the helper in the first pass — `_read_frame` returning `None` on EOF is a deliberate API shape, and every test that expects a payload needs a narrowing wrapper.
- **`ruff` caught two unused imports / variables** that I would have missed without the lint pass. Specifically: a leftover `last_exc` from a retry-loop iteration I simplified, and an `asyncio` import I absorbed into a helper. Note for next refactor: delete-as-you-simplify, don't trust a second pass.
- **Follow-up issues in impl commit is a convention drift.** Three Phase 2/3/4 issue files (`ISSUE-14b034`, `ISSUE-931b3f`, `ISSUE-807a26`) were created inside the Phase 1 `🔧 impl:` commit rather than as standalone `📝 created:` commits on `main` per [project/issues/GIT_CONVENTIONS.md](../GIT_CONVENTIONS.md). The files exist, are correctly linked, and describe real scope derived from Phase 1's analysis — but `git blame` on them points at a code-implementation commit, not an issue-creation one. Mitigation for next time: when a closing issue naturally spawns follow-ups, pause the impl, switch to main, run three `📝` commits, then resume. The cost is a few `git checkout` cycles; the audit-trail win is real.

### Patterns to reuse

- **`HabitatHandle` dataclass with a backoff state machine.** `(proc, socket_path, backoff_next, paused, last_restart_at)` is the minimum state for "supervise a subprocess with exponential retry." Reusable shape for any future supervisor (background jobs that need restart semantics, e.g. a future event-bus consumer process).
- **`_reset_for_tests()` on the supervisor module.** Explicit hook in the implementation file (not a test-only monkeypatch) acknowledging module-level state exists and must be reset between tests. Better to make it deliberate than to have tests `monkeypatch.setattr(..._state, ...)` against module internals. Precedent: `flags._set_data_dir` in the watchdog module.
- **Per-habitat `.venv` via uv.** Not used yet (Phase 2), but the `habitat_python()` helper picks it up automatically. `uv venv` is fast enough (≈100 ms) that per-habitat isolation doesn't kill first-boot time.
- **Proxy retry on transient connect errors, bounded by a total-time budget.** This is the correct shape wherever a client connects to a supervised server — retry the exceptions that mean "server is restarting right now," fail fast on anything else. Reusable pattern for any future IPC boundary (e.g. if a habitat ever talks to a sibling habitat over UDS).
