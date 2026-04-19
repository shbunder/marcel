# ISSUE-6b02d0: Containerize dev on :7421 with unified flag-file restart

**Status:** Open
**Created:** 2026-04-19
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, ops, self-modification

## Capture

**Original request:** Run the dev server as a Docker container on port 7421 instead of on the host via `make serve`, so dev and prod share one runtime shape and the 29-hour "rogue host uvicorn on 7420" class of outage becomes impossible.

## Motivation

We just paid for a "works in dev, breaks in prod" bug (ISSUE-efbaaa: discover() ordering) where dev only worked because `make serve` is a long-running process that had `discover()` triggered by prior interactive use. A cold-started dev container would have caught it. Separately, the prior outage root cause was an orphan host-side `uv run uvicorn ... --port 7420` left running from an old dev session — impossible if dev is containerized and the host never binds ports directly.

## Scope — option (a) unified restart path

Per the self-modification rule, the restart mechanism is a safety-critical boundary and should be a single code path. Today dev uses `os.execv` in-place ([src/marcel_core/main.py:83](../../src/marcel_core/main.py#L83)) while prod uses the flag-file + host-side `marcel-redeploy.path` systemd unit. That divergence is the exact shape of bug we want to stop paying for.

The fix:
1. **`docker-compose.dev.yml`** overlay: same image as prod, binds `:7421` (from `MARCEL_DEV_PORT`), bind-mounts `./src` for live code edits, `command: uvicorn ... --reload`. Reuses the existing `Dockerfile`. Kept separate from `docker-compose.yml` so `docker compose up` still means "prod".
2. **Unified flag-file restart**: `redeploy.sh` takes `--env dev|prod` (default `prod`). A second systemd path unit `marcel-dev-redeploy.path` watches a second flag file (e.g. `restart_requested.dev`) and triggers `redeploy.sh --env dev` which rebuilds + recreates the dev container.
3. **`request_restart()` becomes env-aware**: reads `MARCEL_ENV` (set by each compose file) and writes the matching flag file. One call site, one mechanism, two flag paths.
4. **Delete the `os.execv` dev carve-out** in [src/marcel_core/main.py:83](../../src/marcel_core/main.py#L83) and update the dev-mode docstring. The `_restart_watcher` loop stays — it still reads the flag and writes the result — but the "outside Docker: exec-replace" branch goes away, because dev is now always Docker.
5. **Update [.claude/rules/self-modification.md](../../.claude/rules/self-modification.md)** to reflect that there is no longer a dev exception: one restart path, same mechanism in both environments.
6. **`make serve`** is either deleted or rewritten to `docker compose -f docker-compose.dev.yml up --build`. Prefer rewrite so muscle memory still works.

## Non-scope

- Telegram routing is unaffected. The cloudflared tunnel on the NUC points at `localhost:7420` (prod), stays there, and dev never receives Telegram webhooks unless the operator temporarily re-points it with an ad-hoc `cloudflared tunnel --url http://localhost:7421`. No code changes for this — documented in [docs/channels/telegram.md](../../docs/channels/telegram.md) as a manual op.
- `MARCEL_PUBLIC_URL` is a prod-only setting (Mini App buttons) and stays unset in dev.
- Host-level port conflict detection (ISSUE-efbaaa follow-up option) is still worth doing but is a separate issue — this one supersedes it for the "rogue host uvicorn" case specifically, but a pre-flight `ss -lntp` check in `redeploy.sh` would still catch cases where an operator manually binds the port.

**Follow-up Q&A:**
- Q: Dev-container on the same port as prod? — A: No, dev on 7421 (unchanged), prod on 7420.
- Q: Option (a) unified restart vs (b) punt on dev self-mod? — A: (a). A self-adapting agent should not have two restart mechanisms — that's the shape of bug we keep paying for.
- Q: Does Telegram need rewiring? — A: No. Cloudflared tunnel on the NUC terminates at `localhost:7420`, unaffected by dev.

**Resolved intent:** Collapse the dev/prod runtime-shape divergence. Dev becomes a Docker container on :7421 running the same image as prod, bind-mounting `./src` for live `--reload`. The `os.execv` dev-only self-mod branch in `main.py` is deleted; both environments use the same flag-file mechanism — `request_restart()` reads `MARCEL_ENV` and writes to `restart_requested.{env}`, each watched by its own host-side systemd path unit. Cost: one extra systemd unit pair to maintain. Benefit: one restart path, exercised identically in dev and prod, so dev actually tests the mechanism that has to work in prod — closing the class of bug ISSUE-efbaaa was a member of.

## Description

### Current shape (divergent)

| | Dev (`make serve`) | Prod (Docker) |
|---|---|---|
| Process | Host-native `uv run uvicorn --reload` | `uvicorn` inside container |
| Port | 7421 | 7420 |
| Self-mod restart | `os.execv` in-place | Flag file → `marcel-redeploy.path` → `redeploy.sh` (with rollback) |
| Rollback on failure | No | Yes |
| Code bind-mount | N/A (runs from host) | No (built-in) |

### Target shape (unified)

| | Dev container | Prod container |
|---|---|---|
| Process | `uvicorn --reload` in container | `uvicorn` in container |
| Port | 7421 | 7420 |
| Self-mod restart | Flag file → `marcel-dev-redeploy.path` → `redeploy.sh --env dev` | Flag file → `marcel-redeploy.path` → `redeploy.sh --env prod` |
| Rollback on failure | Yes (same mechanism as prod) | Yes |
| Code bind-mount | Yes (`./src:/app/src`) for live reload | No |

### Why this shape

- **One restart path.** The flag-file + systemd-path-unit mechanism is Marcel's self-modification safety boundary. Having a second mechanism (`os.execv`) that is never exercised in prod means the one that has to work in prod is only tested in prod. The ISSUE-efbaaa cold-start bug slipped through for the same reason: dev's long-running state masked a cold-start regression.
- **Dev-prod parity under self-mod.** After this, `request_restart()` from a dev conversation goes through the full rebuild-and-recreate cycle, catching `pyproject.toml` / `Dockerfile` / `docker-compose.*` divergences before they hit prod.
- **Eliminates the host-bound uvicorn footgun.** With no host process binding `:7420` or `:7421`, a stale `uv run uvicorn` from a past dev session can't silently block the container anymore — the container claims the port at compose-up time.

## Tasks

- [✓] Add `MARCEL_ENV: Literal["dev", "prod"] = "prod"` to [src/marcel_core/config.py](../../src/marcel_core/config.py)
- [✓] Write `docker-compose.dev.yml` — same image, `MARCEL_ENV=dev`, `MARCEL_PORT=7421`, bind-mount `./src:/app/src`, `command: uvicorn marcel_core.main:app --host 0.0.0.0 --port 7421 --reload`, healthcheck on `/health`
- [✓] Rewrite `make serve` target in the Makefile to drive the dev compose file (`docker compose -f docker-compose.dev.yml up --build`), update the help text + the dev-port comment
- [✓] Split flag file naming in [src/marcel_core/watchdog/flags.py](../../src/marcel_core/watchdog/flags.py) to `restart_requested.{env}` and `restart_result.{env}`; read/write by `settings.marcel_env`
- [✓] Update `request_restart()` to pick the right flag file based on `MARCEL_ENV`; update the docstring
- [✓] Add a regression test: `request_restart(sha)` with `MARCEL_ENV=dev` writes to `restart_requested.dev` (and NOT `restart_requested.prod`); vice versa for `prod`
- [✓] Add systemd templates: `deploy/systemd/marcel-dev-redeploy.path.tmpl` + `marcel-dev-redeploy.service.tmpl` (parameterised — or a single template rendered twice with `{env}` substitution)
- [✓] Update [scripts/setup.sh](../../scripts/setup.sh) to render + install both the prod and dev systemd unit pairs
- [✓] Update [scripts/teardown.sh](../../scripts/teardown.sh) to stop + remove both unit pairs
- [✓] Update [scripts/redeploy.sh](../../scripts/redeploy.sh) to accept `--env dev|prod` (default `prod`) and drive the corresponding compose file
- [✓] Delete the `os.execv` branch in `_restart_watcher` in [src/marcel_core/main.py](../../src/marcel_core/main.py); simplify the function — flag file + write_restart_result is the whole job
- [✓] Update `_is_docker()` call site — now always Docker, so the branch can go away; remove the helper if it has no other callers
- [✓] Update [.claude/rules/self-modification.md](../../.claude/rules/self-modification.md) — remove the "dev-mode watcher is the sole exception" carve-out; the rule is now "flag-file is the only mechanism"
- [✓] Update [docs/self-modification.md](../../docs/self-modification.md) — regenerate the ports table (dev + prod are now structurally identical), remove the dev `os.execv` section, document `redeploy.sh --env` flag
- [✓] Update [CLAUDE.md](../../CLAUDE.md) — "make serve" still works but now spins the dev container; dev port is 7421 (unchanged). Ensure the "Dev and prod run on different ports" line reflects the new shape
- [✓] Update [project/plans/architecture-overview.md](../../project/plans/architecture-overview.md) — dev container row in the topology diagram if present
- [✓] Verify: `docker compose -f docker-compose.dev.yml up -d --build` yields a healthy container on :7421, `./src` edits trigger `--reload`
- [✓] Verify: `request_restart(sha)` from inside the dev container triggers `marcel-dev-redeploy.path`, `redeploy.sh --env dev` rebuilds, container comes back healthy
- [✓] Verify: `request_restart(sha)` from inside prod still triggers the prod path (unchanged behavior)
- [✓] Verify: `grep -rn "os.execv" src/ tests/` returns no matches
- [✓] `make check` green at ≥90% coverage

## Relationships

- Informs: ISSUE-efbaaa (the class of bug this prevents — dev/prod divergence hiding a cold-start regression until prod paid the cost)
- Related: ISSUE-020 (dockerize self-restart — established the prod flag-file mechanism this issue extends)
- Touches rule: [.claude/rules/self-modification.md](../../.claude/rules/self-modification.md) — the dev-exception carve-out is removed by this issue

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

### 2026-04-19 — unified dev/prod restart path via containerized dev

**Config & flag files**
- `src/marcel_core/config.py`: added `marcel_env: Literal['dev', 'prod'] = 'prod'`.
- `src/marcel_core/watchdog/flags.py`: env-scoped every path (`restart_requested.{env}`, `restart_result.{env}`) via an `_env()` helper that reads `MARCEL_ENV` from `os.environ` (keeping the watchdog layer dependency-light — no `settings` import). Unknown `MARCEL_ENV` values fall back to `prod`.

**Containers**
- `docker-compose.dev.yml` (new): same `Dockerfile`, `network_mode: host`, `MARCEL_ENV=dev`, `MARCEL_PORT=${MARCEL_DEV_PORT:-7421}`, bind-mount `.:/app`, `command: uvicorn marcel_core.main:app --host 0.0.0.0 --port $MARCEL_DEV_PORT --reload`, healthcheck on `/health`. No watchdog PID 1 — uvicorn is PID 1 in dev, and `--reload` handles code changes without the rollback mechanism.
- `docker-compose.yml`: added `MARCEL_ENV=prod` to the environment block.

**systemd + scripts**
- `deploy/marcel-redeploy.path.tmpl`: `PathExists` updated to `restart_requested.prod`; description tagged `(prod)`.
- `deploy/marcel-redeploy.service.tmpl`: `ExecStart` now `redeploy.sh --env prod --force`.
- `deploy/marcel-dev-redeploy.{path,service}.tmpl` (new): mirror of the prod pair, wired to `restart_requested.dev` and `redeploy.sh --env dev --force`.
- `scripts/redeploy.sh`: added `--env dev|prod` parsing (default `prod`), maps to the matching compose file, running-check and rebuild/restart both go through `$COMPOSE_FILE`.
- `scripts/setup.sh` / `scripts/teardown.sh`: install/remove both unit pairs, updated help hints.

**Delete dev carve-out**
- `src/marcel_core/main.py`: removed the `_restart_watcher` async function, the `_is_docker()` helper, the `_RESTART_POLL_INTERVAL` constant, and the `os`/`sys`/flag-reader imports. `lifespan()` no longer creates a restart task — dev uses the same host-side systemd mechanism as prod.

**Make target**
- `Makefile`: `make serve` now runs `docker compose -f docker-compose.dev.yml up -d --build`; added `make serve-logs` and `make serve-down`.

**Docs + rule**
- `.claude/rules/self-modification.md`: removed the dev-mode `os.execv` exception; rule is now "one mechanism, one flag file per env".
- `docs/self-modification.md`: regenerated topology diagram, ports table, flag-file table, systemd unit table.
- `docs/storage.md`, `docs/architecture.md`, `CLAUDE.md`, `README.md`, `project/FEATURE_WORKFLOW.md`, `scripts/setup.sh` warn, `.claude/agents/security-auditor.md`: updated references from the old unsuffixed `restart_requested` path and the "`make serve` starts host-native uvicorn" phrasing.

**Tests**
- `tests/core/test_watchdog.py`: autouse fixture now pins `MARCEL_ENV=prod` and asserts the suffixed filenames; 5 new tests cover env-aware flag writing, dev/prod isolation, unknown-env fallback, and env-scoped `write_restart_result`.
- `tests/core/test_main_lifespan.py`: dropped the `_restart_watcher` mock (function no longer exists).

**Verification**
- `make check` green — 1514 tests pass, coverage 91.96%.
- `grep -rn 'os.execv' src/ tests/` → no matches.
- `watchdog/flags.py` at 100% coverage.

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
