# Runner — sessions

`runner/src/marcel_runner/sessions.py` starts, lists, reads and stops Claude Code background
sessions (`claude --bg`). The fields are in `contracts/runner-api.yaml` (`SpawnRequest`, `Spawned`,
`Session`, `Error`).

| Route | What it does |
|---|---|
| `POST /sessions` | Starts a session in `cwd`. Answers `201` with `Spawned`. |
| `GET /sessions[?all=false]` | Lists background sessions, newest first. `all=false` leaves out finished ones. |
| `GET /sessions/{id}` | One session. The full id or the 8-digit short id both work. |
| `POST /sessions/{id}/stop` | `claude stop <short-id>`. Answers the stopped session. |

A `repo` spawn, resume and fork: see [workspaces.md](workspaces.md).

## How a spawn runs

1. The body is checked. A bad body is `400 bad_request` with the reason. `cwd` must be an absolute
   path to an existing folder. A `name` or `model` that starts with `-` is refused, so claude can
   never read it as a flag.
2. The supervisor is checked again (`claude daemon status`, its exit code and cgroup), even if it was
   fine when the runner started. If not: `422 no_supervisor`, with the reason.
3. **The worker cap.** A `worker` spawn counts the running workers this runner started. At the cap
   (`MARCEL_WORKER_CAP`, default 4) it answers `409 cap_reached` and starts nothing. Spawns that are
   still starting count too, so two at once cannot both slip under the cap. `brain`, `side` and
   `adopted` sessions never count. If the runner cannot list sessions, the worker spawn is refused
   (`422 no_supervisor`), never let through.
4. A worker spawn that passes the cap asks the usage probe for a fresh reading, on a background
   thread. A slow or failed read never delays or fails the spawn.
5. It runs, as an argument list (never a shell string), in `cwd`, with `claude` by absolute path:

   ```
   claude --bg --setting-sources project,local --name <name> --model <model> --permission-mode <mode>
     [--settings <runner>/src/marcel_runner/role_settings/worker.json]   (a worker only)
     --channels plugin:marcel@marcel-local
     --allowedTools mcp__plugin_marcel_marcel__reply mcp__plugin_marcel_marcel__report
     --debug-file <state>/debug/<name>-<token>.log -- "<prompt>"
   ```

   `brain` and `side` sessions also get Marcel's brain tools and Harry's `harry_find_tools` and
   `harry_mark_done`, by name, after `report` (`sessions.BRAIN_TOOLS`, the list in
   `contracts/runner-api.yaml`); workers never do.
   `--setting-sources project,local` leaves the owner's own `~/.claude/settings.json` out, so a session never
   inherits the owner's allow rules, hooks or `~/.claude/CLAUDE.md`. Allow lists merge across settings files and no
   file can remove another's allow, so leaving the user file out is the only way (S-03.10). MCP servers in
   `~/.claude.json` still load (see `project/lessons/runner-built-ins.md`).
   A worker also gets `--settings role_settings/worker.json`: the five restart commands denied (as the brain's own
   settings deny them) and cross-session messages refused. The brain and side threads need none: the brain
   workspace's `.claude/settings.json` is their project settings. Claude saves `--settings` with the session, so a
   resume keeps it (`docs/runner/workspaces.md`).
   Before every spawn the runner checks that Claude Code is the version in `runner/claude-version`; another one
   answers `422 claude_version` and starts nothing (`docs/runner/built-ins.md`).
   `--channels …` is left out with `channel: false`. The prompt goes after `--`, because
   `--channels` and `--allowedTools` take several values and would swallow it (SP1). The exact lines
   are pinned in `runner/tests/test_sessions_golden/`.
6. The environment is the runner's own, minus every `CLAUDE*` variable (a parent Claude session's
   marker turns transcript saving off, SP2) and every `ANTHROPIC_*` variable (tokens come from the
   subscription only). `HOME` is set to the home the runner reads Claude's files from. The health
   check and the usage probe use the same scrub (`marcel_runner/claude_env.py`).
7. It reads `backgrounded · <short-id> · <name>`, then finds the full id in `claude agents --json --all`.
8. It writes the session to the state file (below).
9. With the channel on, it waits up to 30 s for the debug log to say
   `MCP server "plugin:marcel:marcel": Channel notifications registered`. If the log says `skipped`
   instead, or says nothing in time, the runner stops the session and the spawn fails. If
   `claude stop` fails too, the error says the runner could not stop it, `detail` ends with
   `claude stop said: …`, and the session keeps counting toward the cap.

### When a spawn fails half-way

Claude may have started a session even though the spawn cannot finish: the runner could not read
`backgrounded · …`, `claude --bg` timed out, or the short id never shows up in `agents --json`. The
runner then records what it can find (by short id; without one, by the name it gave, started since
the spawn began, and not already known), so it counts toward the cap, and stops it. The error says
whether the stop worked. A session older than the spawn is never touched.

## Errors a spawn can give

| Code | Status | When |
|---|---|---|
| `bad_request` | 400 | The body is wrong; the message says what. |
| `cap_reached` | 409 | The worker cap is reached. Nothing was started. |
| `claude_timeout`, `claude_unavailable` | 503 | `claude` did not answer in time, or could not be run. |
| `no_supervisor` | 422 | Claude's supervisor is down, in the runner's cgroup, or claude is missing; or the workers cannot be counted. |
| `not_trusted` | 422 | The workspace was never trusted. `detail` is Claude's own line. |
| `exited` | 422 | Claude refused to start, or printed something the runner cannot read. `detail` is what it said. |
| `channel_skipped` | 422 | The channel was skipped. The session was stopped. `detail` is the debug line. |
| `channel_timeout` | 422 | No channel line within 30 s. The session was stopped. |
| `not_supported` | 501 | A `repo` spawn on a server started without workspaces (tests only). |

`list`, `get` and `stop` answer `404 not_found` for an unknown id, `503 roster_unavailable` when
Claude cannot list its sessions, and `stop` answers `502 stop_failed` with Claude's words.

## What a session shows

Each row of `claude agents --json --all` with `kind: background` becomes a `Session`:
`sessionId` → `id`, `id` → `short_id`, `waitingFor` → `waiting_for`, `startedAt` (ms) → `started_at`.

- `role` is there only for sessions this runner started, also after a restart.
- `needs`, `detail` and `result` are read best effort from Claude Code's undocumented job file,
  `~/.claude/jobs/<short-id>/state.json`. A `failed` session shows Claude's reason in `needs`
  (for example `API error`).
- `remote_url` (only on `GET /sessions/{id}`): every `--bg` session is bridged to claude.ai Remote
  Control (SP2). The runner reads the last `remote_session_change` attachment in the session's
  transcript, `~/.claude/projects/<cwd, non-alphanumerics → ->/<id>.jsonl`. When its `url` is null or
  missing, `remote_url` is left out.

A session counts as running when it has a process (`status` is set) or is still starting
(`working` or `blocked`). `/health` `active_workers` is the number of running workers.

## The state file

Every session the runner starts is kept in `<state>/sessions.json`: `id`, `short_id`, `role`,
`name`, `task`, `permission_mode`, `model`, `cwd`, `channel`, `debug_file` (where a resume looks for the channel), `started_at`. After a runner restart
the roles come back from it, and the cap still counts the workers that are running. A state file the
runner cannot read stops it at start-up, with the file's path in the log. Each write goes to a
temp file, is flushed to disk, and then replaces the old file, so a crash never leaves half a file.
If the write fails, the runner logs it and keeps counting the session in memory.

| Variable | Default | Meaning |
|---|---|---|
| `MARCEL_RUNNER_STATE` | `$XDG_STATE_HOME/marcel/runner` (`~/.local/state/marcel/runner`) | The state file and the per-spawn debug logs. |

## Tests

`runner/tests/test_sessions.py` runs everything through the socket against the fake claude
(`runner/tests/fake_claude/`), behind a small wrapper that records each call's argv, cwd and
environment. Every control (the cap, the start-up reservation, the supervisor re-check, the
environment scrub, the state file, the cwd, the channel check, the flag-like name guard) has a test
that goes red when the control is deleted.
