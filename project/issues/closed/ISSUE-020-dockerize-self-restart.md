# ISSUE-020: Dockerize Marcel for Self-Restart and NUC Management

**Status:** Closed
**Created:** 2026-04-02
**Assignee:** Shaun
**Priority:** High
**Labels:** feature, infra

## Capture
**Original request:** "We should move the data to ~/.marcel (all Marcel data should be there). The re-deploy mechanism is not okay for me, it still required sudo meaning that marcel can not restart / redeploy a new version of itself. Does it make sense to go for a docker setup? I do however want marcel to still have access to the source code (here) and be able to change it. There should also be a script that allows marcel to restart itself (the docker then?!) if the redeploy fails marcel should deploy with the older version and be aware the deploy failed. All this setup should be easily done from scratch through an installation / setup logic (probable the install that comes with the CLI). Marcel should have broad access to the NUC: connect to the web, read files on the NUC, restart other containers (like the plex container). Marcel should still be able to manage the NUC. /home/shbunder should be read-write. Put /data under /home/shbunder/.marcel directly, no need for another subfolder data."

**Follow-up Q&A:**
- Docker socket mount for self-restart: confirmed acceptable
- Dev (make serve) and Prod (Docker) layers: confirmed, keep both
- Sidecars may be needed later: use docker-compose from the start
- Marcel must be able to rewrite its own source code AND regenerate the Dockerfile/compose from source
- /home/shbunder mounted read-write, rest of NUC filesystem read-only via /_host
- Runtime state goes directly in ~/.marcel/ (not ~/.marcel/data/)
- network_mode: host for full LAN/internet access
- Dev and prod run on different ports simultaneously: Docker on standard port (7420), dev (make serve) on port from .env.local
- CLI connects to prod by default; `marcel --dev` connects to the dev port

**Resolved intent:** Replace the systemd-based deployment with a Docker Compose setup so Marcel can restart and redeploy itself without sudo. Move all runtime data from the in-repo `data/` directory to `~/.marcel/`. The container gets broad NUC access: read-write to /home/shbunder (source code, repos), Docker socket for managing other containers (Plex, etc.), host networking for LAN/internet, and read-only access to the rest of the filesystem. A redeploy script with automatic rollback on health failure replaces the current watchdog restart mechanism. The install script is updated to bootstrap the full Docker-based setup from scratch.

## Description

Marcel currently runs via systemd, which requires sudo to restart — making true self-redeployment impossible. Moving to Docker solves this: Marcel controls its own container lifecycle via the Docker socket.

### Architecture

```
Docker container (marcel)
  ├── network_mode: host              → full LAN/internet access
  ├── /var/run/docker.sock mounted    → manage all containers (Plex, etc.)
  ├── /home/shbunder mounted (rw)     → self-modify source + work with repos
  ├── ~/.marcel/ mounted (rw)         → persistent runtime state + schedules
  └── / mounted as /_host (ro)        → read anything on the NUC
```

### Key design decisions
- **Docker socket mount** for self-restart and managing other containers (Plex, etc.)
- **network_mode: host** for full network access (LAN devices, webhooks, APIs)
- **Bind-mount source code read-write** so Marcel can edit its own code, Dockerfile, and compose file
- **Runtime state in ~/.marcel/** (users, conversations, memory, watchdog flags, schedules)
- **Rollback on failed deploy**: tag known-good state, build+restart, health-check, revert if unhealthy
- **Dev/Prod split**: `make serve` stays for development, Docker for production
- **Dual-port**: Docker binds to 7420 (standard), dev uses a separate port from `.env.local`. Both can run simultaneously
- **CLI `--dev` flag**: `marcel` → prod (7420), `marcel --dev` → dev port
- **docker-compose from the start** to support future sidecars

## Tasks
- [✓] ISSUE-020-a: Move runtime data from `data/` to `~/.marcel/` — update default `MARCEL_DATA_DIR`, storage layer, all references
- [✓] ISSUE-020-b: Create `Dockerfile` — Python app with uvicorn, install dependencies from pyproject.toml
- [✓] ISSUE-020-c: Create `docker-compose.yml` — volumes (source rw, ~/.marcel rw, docker.sock, /_host ro), network_mode: host, healthcheck
- [✓] ISSUE-020-d: Build redeploy script with rollback — tag known-good, build+restart via Docker socket, health-check, revert on failure, write result to ~/.marcel/watchdog/
- [✓] ISSUE-020-e: Update watchdog to work in Docker context — detect container environment, use redeploy script instead of os.execv
- [✓] ISSUE-020-f: Update `install.sh` to bootstrap Docker-based prod setup — check Docker, create ~/.marcel structure, generate compose, build and start container
- [✓] ISSUE-020-g: Keep `make serve` working for dev — ensure Makefile targets still work without Docker, uses port from .env.local
- [✓] ISSUE-020-h: Add `--dev` flag to CLI — connects to dev port instead of default 7420
- [✓] ISSUE-020-i: Update docs — self-modification.md, deployment docs, storage.md references

## Subtasks

- [✓] ISSUE-020-a: Move runtime data to ~/.marcel/
- [✓] ISSUE-020-b: Create Dockerfile
- [✓] ISSUE-020-c: Create docker-compose.yml
- [✓] ISSUE-020-d: Build redeploy script with rollback
- [✓] ISSUE-020-e: Update watchdog for Docker context
- [✓] ISSUE-020-f: Update install.sh for Docker bootstrap
- [✓] ISSUE-020-g: Ensure make serve dev workflow preserved (separate port)
- [✓] ISSUE-020-h: Add --dev flag to CLI
- [✓] ISSUE-020-i: Update documentation

## Relationships
- Related to: [[ISSUE-015-icloud-caldav-auth]] (credentials storage moves)

## Comments

## Implementation Log

### 2026-04-02 — LLM Implementation
**Action**: Full implementation of Docker-based deployment with self-restart
**Files Modified**:
- `src/marcel_core/storage/_root.py` — Changed default data root from `{repo}/data` to `~/.marcel/`, removed `_find_repo_root()`
- `src/marcel_core/watchdog/flags.py` — Changed watchdog data dir from `{repo}/data/watchdog` to `~/.marcel/watchdog`
- `src/marcel_core/main.py` — Added Docker detection (`/.dockerenv`), runs `redeploy.sh` in Docker instead of `os.execv`
- `Dockerfile` — New: Python 3.12-slim, uv, uvicorn via watchdog, healthcheck
- `docker-compose.yml` — New: host networking, Docker socket, source rw mount, ~/.marcel mount, /_host ro mount
- `redeploy.sh` — New: self-redeploy with known-good tagging, health check, and automatic rollback
- `Makefile` — Added `MARCEL_DEV_PORT` (7421), Docker targets (docker-build/up/down/logs/restart), updated `serve` to use dev port
- `install.sh` — Added `--server` flag for Docker bootstrap, `~/.marcel/` directory setup, `dev_port` config
- `src/marcel_cli/src/config.rs` — Added `dev_port` field, `effective_port()`, `parse_dev_flag()`, updated `ws_url`/`health_url` to accept dev_mode
- `src/marcel_cli/src/main.rs` — Parse `--dev` flag, pass to `app::run`
- `src/marcel_cli/src/app.rs` — Thread `dev_mode` through run/handle_key/handle_command, show mode in /status
- `src/marcel_cli/src/render.rs` — Fixed pre-existing clippy: empty lines after doc comments
- `src/marcel_cli/src/header.rs` — Fixed pre-existing clippy: useless format!
- `src/marcel_cli/src/ui.rs` — Fixed pre-existing clippy: manual strip_prefix
- `pyproject.toml` — Added E402 to ruff ignore (intentional load_dotenv before app imports)
- `docs/self-modification.md` — Rewritten for Docker-based deployment
- `docs/storage.md` — Updated data paths from `data/` to `~/.marcel/`
- `docs/architecture.md` — Updated memory path reference
- `docs/channels/telegram.md` — Updated data path reference
- `project/CLAUDE.md` — Updated user data rule paths, restart trigger docs
**Commands Run**: `make check` (format, lint, clippy, tests)
**Result**: 129 Python tests pass, Rust compiles clean, lint/clippy pass. Pre-existing pyright errors in icloud/client.py and watchdog type-ignore comments remain (not introduced by this change).
