# Marcel hub

State, app API, channel gateway and scheduler. Runs in Docker on the NUC. This page covers what
exists today: the app skeleton, the database, `GET /api/health`, pairing a phone, the task
routes, conversations, the live event stream, artifacts and their versions, memory, projects and
their boards, push notifications, the channel gateway and approvals.

## Run

```bash
uv run marcel-hub
```

It listens on the `host` and `port` from the settings below. If the settings cannot be read, it
stops with `Marcel hub did not start.` and the reason.

On start the hub applies every pending database migration. If a migration fails, the hub does not
start and the error is printed in the log.

## Settings

Read from `/data/marcel.toml`, then from the environment (the environment wins). A missing file is
fine. A file that cannot be parsed, or an unknown key, stops the hub at start with a message that
names the file.

| Key | Environment | Default |
|---|---|---|
| `data_dir` | `MARCEL_DATA_DIR` | `/data` |
| `db_path` | `MARCEL_DB_PATH` | `<data_dir>/marcel.db` |
| `host` | `MARCEL_HOST` | `0.0.0.0` |
| `port` | `MARCEL_PORT` | `7420` |
| `owner_name` | `MARCEL_OWNER_NAME` | `Owner` (shown by `GET /api/me`) |
| `runner_sock` | `MARCEL_RUNNER_SOCK` | `/run/marcel/runner.sock` (the runner's socket, mounted into the container) |
| `worker_cwd` | `MARCEL_WORKER_CWD` | unset. Where workers without a repository run, e.g. `/home/<you>/marcel/workspaces/scratch` (a path on the NUC). Unset: such tasks fail with a reason |
| `ntfy_server` | `MARCEL_NTFY_SERVER` | unset: no pushes. The ntfy server phones subscribe on, e.g. `https://ntfy.sh` or a self-hosted one. Must start with `https://` or `http://` |

`MARCEL_CONFIG` moves the config file itself (tests use it).

## Database

SQLite through SQLModel; models are in `src/marcel_hub/models.py`, revisions in `migrations/`.
After changing a model, generate a revision and read it before committing:

```bash
uv run python -c "from alembic import command; from marcel_hub import db; \
e = db.make_engine('/tmp/new.db'); command.revision(db.alembic_config(e), message='what', autogenerate=True)"
```

A test fails if the models and the migrations disagree. Timestamps are timezone-aware UTC.

## GET /api/health

Needs no token. Returns `ok` (false when the database does not answer), `version`, and
`runner.reachable`: true while the hub's event stream from the runner is open. When it is false,
`runner.reason` says why. The app shows that as "the NUC is not reachable".

## Pairing a phone

```bash
marcel-hub pair                                   # inside the hub container, or `uv run marcel-hub pair`
marcel-hub pair --hub http://127.0.0.1:7420/api   # a phone on the home network, no tunnel
```

It prints a QR code and an 8-letter code. The QR code holds
`marcel://pair?hub=<hub URL>&code=<code>` (`PairingCode` in the contract). The code works **once**,
for **10 minutes**. The app sends it to `POST /api/pair` and gets a device token back. A new
`marcel-hub pair` removes used and expired codes.

- A token is `mrc_` plus 32 random bytes in hex. The hub keeps only its sha256, so the token is
  shown once and cannot be read back from the database. Pairing codes are stored hashed too.
- Every route except `GET /api/health` and `POST /api/pair` needs `Authorization: Bearer <token>`.
  Without it, or with a revoked token, the answer is 401. `/docs` and `/openapi.json` are off.
- `GET /api/devices` lists your devices; `DELETE /api/devices/{id}` revokes one, and its token
  stops working at once. `PUT /api/devices/{id}/push` stores an APNs token or ntfy topic.
- `GET /api/me` reports `push.ntfy` (and `push.ntfy_server`) when `ntfy_server` is set, `push.apns`
  as off until S-08.2, and every agent as `idle` until S-04.8.
- After 10 unknown codes in a minute, `POST /api/pair` refuses every code (429,
  `too_many_tries`) until the minute is over. Expired or already-used codes do not count.
- Every error has the contract's `Error` shape: a stable `code` and a `message` for people. A bad
  request body is a 400 with `code: bad_request`; a crash is a 500 with `code: internal_error`,
  and the details go to the log only.

New routers protect themselves the same way, and read the caller with `current_device(request)`:

```python
router = APIRouter(prefix='/api', dependencies=[Depends(require_device)])
```

`tests/test_auth_routes.py` calls every route in the app without a token and fails on any that
answers something other than 401. A route type it cannot call (a WebSocket) fails it too, until the
test learns how.

The WebSocket follows the same rule: `tests/test_auth_routes.py` opens every socket without a token
and with an unknown one, and fails if the hub accepts it.

## Conversations

Each agent has one **main** conversation; `conversations.ensure_main_conversation(session, agent)`
makes it and sets `agent.main_conversation_id`. Only the agent's owner sees its conversations;
anyone else gets a 404 `conversation_not_found`.

- `GET /api/conversations?agent_id=…`: the main conversation first, then side threads, newest
  first, each with `reply_count` and `last_message_at`.
- `GET /api/conversations/{id}/messages?limit=&cursor=`: newest first, at most `limit` (1–200,
  default 50). Pass `next_cursor` to get older messages; the last page has none. A cursor from
  another conversation is a 400 `bad_cursor`. A message with a side thread carries
  `thread: {conversation_id, reply_count}`.
- `POST /api/conversations/{id}/messages`: stores the message and answers 201 at once; it never
  waits for Marcel (B-02). The same `client_id` in the same conversation returns the stored message
  and sends no new event, so a retry never makes a duplicate. A `project_id` (sent from a project
  room, B-32) must be one of the agent's projects (400 otherwise) and is stored on the message;
  the message still goes to the main conversation, and `message.project_id` tells Marcel which
  project it is about.
- `POST /api/messages/{id}/thread`: opens the side thread on a message (201), or returns the one it
  already has (200). A thread on a milestone or approval gets that message's `task_id`.
- `POST /api/conversations/{id}/send-to-main`: from a side thread, `{"message_id"}` quotes one of
  its messages into the main conversation (`kind: quote`); `{"text"}` posts your own text there.

Other code posts messages (Marcel's answers, milestones) with `conversations.post_message(...)`,
which also sends the `message.created` event.

## Live events: `GET /api/ws`

One WebSocket per phone, with the same `Authorization: Bearer` header as REST. Without a valid
token the hub closes before accepting (the phone sees a 403). A phone removed while connected is
cut off before its next event.

- Every event (`contracts/events.schema.json`) goes to every open socket of the user. Its `id` is
  one higher than the user's last, and it is stored before it is sent, so ids keep counting after
  a restart.
- The first frame is `hello` with `last_event_id` and how many events were `replayed`. Reconnect
  with `?since=<last id you processed>` to get exactly what you missed, in order.
- The hub keeps the last 2000 events per user (`ws.KEEP_EVENTS`). If `since` is older than that,
  higher than the last id, or not a number, the first frame is `resync.required`: re-fetch over
  REST, then carry on from its `last_event_id`. Live events still follow on the same socket.
- A phone that stays connected but stops reading is closed (code 1013, try again later) once 2000
  events wait for it (`ws.MAX_PENDING`); it reconnects with `?since=` and catches up from the
  database, so the hub never holds an unbounded backlog in memory.
- Ordering comes from one lock in `EventLog`: numbering, storing and handing out an event happen
  together, and a reconnecting socket is registered under the same lock as its replay is read, so
  no event falls in between.
- Other code sends events with `request.app.state.events.publish(session, user_id, type, data)`;
  it commits the session with the event, so the change and its event land together.

## Tasks

`/api/tasks` lists (filters `state`, `archived`, `project_id`), reads, steers, stops, archives and
hands back tasks (see `contracts/app-api.yaml`). The code is in `src/marcel_hub/tasks/`.

- **State machine.** Every state change follows `contracts/task-states.md`. Anything else is refused
  and logged as `Refused a task transition: …`. Each change writes a `state` task event.
- **Queue.** When the runner says its worker cap is full, a new task waits as `queued`. When a task
  finishes, fails or is stopped, the oldest waiting task starts. A new task never skips the queue.
  The queue is read from the database, so a restart keeps it.
- **Steer** (`POST /tasks/{id}/messages`) records a `note` and hands the text to the outbox. A `done`
  task goes back to `working`. A `stopped` or `failed` task answers 409 `task_finished`.
- **Stop** asks the runner to stop the session. A queued task just leaves the queue. If the runner
  does not answer, stop answers 503 `runner_unreachable` and the task is left as it was.
- **Archive** works only on `done`, `failed` or `stopped` tasks (409 otherwise). Archived tasks are
  listed only with `archived=true`.
- **Hand-back** records a `note` and puts a `handback` event for Marcel's brain in the outbox.

Steer and hand-back messages go to the channel gateway's Outbox (below), which delivers them to
the session.

## Runner

`src/marcel_hub/runner_client.py` talks to the runner over its unix socket
(`contracts/runner-api.yaml`). It starts and stops worker sessions for the task registry, and a
background watcher follows the runner's streams:

- **State.** `GET /events` reports each session's state. The hub maps it onto the task states:
  `working` → working; a permission prompt → `needs_you` ("Your approval: …"); a question, or a
  session left idle after a refused step → `needs_you`; a usage limit → `needs_you` naming the
  reset time (from `GET /usage`); `done` (with its one-line result as the summary), `failed`,
  `stopped`. A report the state machine does not allow (a stopped task whose session shows `done`)
  changes nothing. The session's `remote_url` is copied onto the task. Approval cards come from the
  channel (S-04.6), not from here.
- **Transcript.** Each task session's `GET /sessions/{id}/tail` is stored as task events (the app
  gets them as `task.event`). `thinking` and `turn_end` are not stored. The hub remembers how far it
  read each transcript in `<data_dir>/runner-offsets.json`, so a reconnect or a restart never stores
  an event twice.
- **Runner down.** When the runner does not answer, every NUC task that was live goes to `unknown`,
  and one "something broke" alert is pushed (see Push notifications). The hub
  retries after 0.5 s, then 1, 2, 4 … up to 30 s. When the runner answers again the alert is
  cleared, each task takes the state the runner reports, and queued tasks start if there is room.
- **Bad input.** An event that does not match the contract is logged (`Ignored a bad runner event`)
  and dropped; the stream carries on.

## Artifacts

`/api/artifacts` lists items, newest change first (filters `task_id`, `project_id`, `kind`), reads
one, lists its versions (`/versions`), and serves its content, by version with `?version=`. The
code is in `src/marcel_hub/artifacts/` (`store.py`, `routes.py`). Boards are listed only with
`kind=board`; the Items tab shows what tasks made.

- **Handing one in.** A worker's `report` artifact (`channel.schema.json` `Artifact`) goes to
  `ArtifactStore.publish_reported`. `pr`, `branch` and `link` need an `http(s)` URL; a `pr` URL must
  end in `/pull/<n>`. `doc` (Markdown or HTML), `file` and `dashboard` need an absolute path, and the
  hub copies the bytes to `<data_dir>/artifacts/<id>`, only from inside the workers' folder
  (`files_root`). It opens the path one folder at a time and refuses any symlink on the way, so a
  worker cannot swap a folder for a link while the hub reads. Over 25 MB is refused. Until the channel
  gateway sets `files_root`, file artifacts are refused with that reason.
- **Dashboards** are checked against `contracts/dashboard.schema.json`, and every chart series must
  have one value per label. A bad one is refused with a message that says where and what to fix.
- **Trust.** HTML a worker hands in is stored with `trusted: false`. Only HTML the hub renders
  itself (`publish_hub_html`) is trusted. The hub never renders content: it serves the bytes with
  their own content type, `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`
  (`sandbox allow-scripts` for trusted HTML).
- **Recorded** on the task as an `artifact` event, and announced to the app as `artifact.created`.
- **Item get** (B-36). A new artifact a task made also posts an `artifact` message (with
  `artifact_id` and `project_id`) in the agent's main conversation. Later versions never post
  another. If the message cannot be posted, the artifact is still stored and the log says so.
- **Versions** (B-34). A `report` artifact with `artifact_id` adds the next version instead of a new
  item: `current_version`, `updated_by` (the task's title, "Marcel" or "the hub") and `updated_at`
  move on, its row in `artifact_version` keeps the URL, type, size, author and one-line `summary`,
  and the app gets `artifact.updated` with the artifact and the version. Each version's bytes are
  kept in their own file (`<id>` for version 1, `<id>.v<n>` after), so older versions stay
  readable. Any task in the same project may add a version; the kind cannot change. An id the hub
  does not have, one from another project, or a board is refused (`UnknownArtifact`): the channel
  answers the whole report with `call_ack.error` (`unknown_artifact`) and stores none of it.
- **PR status.** `refresh_pr` asks a `PrStatusSource` for the PR's state, checks and review;
  `pr_status_from_gh` maps GitHub's REST answers to them. The hub reads GitHub itself with a
  read-only token (ADR-002); that client comes with S-04.12. Until then a refresh answers that the
  hub cannot ask GitHub. A PR starts as `open`.
- **Project.** An item belongs to its task's project, and moves with the task (the app gets
  `artifact.updated` for each, with its current version). One with no task goes to the inbox of
  the hub's first agent.
- **Migration 0006** added `artifact_version` (version 1 for every existing artifact), the current
  version on `artifact`, `artifact_id` on `message`, and a board for every project. Its downgrade
  drops boards and `artifact` messages and keeps every other row. Older code reads only `<id>`, so
  after a downgrade an item that had reached version 2 or later shows its version 1 bytes.

## Project boards

Every project has a board (B-35): an artifact of kind `board` under the project's
`board_artifact_id`, made in the same write as the project (`models.make_board`). The hub builds its
content itself, with no brain turn. The code is `src/marcel_hub/boards.py`.

- **What it shows.** A dashboard (`contracts/dashboard.schema.json`, checked before it is stored)
  with four sections: *Waiting on you* (open approvals, and tasks that need you without a card),
  *In flight* (each running task with its state and the last step it reported), *Recent results*
  (the newest 5 tasks done in the past 7 days, each with its summary and items at their version),
  *Upcoming* (empty until schedules exist), then Marcel's notes, the newest 5.
- **When.** `BoardKeeper` watches the app event log. Any task, approval or artifact change in a
  project (and a project created, renamed or moved into) marks its board. A marked board is rebuilt
  10 s after the first mark, and never sooner than 10 s after its last rebuild, so a burst of
  changes makes one rebuild. Events about boards are ignored, so a rebuild never marks itself. A
  loop on the hub's event loop rebuilds what is due once a second; at start every board is marked.
- **Versions.** A rebuild stores a new version (`artifact.updated`, author "the hub") only when
  the content or title changed. A board's first content sends `artifact.created`. A board read
  before its first build is built on the spot.
- **Marcel's notes.** `BoardKeeper.board_note(project_id, text)` adds a "what this means" line
  (cut at 400 characters) and marks the board. The brain's `board_note` tool (S-06.6) calls it.

Needs `jsonschema` at run time and `contracts/dashboard.schema.json` next to `hub/`.

## Memory

Markdown files in the folder `<data_dir>/memory` (B-20), which the brain can reach. Their git
database is `<data_dir>/memory.git`, next to it, which only the hub can reach. The hub creates both
on first use. The code is `src/marcel_hub/memory.py`.

- `GET /api/memory` lists every `.md` file; `GET /api/memory/files/{path}` reads one. Its `etag` is
  the git blob id of the file on disk, so an edit made anywhere changes it. The `ETag` header is the
  same value in quotes.
- `PUT` with `If-Match: <etag>` (bare, quoted or `W/`) saves only if the file has not changed, else
  409 with the current version. Without `If-Match` it creates the file, and answers 409 if it
  exists. An `If-Match` for a file that is gone answers 404.
- **Size.** A file over 1 MB is refused (400). The request body is read only up to 2 MB (JSON
  escapes take room) and refused beyond that. A file written straight to disk that is over 1 MB is
  never read whole; opening it answers 400 `memory_file_too_big`.
- **Every save is one git commit**, `memory: <path> (app)` from the app. `(agent)` is for the brain's
  saves through the hub, which arrive with its memory tools (S-05.3); a file the brain writes
  straight to disk is not committed, but its etag changes, so the app cannot overwrite it unseen.
  An unchanged save makes no commit. Only the saved file is committed. If git fails, the file is put
  back and the answer is 503 `memory_unavailable`.
- **Paths** must match the contract's pattern (no `..`, no leading `/`, no part starting with `.`,
  ending in `.md`), and no part may be a symlink, so nothing outside the memory folder can be read
  or written. Anything else answers 400 `invalid_memory_path`.
- **Git cannot be made to run a program.** Anyone who can write the memory folder (the brain, a
  prompt-injected brain) must not get code running in the hub. So git runs with `--git-dir` outside
  that folder, with no global or system config (`GIT_CONFIG_GLOBAL=/dev/null`,
  `GIT_CONFIG_NOSYSTEM=1`, `GIT_ATTR_NOSYSTEM=1`, other `GIT_*` variables dropped), and with hooks,
  the file-system monitor and attribute files forced off on the command line. The hub refuses to
  save while a `.gitattributes` file is anywhere in the memory folder (503, naming the file).
- **One-time move.** A hub from before this kept `.git` inside the memory folder. On first start the
  hub moves it to `memory.git`, keeps the history, writes a fresh config and drops its hooks and
  `info/attributes`, since the brain could have edited them.
- Needs `git` on the path.

## Projects

`/api/projects` lists, creates, reads, changes and archives projects (B-30…B-33a), and
`POST /api/tasks/{id}/move` files a task under another one. The code is in
`src/marcel_hub/projects/`.

- **Inbox.** Every agent has one project with slug `inbox`, made in the same write as the agent
  (`models.inbox_id`), whoever inserts it. A task stored without a project is filed there, and the
  database refuses a task with none (triggers `task_needs_project_*`). Migration 0005 gave every
  existing agent its inbox and filed existing tasks and artifacts there. Its downgrade keeps every
  row: it turns foreign keys off while it rebuilds the tables, and `db.downgrade` then drops the
  pooled connections so the next one has them on again.
- **Slugs** match `^[a-z0-9][a-z0-9-]{0,39}$` (400 otherwise). Without one, the slug is made from
  the name (`Energy contracts` → `energy-contracts`, then `-2`, `-3` … if taken). A slug asked for
  that a live project of the agent already has answers 409 `slug_taken`; the database index
  `ix_project_live_slug` enforces it. An archived project's slug can be used again.
- **Notes** (B-33). Creating a project creates `memory/projects/<slug>.md` in the memory folder
  (one git commit). Notes already there (from an archived project with that slug) are kept. If git
  fails, the project is still made, and the log says the notes file was not.
- **Briefing.** A worker's opening prompt starts with its project: name, goal, repo, folder,
  standing instructions and notes, each cut at `memory.SLICE_MAX_BYTES` (8 KB) with a line that
  says where the rest is. Instructions and notes sit between markers
  (`<<<project-notes slug=…>>>` … `<<<end project-notes>>>`); any `<<<` inside them is broken up,
  so notes a worker wrote cannot close the block and pass as the task. An inbox with no notes or
  instructions adds nothing. The stored opening note (what the app shows) is the prompt alone. If
  the briefing cannot be built, the task starts without it and the log says
  `starts without its project briefing` and why.
- **Archive** refuses the inbox (409 `project_is_inbox`) and a project with any task that is not
  done, failed or stopped (409 `project_busy`).
- **Move.** Only into a live project of the same agent (409 `project_archived`, 404
  `project_not_found`). The task's items move with it. The app gets `task.moved`, `task.updated`,
  then `project.updated` for the new and the old project, then `artifact.updated` for each item
  (now in the new project). Both boards are rebuilt. Moving into the same project sends
  nothing. No brain turn.
- **Counts** on each project: `working` (queued, starting, working, silent, unknown), `needs_you`,
  and `done_7d` (done, last changed in the past 7 days). `updated_at` is the latest change to the
  project or any task in it. Every `task.created`/`task.updated` is followed by `project.updated`
  for that task's project.
- **Owner only.** Someone else's project answers 404 `project_not_found`, someone else's agent
  404 `agent_not_found`, and listing it gives `[]`.
- **Board.** Made with the project under `board_artifact_id`; see *Project boards*.

## Push notifications

`src/marcel_hub/notify/` sends pushes to paired phones (B-16, B-18). Set `ntfy_server` to turn
them on; the app then registers an ntfy topic with `PUT /api/devices/{id}/push`.

| Event | Push | Opens |
|---|---|---|
| A task opens an approval | approval, urgent: "Marcel needs your OK" | the approval |
| A task needs you (a question, a login) | task, urgent: "Marcel needs you" | the task |
| A task is done / failed | task, normal: "Done: …" / "Failed: …" | the task |
| A task went silent | broke, urgent: "A task went quiet" | the task |
| The runner is unreachable / warns | broke, urgent: "Marcel cannot reach the NUC" | the task that changed last |
| The runner answers again | broke, normal: "The NUC is back" | the same |
| An urgent push could not be delivered | broke, urgent: "A notification did not arrive" | what it was about |
| A digest (`pusher.digest(...)`) | digest, normal | the digest's message |

Nothing else pushes: no progress, no `working`, `queued`, `unknown` or `stopped`.

- **How events arrive.** The task registry's publisher is `pusher.task_publisher()`: each `state`
  task event is one push at most. The runner watcher uses the pusher as its alerts. The channel
  gateway can call `pusher.approval_opened(id)` when it opens an approval.
- **ntfy.** A JSON POST to the server's root: `topic`, `title`, `message`, `tags` (the category),
  `priority` (4 for urgent, 3 for normal) and `click` (the `marcel://` deep link). No token ever goes
  to the ntfy server; the push only opens the app.
- **Quiet repeats.** The same push for the same task is not sent twice within 10 minutes. An urgent
  "broke" push goes out once per cause per hour; when its all-clear goes out, that hour starts over,
  so a second outage is pushed again. Every push carries a collapse key (the task id, or the cause),
  but only APNs uses it to replace the older push on the phone: on ntfy each push shows separately.
- **A failed push never breaks the hub.** Pushes are sent in the background, tried three times
  (after 2 s and 10 s), then given up and logged. A refused push (4xx other than 429) is not
  retried. When an urgent push did not arrive, one "a notification did not arrive" push goes to
  every device.
- **Secrets.** An ntfy topic lets anyone read the pushes. Logs name the device id, never the topic.
- **APNs** (S-08.2) is a second backend with `kind = 'apns'`; devices that registered an APNs token
  get nothing until it exists, and the log says so.

## Channel gateway: `/channel` and approvals

Every Claude Code session on the NUC (the brain, a side thread, a worker) holds one WebSocket to
`ws://127.0.0.1:7420/channel` through the marcel plugin's channel server
(`contracts/channel-protocol.md`, `docs/channel-server.md`). The code is in
`src/marcel_hub/channel/`.

- **Outside `/api`.** The Cloudflare tunnel forwards only `/api/*`, so the channel is reachable
  from the NUC alone. It takes no device token. As a backstop, a `/channel` request carrying
  Cloudflare's `cf-connecting-ip` header is refused before it is accepted.
- **Hub secret.** The hub reads it from `<data_dir>/hub-secret` (at least 32 characters; deploy
  writes the same value to the plugin's `hub-secret`). A session with another secret is rejected
  `bad_secret`. With no secret file, every session is. Either raises one "something broke" push.
- **Who a session is.** Only a session id the runner reported: a NUC task's `session_id` (role
  `worker`, or `adopted`) or a conversation's `brain_session_id` (`brain` for the main
  conversation, `side` for a side thread). Anything else is rejected `unknown_session`; the
  server retries for two minutes. A plugin older than 0.1.0 is rejected `plugin_too_old`.
- **A session acts only for itself.** A worker's `report` lands on its own task; a brain's or side
  thread's `reply` in its own conversation. A worker's `reply` and a brain's `report` are dropped
  and logged. A session can never answer a prompt.
- **Brain and side-thread prompts are denied.** An approval card belongs to a task, so the brain
  or a side thread cannot get one (`project/contract-requests/S-04.6.md`). Rather than wait at its
  prompt for ever, the session gets `permission_decision deny` at once, and the log says which
  tool it asked for. A re-sent prompt is denied again.
- **Calls.** Each stored `reply` or `report` is answered `call_ack`; a repeated `call_id` is not
  stored again and is acked with `duplicate: true`. A report that asks for a new version of an
  artifact it may not update is refused whole: `call_ack` with `error` (`unknown_artifact`), and
  nothing of it is stored or recorded. A call the hub fails to store is not acked, so
  the server sends it again after a reconnect.
- **Reports.** `started` and `progress` become `progress` task events (and move the task to
  `working`); artifacts go to the artifact store (files are copied from the workers' folder) and
  get an `artifact` event; one the store refuses becomes an `error` event saying why, and the rest
  of the report still counts; `done` and `failed` set the summary and the state. `started`, `done` and `failed` post a
  milestone in the main conversation. A report the state machine does not allow (for a task you
  stopped) is stored but changes no state.
- **Outbox.** Frames for a session (user messages, steers, hand-backs, permission decisions) are
  numbered per task or conversation and kept in the database until the session acks them, so a
  session that is offline, or a task still `queued`, gets them after its hello, in order, and a
  restart loses nothing. The hub sends from the database a page at a time; a session that stops
  reading costs nothing but memory for one flag. Acked frames are deleted (the newest row of each
  task or conversation stays, to keep the numbering). When a task stops or fails, frames it never
  received are dropped. A newer socket for the same session replaces the old one. A socket gets
  15 s to say hello, and a frame over 256 KB closes it (uvicorn's `ws_max_size` is the same cap).
- **Owner messages.** A message you send in the main conversation, or in a side thread, goes to
  that conversation's session. Only your own messages go: Marcel's replies are never sent back to
  it. A conversation with no session yet (`brain_session_id` unset) gets nothing queued: the
  session that starts for it reads the conversation. A thread that belongs to a task is left to
  the task threads (S-04.9).

### Approvals

A worker's permission prompt becomes an approval card: the task goes to `needs_you`, a
`permission` task event, an `approval` message in the main conversation, `approval.created` on
the phone, and an approval push. The same `request_id` on the same task never opens a
second card, whatever state the card is in.

- `GET /api/approvals?state=` lists your cards, open ones first.
- `POST /api/approvals/{id}` with `{"decision": "approve" | "deny", "note"?}` answers a card and
  sends the session its `permission_decision`. A denial's note follows as a `steer`. Only the
  owner of the task's agent sees or answers a card (404 for anyone else). A card that is no
  longer open answers 409 with the card as it stands.
- **Answered in a terminal.** When the runner shows the session left the permission prompt and the
  transcript has the tool's result, the card becomes `approved` (or `denied`, when the result says
  `denied: true`) with `answered_via: terminal`. The first answer wins, from the app or the
  terminal. The answer is matched by tool name: the first result of a call to the same tool that
  arrived after the card opened. Two prompts for the same tool at once can be matched the wrong
  way round (a known limit).
- **Expired.** When a task ends (`done`, `failed` or `stopped`) with an open card, the card is
  resolved from the transcript if it can be, and otherwise becomes `expired`. At start the hub does
  the same for cards left open while it was down.
- Every change to a card sends `approval.resolved`.
