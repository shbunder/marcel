# SP6 — Hub in Docker ↔ native runner — RESULT

**Verdict: PASS**, with three traps that the default setup falls into. All three have verified fixes, which are
encoded in the snippets in this folder.

## Question

Can the hub (Docker) and the runner (native, systemd user service) talk over a unix socket? Which UID/GID mapping
works? Does the hub need `~/.claude`? Do the runner and its sessions survive `docker compose down` and a runner restart?

## What was run

- NUC: Docker 29.1.3, Compose v5.0.0, no userns-remap, systemd user manager running, user `shbunder` (1000) in
  the `docker` group. **Linger is off** (see below). Claude Code 2.1.289.
- [`runner.py`](runner.py): a stdlib HTTP server on a unix socket. It has `GET /health`, `GET /sessions`
  (`agents --json`), `POST /spawn` (runs `claude --bg` natively), `POST /stop`, and `GET /tail?session=&offset=&follow=`
  (transcript as SSE, `id:` = byte offset).
- [`hub.py`](hub.py): a stdlib client in `python:3.12-slim`, from [`docker-compose.yml`](docker-compose.yml).
- The runner and claude's supervisor ran as **transient** user units (`systemd-run --user --unit=… -p …`), with
  the same properties as the unit files here. That kept everything inside `scratch/`; nothing was written to
  `~/.config/systemd`.

```bash
systemd-run --user --unit=sp6-runner -p RuntimeDirectory=marcel-sp6 -p RuntimeDirectoryMode=0750 \
  [-p RuntimeDirectoryPreserve=yes] -E RUNNER_SOCK=/run/user/1000/marcel-sp6/runner.sock [-E PATH=~/.local/bin:…] \
  /usr/bin/python3 runner.py
systemd-run --user --unit=sp6-claude-daemon -E PATH=… ~/.local/bin/claude daemon run
HOST_RUNNER_DIR=/run/user/1000/marcel-sp6 docker compose -p sp6 up -d
docker exec sp6-hub python3 /app/hub.py health | sessions | spawn <cwd> <name> <prompt> | tail <uuid> <offset> <follow_s>
```

## Answers

### Hello-world round trip — PASS

`hub (container) → unix socket → runner → claude --bg → session` worked. The hub spawned `sp6-a` ("PONG") and
`sp6-b` (a 45 s task), listed them through `/sessions`, and streamed `sp6-b`'s whole transcript (50 entries, final
text `DONE`) through `/tail` into the container. The container had **no** `~/.claude` mounted. Resuming
`/tail` from the last `id:` returns nothing new. **The hub never needs `~/.claude/projects`**: the runner streams.

### UID/GID — socket `0660 shbunder:shbunder`, dir `0750`

| Container user | `/health` |
|---|---|
| root | ✅ (no userns-remap, so root is host root. Works, but don't.) |
| `1000:1000` (same as host user) | ✅ |
| `2000:1000` (other uid, same gid) | ✅ |
| `65534:65534 --group-add 1000` | ✅ |
| `65534:65534` | ❌ `PermissionError: [Errno 13]` |

**Group membership is enough.** The hub runs as `user: "${UID}:${GID}"` (simplest), or as its own uid with
`group_add: [<runner gid>]`. `/run/user/1000` itself is `0700`, but that doesn't matter: the bind-mount source is
resolved by dockerd. Mount the **directory**, never the socket file, because the runner recreates the socket on every start.

### Trap 1 — `RuntimeDirectory` deleted on stop → stale bind mount

Without `RuntimeDirectoryPreserve=yes`, stopping the runner deletes `/run/user/1000/marcel-sp6`. The new runner
creates a new directory, but the **running** hub container still holds the old, deleted inode:
`ls /run/marcel` is empty and `/health` fails with `FileNotFoundError` until the container is recreated.
**Fix:** `RuntimeDirectoryPreserve=yes`. With it, the hub reached the restarted runner (new pid) without a container restart. ✅

### Trap 2 — `claude` not on the unit's PATH

systemd's user PATH is `/usr/local/sbin:…:/snap/bin`, with no `~/.local/bin` (where the native `claude` lives), so
`/spawn` failed with `FileNotFoundError: 'claude'`. **Fix:** `Environment=PATH=%h/.local/bin:…` (or call
`%h/.local/bin/claude` by absolute path).

### Trap 3 — the first `claude --bg` puts claude's supervisor *inside the runner's cgroup*

With no supervisor running, the runner's `claude --bg` started one (`claude daemon run --origin transient`). The
supervisor, the pty hosts and the session all landed in `app.slice/sp6-runner.service`. A runner restart with the
default `KillMode=control-group` **killed the session process**. The conversation survived (`state: done`, no
pid, resumable), but a mid-task worker would have died.

**Fix (verified):** run claude's supervisor in **its own user unit**,
[`marcel-claude-daemon.service`](marcel-claude-daemon.service) (`ExecStart=claude daemon run`). The 2.1.289 CLI says
"Service install is disabled in this version — the daemon runs on demand", so we provide the unit ourselves.
`claude --bg` then connects to it, and sessions live in `marcel-claude-daemon.service`. The foreground daemon stays
up when idle (verified 2 min after its sessions finished). (`KillMode=process` on the runner would also stop the
killing, but it leaves orphans in the runner's cgroup. The separate unit is cleaner.)

### Survival — PASS (with the fix)

| Event, while `sp6-b` was mid-`sleep 45` | Session | Runner |
|---|---|---|
| `systemctl --user restart sp6-runner` | same pid 2926694, `working` | new pid, hub reconnects with no container restart |
| `docker compose down` | same pid, `working`, finished with `DONE` | `active` |

### Linger

`loginctl show-user shbunder -p Linger` → `no`. Without linger, user units don't start at boot and the user manager
(with the runner, claude's supervisor and every session) stops at the owner's last logout. **F14 must run
`sudo loginctl enable-linger shbunder` once.** (Not done here: it needs root and is outside this spike's scope.)

## Snippets (documented deliverable)

- [`docker-compose.yml`](docker-compose.yml): the hub with `user:`, a directory bind mount, and `RUNNER_SOCK`.
- [`marcel-runner.service`](marcel-runner.service): PATH, `RuntimeDirectory=marcel` + `Preserve=yes`, socket
  `0660`, `Requires=marcel-claude-daemon.service`.
- [`marcel-claude-daemon.service`](marcel-claude-daemon.service): claude's supervisor in its own cgroup.

## What the plan changes

- **02-architecture:**
  - The socket is `%t/marcel/runner.sock` = `/run/user/<uid>/marcel/runner.sock` on the host (not `/run/marcel`, which
    needs root), mounted into the hub at `/run/marcel`.
  - Add a third native component: `marcel-claude-daemon.service`.
- **F03 runner:**
  - Set PATH or call claude by absolute path.
  - Refuse to start (or warn loudly) if no claude supervisor is running outside its own cgroup (`claude daemon status` →
    `origin: foreground` and a pid not in the runner's cgroup), so the runner never spawns the first `--bg` itself.
  - `/tail` streams by byte offset (SSE `id:`), which F03's `Last-Event-ID` resume can use directly.
- **F14 deploy:**
  - Enable linger.
  - Install both user units.
  - Compose `user: "${UID}:${GID}"`.
  - After a Claude Code auto-update, prefer `claude respawn --all` over restarting `marcel-claude-daemon` (a restart
    stops every session). *Unverified:* whether the long-lived supervisor itself must be restarted to pick up a new version.
- **F04 hub:** no `~/.claude` mount. Drop it from the compose file entirely.
