# Self-Modification Safety

Marcel can rewrite its own code.  The safety net that makes this possible has
two layers with a clean split of responsibility:

- **`redeploy.sh`** runs on the host. It clears the env-scoped restart flag,
  rebuilds the Docker image, and recreates the container. That's all.
- **The in-container watchdog** (prod only, PID 1) is the rollback mechanism.
  It polls `/health` after restart and triggers `git revert HEAD` on failure.
  Dev has no watchdog by design (uvicorn is PID 1 for `--reload`), so dev has
  no automatic rollback.

---

## Process hierarchy

### Production (Docker + systemd)

```
Host
  ├── marcel.service              (systemd user unit — manages docker compose)
  ├── marcel-redeploy.path        (systemd — watches restart_requested.prod)
  ├── marcel-redeploy.service     (systemd — runs redeploy.sh --env prod)
  ├── marcel-dev-redeploy.path    (systemd — watches restart_requested.dev)
  └── marcel-dev-redeploy.service (systemd — runs redeploy.sh --env dev)

Docker container (marcel, prod)
  └── marcel-watchdog   (marcel_core.watchdog.main, PID 1)
        └── uvicorn     (marcel_core.main:app)

Docker container (marcel-dev)
  └── uvicorn --reload  (marcel_core.main:app, bind-mounted ./src)
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
make serve   →   docker compose -f docker-compose.dev.yml up -d --build
                 (container: marcel-dev, port 7421, uvicorn --reload, no watchdog)
```

The dev container uses the same `Dockerfile` as prod but bind-mounts `./src`
into `/app/src` and runs `uvicorn --reload` directly (no watchdog PID 1). Dev
and prod can run simultaneously on their respective ports.

---

## Setup

```bash
git clone https://github.com/shbunder/marcel
cd marcel
cp .env.example .env    # fill in ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, etc.
make setup              # installs systemd units, builds container, starts everything
```

`make setup` runs `scripts/setup.sh` which:

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

## Restart flow

Dev and prod share one mechanism. The only differences are the flag-file
suffix (resolved from `MARCEL_ENV`), the compose file the redeploy script
drives, and whether rollback is automatic (prod) or developer-driven (dev).

### Agent side (dev or prod)

When Marcel modifies its own code:

1. Commits all changes via git.
2. Calls `request_restart(pre_change_sha)` which writes the SHA to
   `~/.marcel/watchdog/restart_requested.{env}` (where `{env}` is `dev` or
   `prod`, taken from `MARCEL_ENV`).

### Host side — `redeploy.sh`

The matching host-side systemd path unit fires:

- `marcel-redeploy.path` watches `restart_requested.prod` → runs
  `redeploy.sh --env prod` (drives `docker-compose.yml`, port 7420)
- `marcel-dev-redeploy.path` watches `restart_requested.dev` → runs
  `redeploy.sh --env dev` (drives `docker-compose.dev.yml`, port 7421)

`redeploy.sh` does only three things, identical across dev and prod:

1. **Clears the `restart_requested.{env}` flag.** Idempotent — safe to run
   even if the in-container watchdog (prod) already cleared it. Without this
   step, the `PathExists=` condition on the systemd path unit would re-fire
   the redeploy on any host reboot or `systemctl restart` of the unit.
2. **Rebuilds the image**: `docker compose -f <compose-file> build`.
3. **Recreates the container**: `docker compose -f <compose-file> up -d`.

### Health-check and rollback (prod only — in-container watchdog)

The prod container runs a watchdog as PID 1 (see
[`src/marcel_core/watchdog/main.py`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/watchdog/main.py)).
The watchdog is the rollback mechanism — it has nothing to do with
`redeploy.sh`:

1. Starts `uvicorn` and polls `GET /health`.
2. On restart request (flag file seen *inside* the container): stops uvicorn,
   starts it again with the new code, polls `/health`.
3. **If healthy**: writes `"ok"` to `~/.marcel/watchdog/restart_result.prod`.
4. **If unhealthy**: triggers `git revert HEAD`, restarts uvicorn against the
   reverted code, writes `"rolled_back"` or `"rollback_failed"`. The watchdog
   does not rebuild the image — the container is already running the image
   that `redeploy.sh` built; rolling back a source-level change only requires
   an uvicorn restart because `./src` is bind-mounted into the container.

### Dev has no rollback — by design

The dev container does not run the watchdog. `uvicorn --reload` is PID 1, so
incremental source edits live-reload without the flag-file path at all.
Full self-mod restarts in dev still go through `redeploy.sh --env dev` — but
there is no health-check, no rollback, and nothing writes `restart_result.dev`.

This is deliberate: dev self-mod is exercised by an attentive developer who
can read logs and fix a broken restart manually, not by production incident
response. Introducing a redundant watchdog in dev would re-create the exact
dev/prod divergence ISSUE-6b02d0 set out to eliminate.

---

## Flag files

Flag files live at `~/.marcel/watchdog/` (or `$MARCEL_DATA_DIR/watchdog/`).

| File | Writer | Cleared by | Reader | Contents |
|------|--------|-----------|--------|----------|
| `restart_requested.prod` | prod agent (`request_restart`) | prod watchdog + `redeploy.sh --env prod` | `marcel-redeploy.path` (existence trigger) | pre-change git SHA (plain text) |
| `restart_requested.dev` | dev agent (`request_restart`) | `redeploy.sh --env dev` | `marcel-dev-redeploy.path` (existence trigger) | pre-change git SHA (plain text) |
| `restart_result.prod` | prod watchdog | (not cleared by redeploy) | prod agent (optional) | `"ok"`, `"rolled_back"`, or `"rollback_failed"` |
| `restart_result.dev` | *(nothing — dev has no in-container watchdog by design)* | — | — | — |

The `.{env}` suffix ensures a dev self-mod cannot trigger the prod rebuild
path and vice versa. All writes use an **atomic write-to-temp-then-rename**
pattern so neither side ever reads a partially-written file.

The agent API for triggering a restart:

```python
from marcel_core.watchdog.flags import request_restart
request_restart(pre_change_sha)
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MARCEL_ENV` | `prod` | `dev` or `prod`; selects the flag-file suffix in `request_restart()` |
| `MARCEL_PORT` | `7420` | Port passed to `uvicorn` (dev container overrides to `MARCEL_DEV_PORT`) |
| `MARCEL_DEV_PORT` | `7421` | Port the dev container binds |
| `MARCEL_DATA_DIR` | `~/.marcel/` | Runtime data directory |
| `MARCEL_HEALTH_TIMEOUT` | `30` | Seconds to wait for `/health` |
| `MARCEL_POLL_INTERVAL` | `2` | Seconds between health polls |

---

## Dual-port setup

| Mode | Port | How to start | Compose file | `MARCEL_ENV` |
|------|------|-------------|--------------|--------------|
| Production | 7420 | `make setup` / `make docker-up` | `docker-compose.yml` | `prod` |
| Development | 7421 | `make serve` | `docker-compose.dev.yml` | `dev` |

Both can run simultaneously. The dev port can be overridden via
`MARCEL_DEV_PORT` in `.env` or the Makefile.

---

## Systemd units

All units are user-level (`~/.config/systemd/user/`) — no root access needed.

| Unit | Type | Purpose |
|------|------|---------|
| `marcel.service` | oneshot (RemainAfterExit) | Runs `docker compose up -d --build` (prod) |
| `marcel-redeploy.path` | path | Watches `restart_requested.prod` flag file |
| `marcel-redeploy.service` | oneshot | Runs `redeploy.sh --env prod` when triggered |
| `marcel-dev-redeploy.path` | path | Watches `restart_requested.dev` flag file |
| `marcel-dev-redeploy.service` | oneshot | Runs `redeploy.sh --env dev` when triggered |

The dev container itself is started manually via `make serve` (not a systemd
unit), but self-mod restarts go through the same host-side path.

Useful commands:

```bash
systemctl --user status marcel                  # prod container status
systemctl --user restart marcel                 # manual restart (full rebuild)
systemctl --user status marcel-redeploy.path    # prod watcher active?
systemctl --user status marcel-dev-redeploy.path # dev watcher active?
journalctl --user -u marcel-redeploy -f         # follow prod redeploy logs
journalctl --user -u marcel-dev-redeploy -f     # follow dev redeploy logs
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
