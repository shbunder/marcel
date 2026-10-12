# Marcel hub

State, app API, channel gateway and scheduler. Runs in Docker on the NUC. This page covers what
exists today: the app skeleton, the database, `GET /api/health`, pairing a phone, the task
routes, conversations, the live event stream, the channel gateway and approvals.

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
- `GET /api/me` reports `push` as off for both backends until S-08.1 and S-08.2 add them, and every
  agent as `idle` until S-04.8.
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
  and sends no new event, so a retry never makes a duplicate. `project_id` is accepted and not yet
  stored (S-04.10).
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

`/api/tasks` lists, reads, steers, stops, archives and hands back tasks (see
`contracts/app-api.yaml`). The code is in `src/marcel_hub/tasks/`.

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
  and one "something broke" alert is raised (logged until the notifier, S-08.1, exists). The hub
  retries after 0.5 s, then 1, 2, 4 … up to 30 s. When the runner answers again the alert is
  cleared, each task takes the state the runner reports, and queued tasks start if there is room.
- **Bad input.** An event that does not match the contract is logged (`Ignored a bad runner event`)
  and dropped; the stream carries on.

## Channel gateway: `/channel` and approvals

Every Claude Code session on the NUC (the brain, a side thread, a worker) holds one WebSocket to
`ws://127.0.0.1:7420/channel` through the marcel plugin's channel server
(`contracts/channel-protocol.md`, `docs/channel-server.md`). The code is in
`src/marcel_hub/channel/`.

- **Outside `/api`.** The Cloudflare tunnel forwards only `/api/*`, so the channel is reachable
  from the NUC alone. It takes no device token.
- **Hub secret.** The hub reads it from `<data_dir>/hub-secret` (at least 32 characters; deploy
  writes the same value to the plugin's `hub-secret`). A session with another secret is rejected
  `bad_secret`. With no secret file, every session is. Either raises one "something broke" alert
  (logged until the notifier, S-08.1, exists).
- **Who a session is.** Only a session id the runner reported: a NUC task's `session_id` (role
  `worker`, or `adopted`) or a conversation's `brain_session_id` (`brain` for the main
  conversation, `side` for a side thread). Anything else is rejected `unknown_session`; the
  server retries for two minutes. A plugin older than 0.1.0 is rejected `plugin_too_old`.
- **A session acts only for itself.** A worker's `report` lands on its own task; a brain's or side
  thread's `reply` in its own conversation. A worker's `reply`, a brain's `report` and a brain's
  permission request are dropped and logged. A session can never answer a prompt.
- **Calls.** Each stored `reply` or `report` is answered `call_ack`; a repeated `call_id` is not
  stored again and is acked with `duplicate: true`. A call the hub fails to store is not acked, so
  the server sends it again after a reconnect.
- **Reports.** `started` and `progress` become `progress` task events (and move the task to
  `working`); artifacts are stored (the row only, until S-04.7 copies files) with an `artifact`
  event; `done` and `failed` set the summary and the state. `started`, `done` and `failed` post a
  milestone in the main conversation. A report the state machine does not allow (for a task you
  stopped) is stored but changes no state.
- **Outbox.** Frames for a session (user messages, steers, hand-backs, permission decisions) are
  numbered per task or conversation and kept in the database until the session acks them, so a
  session that is offline, or a task still `queued`, gets them after its hello, in order, and a
  restart loses nothing. The hub sends from the database a page at a time; a session that stops
  reading costs nothing but memory for one flag. A newer socket for the same session replaces the
  old one. A socket gets 15 s to say hello, and a frame over 256 KB closes it.
- **Owner messages.** A message you send in the main conversation, or in a side thread, goes to
  that conversation's session. A thread that belongs to a task is left to the task threads
  (S-04.9).

### Approvals

A worker's permission prompt becomes an approval card: the task goes to `needs_you`, a
`permission` task event, an `approval` message in the main conversation, `approval.created` on
the phone, and a push (logged until S-08.1). The same `request_id` on the same task never opens a
second card, whatever state the card is in.

- `GET /api/approvals?state=` lists your cards, open ones first.
- `POST /api/approvals/{id}` with `{"decision": "approve" | "deny", "note"?}` answers a card and
  sends the session its `permission_decision`. A denial's note follows as a `steer`. Only the
  owner of the task's agent sees or answers a card (404 for anyone else). A card that is no
  longer open answers 409 with the card as it stands.
- **Answered in a terminal.** When the runner shows the session left the permission prompt and the
  transcript has the tool's result, the card becomes `approved` (or `denied`, when the result says
  `denied: true`) with `answered_via: terminal`. The first answer wins, from the app or the
  terminal.
- **Expired.** When a task stops or fails with an open card, the card becomes `expired`.
- Every change to a card sends `approval.resolved`.
