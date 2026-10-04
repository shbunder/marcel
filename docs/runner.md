# Runner

The runner is the only process that runs `claude`. It runs natively on the NUC as a systemd user
service and talks to the hub over HTTP on a unix socket. Today it answers `GET /health`.

## Run it

```bash
cd runner && uv run python -m marcel_runner.server
```

The runner starts only if Claude's own supervisor is already running in its own unit,
`marcel-claude-daemon.service` (`claude daemon run`). If the runner started the supervisor itself,
a runner restart would kill every session.

## Settings

All settings are environment variables. None is required.

| Variable | Default | Meaning |
|---|---|---|
| `MARCEL_RUNNER_SOCK` | `$XDG_RUNTIME_DIR/marcel/runner.sock` | Where the runner listens. |
| `MARCEL_CLAUDE` | `~/.local/bin/claude` | Absolute path to `claude`. systemd's user PATH has no `~/.local/bin`. |
| `MARCEL_WORKER_CAP` | `4` | The worker cap that `/health` reports. |
| `MARCEL_RUNNER_GROUP` | unset | Group that owns the socket. Unset keeps your own group. Group membership is enough for the hub (SP6). |

The socket has mode 0660. Its directory is created with mode 0750 when missing. systemd should
create it (`RuntimeDirectory=marcel`, `RuntimeDirectoryPreserve=yes`).

## `GET /health`

Always answers 200 while the runner is up. Read `ok`. When `ok` is `false`, `reason` says why in
plain words. The fields are in `contracts/runner-api.yaml` (`Health`).

`ok` is `false` when:

- `claude` is missing, not executable, or does not answer `--version`;
- no supervisor is running (`claude daemon status` exits 1);
- `claude daemon status` hangs or fails, and the reason says so;
- the supervisor runs in the runner's own cgroup;
- the runner cannot tell where the supervisor runs (no process id in the status, or a cgroup it cannot
  read). It then says it could not tell, and never tells you to stop the supervisor.

The runner decides "running or not" from the exit code of `claude daemon status`, and reads
only the process id from its text.

Anything else on the socket gets a JSON `Error` (404 for another path, 405 for another method).

## When something is down

| What | What the runner does | What you see |
|---|---|---|
| `claude` missing | Starts anyway. | `/health` says `ok: false`, and names the path to fix. |
| No supervisor, one inside the runner's cgroup, or the runner cannot tell | Refuses to start, exit code 1. | The log says what it found, and to start or check `marcel-claude-daemon.service`. |
| Another runner holds the socket | Refuses to start. | The log says a runner is already running. |
| A dead socket file is left from a crash | Deletes it and starts. | Nothing. |
| A file that is not a socket is at the path | Refuses to start and leaves the file. | The log names the path. |
| The socket cannot get mode 0660, or `MARCEL_RUNNER_GROUP` does not exist | Refuses to start. | The log says what to fix. |

`active_workers` is `0` until sessions are tracked (a later runner story).
