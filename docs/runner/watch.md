# Runner — state watcher and `GET /events`

The runner tells the hub what every background session is doing, as it happens. The hub reads
`GET /events`, a Server-Sent Events stream, and turns each change into a task state.

Code: `runner/src/marcel_runner/watch.py`. Contract: `contracts/runner-api.yaml` (`/events`,
`RunnerEvent`, `Session`).

## Where the state comes from

Every 2 seconds the runner runs `claude agents --json --all` (about 0.12 s per call, SP2). It keeps
rows with `kind: background`, a `sessionId`, and a state the contract knows (`working`, `blocked`,
`done`, `failed`, `stopped`). Each row becomes a `Session`, exactly as `GET /sessions` shows it
(see [sessions.md](sessions.md)): `needs`, `detail` and `result` come best effort from
`~/.claude/jobs/<short-id>/state.json`.

The event also carries `remote_url` when the session's transcript has one. The watcher reads only
the lines added since the last poll, so a long transcript is read once, not every 2 seconds.

## What the hub gets

| Event | When |
|---|---|
| `session.state` | A session shows up, or any field of its `Session` changes. `previous_state` is the state before (absent for a new session). |
| `session.gone` | A session leaves `agents --json --all`. `session` is the last thing seen. |
| `runner.warning` | Three polls in a row failed. `message` says why, in plain words. |
| `runner.recovered` | The first good poll after a `runner.warning`. `message` says so, in plain words. Never sent without a warning before it. |
| `runner.claude_changed` (0.1.12) | The installed `claude --version` is not the one the last look saw. `claude_change {from, to, expected}` (as `GET /health` words them; `expected` is `runner/claude-version`), and `message`, e.g. "Claude Code changed from 2.1.296 to 2.1.301; Marcel is tested with 2.1.296." Once per change. |
| `runner.supervisor_restarted` (0.1.12) | Claude's supervisor has another pid than at the last look. `supervisor_restart {session_ids, cause?, live_workers?}`: every session seen running on any poll since the last look that is not running now (so it can also name one that ended on its own in that window), and `cause` / `live_workers` from the last `shutting down (cause=…, live_workers=…)` line written to `~/.claude/daemon.log` since the last look (left out when there is none: an older line never explains a new restart). `message`, e.g. "Claude's supervisor restarted (upgrade); 2 running sessions ended." Each ended session still gets its own `session.gone` or state change. |

The version and the supervisor's pid are looked at on a poll at most every 30 s (`health.ClaudeProbe`,
`PROBE_EVERY`), so the 2-second poll runs no extra process most of the time; each of the look's two
commands may take at most 3 s (`PROBE_TIMEOUT`), so a stuck `claude` holds the watch only briefly. The first look after a runner
start only records them; a look where claude cannot be run or no supervisor answers changes nothing.

A poll that finds nothing new sends nothing. The runner never sends the same session twice in a
row unchanged.

The states the hub needs to tell apart are passed through as Claude Code reports them (SP2, SP4):

| The session | `state` | Other fields |
|---|---|---|
| Waits for an approval | `blocked` | `waiting_for: permission prompt`, `needs: approve Bash: …` |
| Hit the usage limit | `blocked` | no `waiting_for`; `needs: usage limit reached — check plan` |
| Asked a question at the end of its turn | `blocked` | no `waiting_for`; `status: idle` |
| Failed | `failed` | `needs` holds the reason, e.g. `API error` |
| Finished its turn | `done` | `result` holds a one-line summary |

The hub decides what each one means for the task (`contracts/task-states.md`).

## Ids and reconnecting

Each message has `id:` = the event's `seq` and `data:` = the `RunnerEvent` as JSON. Ids go up by
one per event. They start at the runner's start time in milliseconds, so an id from before a runner
restart is always smaller than every id of the new run, and is never mistaken for one of them.

The runner keeps the last 1000 events in memory, and nothing else grows with time: the view holds
one entry per listed session.

On every connect the client gets, in this order:

1. **The events it missed**, when its `Last-Event-ID` is one the runner can resume from (one it still
   has, or the newest id). Each event once, nothing missed. With no id, an id older than the 1000
   kept, one from before a restart, or one it cannot read, this part is empty.
2. **The fresh view, always:** one `session.state` per known session, all carrying the newest id and
   no `previous_state`.
3. Live events.

The fresh view comes last on purpose. A hub that marked its tasks `unknown` during a short outage
gets every session's current state back, even when nothing changed meanwhile. Sent first, it would
be followed by older changes and make a task jump back (e.g. `done` → `working` → `done`). The fresh
view repeats states the client may already have; the hub ignores a state it already has.

A client that falls more than 1000 events behind while connected is disconnected. It reconnects
and gets the fresh view.

A quiet stream gets a comment line (`: still here`) every 15 seconds, so the runner notices a client
that left.

## When something goes wrong

| What | What the runner does | What you see |
|---|---|---|
| `claude agents` fails once or twice (e.g. the supervisor is restarting) | Logs one line, keeps polling. | Nothing in the app. |
| It fails three polls in a row | Sends one `runner.warning`, keeps polling. | "The runner has not been able to see Claude's sessions for 3 tries in a row. … Task states may be out of date until it can; it keeps trying." |
| It answers again | Sends one `runner.recovered` ("The runner can see Claude's sessions again, …") and what changed meanwhile. | Sessions the restart stopped show as `stopped`, `done` or `gone`. |
| A job file (`~/.claude/jobs/<id>/state.json`) is missing or not JSON | Leaves `needs`, `detail` and `result` out. | The state is still right; the approval card has no text. |
| Anything unexpected in a poll | Counts it as a failed poll and says what it was. The watcher never stops. | As above. |

The runner's own state file (`<state>/sessions.json`, see [sessions.md](sessions.md)) is read once
at start; the watcher never reads it.
