# Task states

A **task** is one thread: a Claude Code session working on one piece of work, on the NUC or in the
cloud. The hub owns its state. Every transition is in the table below. Any transition not listed
is illegal; the hub refuses it and logs why (S-04.4).

The contract test parses the table, so keep it a table: one row per transition, `from` and `to`
may list several states separated by commas, and `—` means "a new task".

The runner polls session state every 2 seconds and cloud tasks may have no channel, so the hub can
see a session jump several steps at once. The table allows those jumps (for example `starting →
done`); the hub never has to invent a state in between.

## States

| State | Meaning | Shown in the app as |
|---|---|---|
| `queued` | Accepted, but waiting: the NUC is at its worker cap (`queued_reason: cap`) or the usage guard is holding new work (`queued_reason: usage`). | Queued |
| `starting` | The runner (or `claude --cloud`) has been asked to start the session. | Starting |
| `working` | The session is running a turn, or between steps of work it drives itself. | Working |
| `needs_you` | The session waits on something only you can give: an approval, an answer, a login. | **Needs you** (pinned) |
| `done` | The last turn finished what was asked. The session can take a follow-up. | Done |
| `failed` | The session ended with an error, or reported that it could not do the work. | Failed |
| `stopped` | Stopped by you or by Marcel. Final. | Stopped |
| `silent` | No activity since `expected_by`. Not assumed finished, not assumed dead (B-23). | Silent |
| `unknown` | The hub cannot see the session: the runner is unreachable. NUC tasks only. | Unknown |

## Transitions

| from | to | trigger | who |
|---|---|---|---|
| — | starting | Spawn accepted | hub, after `tasks_spawn`, a schedule or `/marcel-adopt` |
| — | queued | Spawn refused for capacity (`cap_reached`) or held by the usage guard | hub |
| queued | starting | A worker slot frees, or usage resets | hub queue, oldest first |
| queued | stopped | Stopped before it ran | app or brain (`tasks_stop`) |
| starting | working | The runner reports `working`, or the session's channel says hello | runner, channel |
| starting | needs_you | The session asks for something before its first step (a permission, a login) | channel, runner |
| starting | failed | The session could not be started | runner |
| working, silent | needs_you | A permission request, or the runner reports `blocked` | channel, runner |
| needs_you | working | The approval is answered (app or terminal), or a steer message arrives | app, terminal, channel |
| starting, working, needs_you, silent | done | The worker reports `done`, or the runner reports `done` (a short task can finish between two polls) | channel, runner |
| working, needs_you, silent | failed | The worker reports `failed`, or the runner reports `failed` | channel, runner |
| starting, working, needs_you, silent, unknown | stopped | Stopped | app or brain (`tasks_stop`) |
| starting, working | silent | No activity past `expected_by`, including a session that never got past starting | hub watchdog |
| silent | working | Activity resumes | runner, channel |
| starting, working, needs_you, silent | unknown | The runner is unreachable (NUC tasks) | hub |
| unknown | starting, working, needs_you, done, failed, stopped | The runner is back and reports the session's real state | runner |
| done | working | A follow-up is sent to the same session (B-05) | app (steer) or brain (`tasks_send`) |
| failed | starting | Marcel retries the same task in a new session | brain |

## Who may trigger what

- **The app** stops tasks, steers them, and answers approvals. It never sets a state directly.
- **The brain** spawns, stops, sends follow-ups and retries, through its MCP tools.
- **The runner and the channel** report what the session is doing. The hub maps their reports
  onto the table above.
- **The hub's watchdog** is the only source of `silent`; the hub alone sets `unknown` and `queued`.

Every transition writes a `state` task event (`StateEventData`: `from`, `to`, `reason`) and sends
`task.updated`. Archiving (`POST /tasks/{id}/archive`, B-17) is not a state: it sets `archived_at`
on a `done`, `failed` or `stopped` task and hides it from the Activity tab.
