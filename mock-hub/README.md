# mock-hub — the app's stand-in for the hub

Lets the iOS app (and the avatar) be built before the real hub exists. It serves every operation in
[`contracts/app-api.yaml`](../contracts/app-api.yaml) under `/api`, and `GET /api/ws` speaks
[`contracts/events.schema.json`](../contracts/events.schema.json). It reads the contract at start-up and
answers with the contract's own examples. The only things it adds are the content of the boards
the examples name but do not define (the Inbox's and Marcel dev's) and the scripted runs below.

```bash
make mock-hub                 # http://127.0.0.1:7499/api  (MOCK_DELAY=0 for an instant scenario)
MOCK_HUB_PORT=7488 make mock-hub   # another port, so two sessions on one Mac do not collide
HUB=http://127.0.0.1:7488 mock-hub/scripts/walk.sh
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
- **Unknown ids** (`/tasks/x`, `/agents/x`, `/projects/x`, ...) are 404 in the `Error` shape.
- **A bad number in the query** (`?limit=ten`, `?limit=0`, `?after=-1`, `?version=x`) is a 400 in the
  `Error` shape, never a 500. `limit` runs from 1 to 200.
- **Lists filter** by the query parameters the contract names: tasks by `agent_id`, `project_id` and
  `state`; artifacts by `task_id`, `project_id` and `kind` (newest change first).
- Steering, stopping, archiving and handing back a task answer with the task as it stands. The mock
  does not act on them. Schedules, memory, devices and usage return the contract's examples as they
  are, and writes are not remembered.

## Projects, items and boards

Three projects are there from the start, from the contract's `listProjects` example: **Energy
contracts** (`prj_01JA0A1`), **Marcel dev** (`prj_01J9ZM1`, where the contract's task and PR examples
live) and **Inbox** (`prj_inbox`, always last on the map). Every task and every artifact belongs to one of them.

- **Every project has a board**: an artifact of kind `board` whose content (JSON, `GET
  /artifacts/{id}/content`) matches `contracts/dashboard.schema.json`. It has four sections: *Waiting on
  you* (open approvals), *In flight* (threads starting, working or waiting), *Recent results* (the
  project's items with their version) and *Upcoming* (always empty here).
- **The board is rebuilt after every change** to the project's tasks, approvals or items. If its content
  changed it gets a new version (`artifact.updated`), and the project follows with `project.updated`.
  A change of counts alone sends only `project.updated`.
- **Creating a project** (`POST /projects`) makes its board too: `artifact.created` (board), then
  `project.created`. The slug comes from the name when you send none; a live project with the same slug
  is a 409 `slug_taken`.
- **Changing a project** (`PATCH`) sends `project.updated`, after a new board version when the name or
  goal changed.
- **Archiving** refuses the inbox and any project with a thread that is not done, failed or stopped
  (409). An archived project is listed with `?archived=true` and refuses moves (409 `project_archived`).
- **Moving a task** (`POST /tasks/{id}/move`) sends `task.moved`, then the old project's board and
  `project.updated`, then the new project's. The task's items move with it. Moving it to the project
  it is in already changes nothing and sends nothing.
- **Versions**: `GET /artifacts/{id}/versions` lists them newest first; `?version=n` on `/content`
  answers an older one. A seeded artifact at version 3 has versions 1 to 3.
- **A message sent from a project room** (`project_id` on the message, B-32) is stored with the tag, and
  the thread it starts is filed in that project. An unknown `project_id` is a 400.

## The scenario

`POST /api/conversations/cnv_main_marcel/messages` starts one scripted run. A message that mentions
**energy** starts the items run (flows 4-6); any other message starts the login run (flows 1-3). Each
step is a WebSocket event, `MOCK_DELAY` seconds apart (default 1). Board and project events are left out
of the tables below; the tests list every frame in order.

### The login run (flows 1-3), filed in Marcel dev

| Step | Events |
|---|---|
| your message | `message.created` (user) |
| Marcel starts | `agent.state thinking`, `task.created`, `message.created` (milestone `started`) |
| it works | `task.event state`, `task.updated working`, `agent.state working(1)`, then `task.event` `text`, `tool_call`, `diff` |
| it asks | `task.event permission`, `approval.created`, `message.created` (approval card), `task.updated needs_you`, `agent.state needs_you` |
| you answer `POST /api/approvals/{id}` | `approval.resolved` |
| approved | `task.updated working`, `agent.state working(1)`, `task.event progress` and `artifact`, `artifact.created` (PR), `message.created` (kind `artifact`: the item arrived), `task.updated done`, `message.created` (milestone `done`), `agent.state done`, five steps later `agent.state idle` |
| denied | `task.updated stopped`, `message.created` (milestone `stopped`), `agent.state done`, then `idle` |

### The items run (flows 4-6), filed in the inbox

| Step | Events |
|---|---|
| your message about energy | `message.created` (user) |
| Marcel starts | `agent.state thinking`, `task.created` (in `prj_inbox`), the inbox board and `project.updated`, `message.created` (milestone `started`) |
| it works | `task.event state`, `task.updated working`, `agent.state working(1)`, `task.event` `text`, `tool_call` |
| item v1 | `task.event artifact`, `artifact.created` (a doc, "Switching to Luminus"), `message.created` (kind `artifact`) |
| it asks | `task.event permission`, `approval.created` ("Send the switch request to Luminus"), approval card, `task.updated needs_you`, `agent.state needs_you` |
| you move it: `POST /api/tasks/{id}/move` to `prj_01JA0A1` | `task.moved`, inbox board + `project.updated`, energy board + `project.updated` |
| you answer `POST /api/approvals/{id}` | `approval.resolved`, then the energy board loses its "waiting on you" line: a new board version |
| approved | `task.updated working`, `task.event progress` and `artifact`, `artifact.updated` (the doc's v2; no second "item arrived" message), `task.updated done`, milestone `done`, `agent.state done`, then `idle` |
| denied | `task.updated stopped`, milestone `stopped`; the doc stays at v1 |

You do not have to move the task; the run goes on either way, in whichever project the task is in.

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
- A bad number in the query is a 400; the contract lists it for every operation that takes one.
