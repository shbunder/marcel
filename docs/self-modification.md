# Self-Modification Safety

Marcel can rewrite its own code.  The safety net that makes this possible has
two layers: the **watchdog** (manages the `uvicorn` subprocess inside the
container) and the **redeploy script** (rebuilds and restarts the Docker
container with automatic rollback on failure).

---

## Process hierarchy

### Production (Docker)

```
Docker container (marcel)
  └── marcel-watchdog   (marcel_core.watchdog.main, PID 1)
        └── uvicorn     (marcel_core.main:app)
```

The container runs with `network_mode: host` and has access to:

- `$HOME` (read-write) — source code and user files
- `~/.marcel/` (read-write) — runtime data, watchdog flags, schedules
- `/var/run/docker.sock` — self-restart and managing other containers
- `/_host` (read-only) — full NUC filesystem for inspection

### Development

```
make serve   →   uvicorn --reload   (port 7421, no watchdog)
```

Dev and prod can run simultaneously on different ports.

---

## Restart flow (Docker)

When Marcel modifies its own code:

1. Commits all changes via git.
2. Writes the **pre-change SHA** to the `restart_requested` flag file.

The restart watcher in `main.py` detects the flag and:

1. Clears `restart_requested`.
2. Launches `redeploy.sh --no-build` (a background process that outlives the
   container restart).

### redeploy.sh

The redeploy script (`redeploy.sh` in the repo root):

1. Tags the current commit as known-good.
2. Runs `docker compose build` (skipped with `--no-build` for code-only changes).
3. Runs `docker compose up -d` — Docker replaces the old container.
4. Polls `GET http://localhost:7420/health` for up to 60 seconds.
5. **If healthy**: writes `"ok"` to `~/.marcel/watchdog/restart_result`.
6. **If unhealthy**: reverts to the known-good commit, rebuilds, restarts,
   and writes `"rolled_back"` or `"rollback_failed"`.

### Watchdog (inside the container)

The watchdog still runs as PID 1 inside the container and provides a second
layer of safety:

1. Starts `uvicorn` and polls `/health`.
2. On restart request: stops and restarts uvicorn, rolls back via `git revert`
   if the new code fails health checks.
3. On unexpected uvicorn exit: restarts immediately.

---

## Restart flow (Development)

When running without Docker (`make serve`), the restart watcher in `main.py`
uses `os.execv` to replace the running process in-place. The PID stays the same
and the Python interpreter reloads fresh from disk. No rollback is attempted in
dev mode.

---

## Flag files

Flag files live at `~/.marcel/watchdog/` (or `$MARCEL_DATA_DIR/watchdog/`).

| File | Writer | Reader | Contents |
|------|--------|--------|----------|
| `restart_requested` | agent | watchdog / main.py | pre-change git SHA (plain text) |
| `restart_result` | watchdog / redeploy.sh | agent | `"ok"`, `"rolled_back"`, or `"rollback_failed"` |

All writes use an **atomic write-to-temp-then-rename** pattern so neither side
ever reads a partially-written file.

The agent API for triggering a restart lives in
`src/marcel_core/agent/runner.py` → `request_restart(pre_change_sha)`.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MARCEL_PORT` | `7420` | Port passed to `uvicorn` |
| `MARCEL_DATA_DIR` | `~/.marcel/` | Runtime data directory |
| `MARCEL_HEALTH_TIMEOUT` | `30` | Seconds to wait for `/health` |
| `MARCEL_POLL_INTERVAL` | `2` | Seconds between health polls |

---

## Dual-port setup

| Mode | Port | How to start | How to connect |
|------|------|-------------|----------------|
| Production (Docker) | 7420 | `docker compose up -d` | `marcel` |
| Development | 7421 (configurable) | `make serve` | `marcel --dev` |

Both can run simultaneously. The dev port can be overridden via
`MARCEL_DEV_PORT` in the Makefile or `dev_port` in `~/.marcel/config.toml`.

---

## Docker setup

### docker-compose.yml

The compose file lives in the repo root and defines the `marcel` service with:

- `network_mode: host` — full LAN/internet access
- Docker socket mount — self-restart and managing other containers (Plex, etc.)
- Source code bind-mount (read-write) — Marcel can edit its own code
- `~/.marcel/` bind-mount — persistent runtime data
- `/_host` (read-only) — NUC filesystem for inspection

### Installation

```bash
./install.sh --server     # installs CLI + bootstraps Docker server
```

Or manually:

```bash
docker compose build
docker compose up -d
docker compose logs -f marcel   # follow logs
```

### Makefile targets

```bash
make docker-build      # build image
make docker-up         # start container
make docker-down       # stop container
make docker-logs       # tail logs
make docker-restart    # rebuild + restart with rollback
```

---

## Off-limits for self-modification

`src/marcel_core/watchdog/main.py` **must never be modified by Marcel's
self-modification system**.  It is the process that decides whether a
self-modification was safe, and modifying it mid-flight would remove the safety
guarantee.  If a change to the watchdog itself is required, it must be done by
a human developer with a manual restart.

`redeploy.sh` is similarly critical — it orchestrates the container restart and
rollback. Changes to it should be reviewed carefully.

The restriction is documented in CLAUDE.md under *Self-Modification Safety*.
