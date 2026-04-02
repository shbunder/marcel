# Self-Modification Safety

Marcel can rewrite its own code.  The safety net that makes this possible has
two layers: the **watchdog** (manages the `uvicorn` subprocess inside the
container) and the **redeploy script** (runs on the host, rebuilds and restarts
the Docker container with automatic rollback on failure).

---

## Process hierarchy

### Production (Docker + systemd)

```
Host
  ├── marcel.service          (systemd user unit — manages docker compose)
  ├── marcel-redeploy.path    (systemd — watches restart flag file)
  └── marcel-redeploy.service (systemd — runs redeploy.sh when triggered)

Docker container (marcel)
  └── marcel-watchdog   (marcel_core.watchdog.main, PID 1)
        └── uvicorn     (marcel_core.main:app)
```

The container runs with `network_mode: host` and has access to:

- `$HOME` (read-write) — source code and user files
- `~/.marcel/` (read-write) — runtime data, watchdog flags, schedules
- `/var/run/docker.sock` — managing other containers (Plex, Home Assistant, etc.)
- `/_host` (read-only) — full host filesystem for inspection

Self-restart is handled entirely by host-side systemd — the container does
**not** use the Docker socket to restart itself.

### Development

```
make serve   →   uvicorn --reload   (port 7421, no watchdog)
```

Dev and prod can run simultaneously on different ports.

---

## Setup

```bash
git clone https://github.com/shbunder/marcel
cd marcel
cp .env.example .env    # fill in ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, etc.
make setup              # installs systemd units, builds container, starts everything
```

`make setup` runs `deploy/setup.sh` which:

1. Checks prerequisites (docker, docker compose, systemd, docker group membership)
2. Creates `.env` from `.env.example` if missing
3. Renders systemd unit templates with correct paths
4. Installs units to `~/.config/systemd/user/`
5. Enables and starts the service + restart watcher
6. Waits for the health check to pass

To tear down: `make teardown` (stops everything, removes systemd units, preserves data).

### Prerequisites

- Docker with Compose plugin (`docker compose version`)
- Current user in the `docker` group
- systemd user session (enabled on most modern Linux distros)
- Recommended: `sudo loginctl enable-linger $USER` so Marcel runs even when you're logged out

---

## Restart flow (Docker)

When Marcel modifies its own code:

1. Commits all changes via git.
2. Calls `request_restart(pre_change_sha)` which writes the SHA to
   `~/.marcel/watchdog/restart_requested`.

The host-side systemd path unit (`marcel-redeploy.path`) detects the flag file
and triggers `marcel-redeploy.service`, which runs `redeploy.sh`:

1. Clears the `restart_requested` flag.
2. Records the current commit as known-good.
3. Runs `docker compose build` (rebuilds the image with new code).
4. Runs `docker compose up -d` (restarts the container).
5. Polls `GET http://localhost:7420/health` for up to 60 seconds.
6. **If healthy**: writes `"ok"` to `~/.marcel/watchdog/restart_result`.
7. **If unhealthy**: reverts to the known-good commit, rebuilds, restarts,
   and writes `"rolled_back"` or `"rollback_failed"`.

Because `redeploy.sh` runs on the host (not inside the container), the rollback
logic works reliably — it survives the container restart.

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
detects the flag file and uses `os.execv` to replace the running process
in-place. The PID stays the same and the Python interpreter reloads fresh from
disk. No rollback is attempted in dev mode.

---

## Flag files

Flag files live at `~/.marcel/watchdog/` (or `$MARCEL_DATA_DIR/watchdog/`).

| File | Writer | Reader | Contents |
|------|--------|--------|----------|
| `restart_requested` | agent | systemd path unit / main.py | pre-change git SHA (plain text) |
| `restart_result` | redeploy.sh / watchdog | agent | `"ok"`, `"rolled_back"`, or `"rollback_failed"` |

All writes use an **atomic write-to-temp-then-rename** pattern so neither side
ever reads a partially-written file.

The agent API for triggering a restart:

```python
from marcel_core.watchdog.flags import request_restart
request_restart(pre_change_sha)
```

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
| Production (Docker) | 7420 | `make setup` | `marcel` |
| Development | 7421 (configurable) | `make serve` | `marcel --dev` |

Both can run simultaneously. The dev port can be overridden via
`MARCEL_DEV_PORT` in the Makefile or `dev_port` in `~/.marcel/config.toml`.

---

## Systemd units

All units are user-level (`~/.config/systemd/user/`) — no root access needed.

| Unit | Type | Purpose |
|------|------|---------|
| `marcel.service` | oneshot (RemainAfterExit) | Runs `docker compose up -d --build` |
| `marcel-redeploy.path` | path | Watches `restart_requested` flag file |
| `marcel-redeploy.service` | oneshot | Runs `redeploy.sh` when triggered |

Useful commands:

```bash
systemctl --user status marcel              # container status
systemctl --user restart marcel             # manual restart (full rebuild)
systemctl --user status marcel-redeploy.path  # is the watcher active?
journalctl --user -u marcel-redeploy -f     # follow redeploy logs
```

---

## Docker setup

### docker-compose.yml

The compose file lives in the repo root and defines the `marcel` service with:

- `network_mode: host` — full LAN/internet access
- Docker socket mount — for managing other containers (Plex, etc.), **not** for self-restart
- Source code bind-mount (read-write) — Marcel can edit its own code
- `~/.marcel/` bind-mount — persistent runtime data
- `/_host` (read-only) — host filesystem for inspection

### Makefile targets

```bash
make setup             # full setup (systemd + Docker, one command)
make teardown          # stop everything, remove systemd units
make docker-build      # build image only
make docker-up         # start container only
make docker-down       # stop container
make docker-logs       # tail logs
make docker-restart    # rebuild + restart via redeploy.sh
```

---

## Off-limits for self-modification

`src/marcel_core/watchdog/main.py` **must never be modified by Marcel's
self-modification system**.  It is the process that decides whether a
self-modification was safe, and modifying it mid-flight would remove the safety
guarantee.  If a change to the watchdog itself is required, it must be done by
a human developer with a manual restart.

`redeploy.sh` and the systemd unit templates in `deploy/` are similarly
critical — they orchestrate the container restart and rollback. Changes to them
should be reviewed carefully.

The restriction is documented in CLAUDE.md under *Self-Modification Safety*.
