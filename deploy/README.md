# deploy/ — running Marcel on the NUC

How to install and run it: [docs/operating.md](../docs/operating.md).

| File | What it is |
|---|---|
| `Makefile` | `install`, `deploy`, `logs`, `health`, `status`, `tunnel`, and `check` (this folder's gate) |
| `install.sh` | Installs or upgrades everything under `~/marcel`, as the owner. Safe to run again |
| `docker-compose.yml`, `hub.Dockerfile` | The hub container on `127.0.0.1:7420`. Run it through `compose.sh` |
| `compose.sh` | `docker compose` with `MARCEL_HOME`, `MARCEL_UID` and `MARCEL_GID` set |
| `health.sh` | Checks the units, the runner, the hub, and the hub's whole-path check (`GET /internal/health`: hub → runner → a connected, pinging brain); waits up to `HEALTH_WAIT` s for it |
| `backup.sh` | The nightly backup of the database, the memory, the artifacts and the hub's files (`marcel-backup.timer`); restore in docs/operating.md |
| `systemd/` | User units: Claude's supervisor, the runner, and the redeploy path and service |
| `cloudflared/` | The tunnel's ingress (`/api/*` only) and its system unit |
| `marketplace.json` | The `marcel-local` marketplace listing the deployed plugin |

Secrets live in `deploy/.env.local`, which is never committed.
