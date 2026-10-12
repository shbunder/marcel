# mock-hub — the app's stand-in for the hub

Lets the iOS app (and the avatar) be built before the real hub exists. It serves every operation in
[`contracts/app-api.yaml`](../contracts/app-api.yaml) under `/api`, and `GET /api/ws` speaks
[`contracts/events.schema.json`](../contracts/events.schema.json). It reads the contract at start-up and
answers with the contract's own examples; it invents no data.

```bash
make mock-hub                 # http://127.0.0.1:7499/api  (MOCK_DELAY=0 for an instant scenario)
mock-hub/scripts/walk.sh      # walks the whole scenario with curl and a WebSocket listener
make -C mock-hub check        # the lane gate: ruff, pyright, pytest, coverage >= 90 %
```

Point the app's server URL at `http://127.0.0.1:7499/api`.

## What it does

- **Any device token works**, but a request with no `Authorization: Bearer ...` gets a 401, as the real
  hub would send. `POST /api/pair` accepts any code. `/api/health` and `/api/pair` need no token.
- **Bodies are checked against the contract.** A request body that does not match gets a 400 with a
  readable `message`, so a wrong request from the app shows up here.
- **State lives in memory.** A restart resets it. Nothing is stored on disk.
- **Unknown ids** (`/tasks/x`, `/agents/x`, ...) are 404 in the `Error` shape.
- Steering, stopping, archiving and handing back a task answer with the task as it stands. The mock
  does not act on them. Schedules, memory, devices and usage return the contract's examples as they
  are, and writes are not remembered.

## The scenario

`POST /api/conversations/cnv_main_marcel/messages` starts one scripted run. Each step is a WebSocket
event, `MOCK_DELAY` seconds apart (default 1):

| Step | Events |
|---|---|
| your message | `message.created` (user) |
| Marcel starts | `agent.state thinking`, `task.created`, `message.created` (milestone `started`) |
| it works | `task.event state`, `task.updated working`, `agent.state working(1)`, then `task.event` `text`, `tool_call`, `diff` |
| it asks | `task.event permission`, `approval.created`, `message.created` (approval card), `task.updated needs_you`, `agent.state needs_you` |
| you answer `POST /api/approvals/{id}` | `approval.resolved` |
| approved | `task.updated working`, `agent.state working(1)`, `task.event progress` and `artifact`, `artifact.created` (PR), `task.updated done`, `message.created` (milestone `done`), `agent.state done`, five steps later `agent.state idle` |
| denied | `task.updated stopped`, `message.created` (milestone `stopped`), `agent.state done`, then `idle` |

The run waits for your answer with no timeout. A message sent while a run is going is stored and shown,
and starts nothing new. Messages in side threads start no run.

`GET /api/ws?since=<id>` replays what you missed after `hello`. A `since` newer than anything the mock
holds (for example after a restart) gets `resync.required`. A missing token, or a `since` that is not
a number, closes the socket before it opens (the client sees an HTTP 403).

## When something is down

There is nothing to be down: no runner, no database. If the mock itself is not running, the app cannot
connect and shows its own offline state. Start it with `make mock-hub`.

## Contract notes

- Task events carry the shapes in `contracts/transcript.schema.json`; a test validates every event the
  mock logs against `TaskEvent`, so a shape the contract does not allow fails.
- A rejected WebSocket answers 403, which the contract lists for `/ws`.
