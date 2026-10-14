# Runner

The runner is the only process that runs `claude`. It runs natively on the NUC as a systemd user
service and talks to the hub over HTTP on a unix socket. Today it answers `GET /health`.

Plan usage (`GET /usage`): see [runner/usage.md](runner/usage.md).

Session state changes (`GET /events`): see [runner/watch.md](runner/watch.md).

Transcripts (`GET /sessions/{id}/tail`): see [runner/transcript.md](runner/transcript.md).

Resume, fork and `repo` spawns (worktrees): see [runner/workspaces.md](runner/workspaces.md).

Cloud sessions on claude.ai/code (`/cloud/sessions`): see [runner/cloud.md](runner/cloud.md).

A brain folder per person (`POST /brains`): see [runner/brains.md](runner/brains.md).

What Claude Code itself does for the runner (and the version pin): see [runner/built-ins.md](runner/built-ins.md).

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
- the installed Claude Code is not the version in `runner/claude-version` (`claude_version_expected`):
  "Claude Code 2.1.301 is installed, but Marcel is tested with 2.1.296 …";
- no supervisor is running (`claude daemon status` exits 1);
- `claude daemon status` hangs or fails, and the reason says so;
- the supervisor runs in the runner's own cgroup;
- the runner cannot tell where the supervisor runs (no process id in the status, or a cgroup it cannot
  read). It then says it could not tell, and never tells you to stop the supervisor.

The runner decides "running or not" from the exit code of `claude daemon status`, and reads
only the process id from its text.

### `login`: is Claude Code still logged in?

`login` is `{state, since, message}` (runner-api 0.1.13). It never changes `ok` or `reason`: a redeploy
check reads `ok`, and rolling back a good merge cannot log anyone in. The hub reads `login` and tells the
owner, once per `since`.

| `state` | When | `message` |
|---|---|---|
| `ok` | `claude auth status --json` says `loggedIn: true`, and nothing below says otherwise | none |
| `lost` | `auth status` says `loggedIn: false`; or the supervisor's `~/.claude/daemon.log` says it has no token ("headless daemon cannot complete OAuth", "no token found", "re-auth required"); or `/usage` shows the pay-as-you-go cost view | "Claude Code on the NUC is logged out. Log in again: on the NUC run `claude auth login`." |
| `unknown` | `auth status` cannot be run, takes over 3 s, or prints something else, and nothing says the login is lost | "The runner could not check whether Claude Code is logged in." |

- The runner looks at most once every 30 s; a `/health` in between gets the last answer. Only `/health`
  looks: a session start never waits for `claude auth status`.
- A loss holds until there is evidence it is over: `auth status` says logged in, the supervisor logs
  that it has a token again ("token found via keychain re-check"), and `/usage` has had a good reading.
  Then it takes two clean looks in a row (about a minute) to say `ok`. Silence is not evidence: on
  2026-10-14 the supervisor logged its failure once and nothing more for two hours.
- When `auth status` says logged in again while `/usage` still shows the cost view, the runner reads
  usage at once, so the old "logged out" usage note is gone before `login` says `ok`.
- `since` is when the runner first saw the state. It is kept in `login.json` in the runner's state
  folder, so a runner restart during a loss keeps it. An unreadable `login.json` starts afresh.
- Only `loggedIn` is read from `auth status`: its email, org and ids never reach an answer.

Anything else on the socket gets a JSON `Error` (404 for another path, 405 for another method).

## When something is down

| What | What the runner does | What you see |
|---|---|---|
| Claude Code is logged out, or its login cannot be refreshed | Carries on; sessions and `/usage` fail until someone logs in. | `/health` stays `ok: true` with `login.state: lost` and what to do; the hub tells the owner. |
| `claude` missing | Starts anyway. | `/health` says `ok: false`, and names the path to fix. |
| Claude Code is not the pinned version (`runner/claude-version`) | Starts anyway and prints the line; every spawn, resume, fork and cloud start answers `422 claude_version`. Running sessions carry on. | `/health` says `ok: false` with both versions; `make -C deploy install` puts the pinned one back (`docs/operating.md`). |
| No supervisor, one inside the runner's cgroup, or the runner cannot tell | Refuses to start, exit code 1. | The log says what it found, and to start or check `marcel-claude-daemon.service`. |
| Another runner holds the socket | Refuses to start. | The log says a runner is already running. |
| A dead socket file is left from a crash | Deletes it and starts. | Nothing. |
| A file that is not a socket is at the path | Refuses to start and leaves the file. | The log names the path. |
| The socket cannot get mode 0660, or `MARCEL_RUNNER_GROUP` does not exist | Refuses to start. | The log says what to fix. |

Sessions (spawn, list, get, stop, the worker cap, and what `active_workers` counts): [runner/sessions.md](runner/sessions.md).

`claude daemon stop --keep-workers` shuts down the supervisor and leaves detached sessions running. Plain `claude daemon stop` terminates them.
