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
  hub would send. `POST /api/pair` accepts any code (see Buddies below for a code that pairs a new
  phone). `/api/health` and `/api/pair` need no token; `/api/health` says `os: macos`.
- **Bodies are checked against the contract.** A request body that does not match gets a 400 with a
  readable `message`, so a wrong request from the app shows up here.
- **State lives in memory.** A restart resets it. Nothing is stored on disk.
- **Unknown ids** (`/tasks/x`, `/agents/x`, `/projects/x`, ...) are 404 in the `Error` shape.
- **A bad number in the query** (`?limit=ten`, `?limit=0`, `?after=-1`, `?version=x`) is a 400 in the
  `Error` shape, never a 500. `limit` runs from 1 to 200.
- **Lists filter** by the query parameters the contract names: tasks by `agent_id`, `project_id` and
  `state`; artifacts by `task_id`, `project_id` and `kind` (newest change first).
- Steering, stopping, archiving and handing back a task answer with the task as it stands. The mock
  does not act on them. Schedules, memory and usage return the contract's examples as they are, and
  writes are not remembered.

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

## Buddies (app-api 0.8.0, B-49) and chat clarity (0.7)

One person (`usr_owner`, Shaun) with two buddies from the start: **Marcel** (`agt_marcel`, a rose
giraffe, professional) and **Pip** (`agt_01JA3E2`, a tangerine fox, cheerful). Each has a main
conversation (`cnv_main_marcel`, `cnv_main_pip`, where Pip has said hello) and an Inbox. The scripted
runs below are Marcel's; a message to Pip is stored and answered by nobody.

- **Every phone sees every buddy** (ADR-013). Nothing is scoped to the phone that asks: lists filter
  by the query parameters the contract names (`/conversations` and `/projects` by `agent_id`, an
  unknown buddy gives `[]`), every phone gets every event, live and on replay, and any phone answers
  any approval card, a self-change card too.
- **Which phone is it.** Any token the mock did not hand out is the first phone's (`dev_01J9ZQ4K2B`,
  the contract's `Device` example), which shows Marcel. `GET /api/devices` lists the phones, newest
  first, with `current` on the one asking.
- **Switching buddy.** `PATCH /api/devices/{id}` `{agent_id}` sets the buddy that phone shows (any phone
  may switch any phone) and sends `device.updated`. An unknown phone is 404 `device_not_found`; an
  unknown or archived buddy 404 `agent_not_found`.
- **Pairing another phone.** `POST /api/pairing-codes` (body optional) gives a one-time code (`MOCK01`,
  `MOCK02`, ...) whose link carries `&buddy=<id>` when one is named. `POST /api/pair` with it pairs a
  new phone, with its own token, on that buddy (the first live one when none was named, or when it
  was archived since). A used code is a 400. Any other code answers the contract's example: the
  first phone.
- **Signing a phone out.** `DELETE /api/devices/{id}` of a phone the mock paired: its token is a 401
  from then on and its stream is refused. The first phone stays (any token is still its).
- **Push.** `PUT /api/devices/{id}/push` stores the target (`apns`, `ntfy` or `relay`, checked against
  the contract) and `Device.push` shows its kind; the relay's `send_secret` is never shown again.
  Phones start at `none`. Nothing is pushed.
- **Adding a buddy.** `POST /api/agents` makes one with its own main conversation and Inbox and sends
  `agent.updated`; without a `look` it gets one picked from its name (the same name, the same look).
  No phone switches to it by itself.
- **Archiving a buddy.** `POST /api/agents/{id}/archive` sets `archived_at`, sends `agent.updated`, and
  moves each phone that showed it to the first live buddy (`device.updated` each). It leaves `Me.agents`
  but stays readable by id, its chat too. Archiving it again answers it unchanged; the last live
  buddy is refused with 409 `last_buddy`.
- **A look change** (`PATCH /api/agents/{id}` with `look`, `name` or `onboarded`) is remembered and sends
  `agent.updated`, with `avatar_etag` `"lk-<animal>-<color>-<personality>"`. `onboarded_at` is set
  once. Pip starts without it, so a phone on Pip shows "Meet your helper" (S-17.5).
- **Avatars are drawn by the Pixel Zoo** (`shared/avatar/zoo`), as the real hub draws them. `GET
  /api/agents/{id}/avatar` answers the look's sheet: its JSON, or its PNG for `Accept: image/png`, both
  with the ETag; `If-None-Match` with it answers 304. A buddy without a look (none, today) would get
  the rose giraffe's sheet and no ETag. `GET /api/avatar-previews` draws one 32 × 32 PNG of any look for the look picker;
  an unknown animal, colour, personality or mood is a 400.
- **Quoted replies (B-41).** Every milestone, approval card and item message of a run carries
  `reply_to_message_id`: the message that started it. Say something else while a run waits, and its
  `done` milestone answers an older message than your latest: the app shows a quote.
- **Mentions (B-42).** `GET /api/mentions?q=&kind=` lists every buddy's quests, projects and items
  whose title holds `q`, newest first, at most 20 (boards left out). A message's `mentions` must
  exist (any buddy's), or it is refused with 404 `mention_not_found` and nothing is stored; the stored
  message carries the hub's titles, and a project mention files it in that project.

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
