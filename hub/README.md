# Marcel hub

State, app API, channel gateway and scheduler. Runs in Docker on the NUC. This page covers what
exists today: the app skeleton, the database, `GET /api/health`, pairing a phone, the task
routes, conversations and task threads, the live event stream, artifacts and their versions, memory, projects and
their boards, the avatar's state, push notifications, the usage guard, the channel gateway and approvals, the brain tools' routes, keeping the brain alive, and cloud tasks.

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
| `brain_cwd` | `MARCEL_BRAIN_CWD` | unset: the hub starts no brain. The brain workspace on the NUC, e.g. `/home/<you>/marcel/brain` (`brain/README.md`). Set: the hub keeps one brain running (see "The brain: keep-alive and rollover") |
| `brain_rollover_on_context` | `MARCEL_BRAIN_ROLLOVER_ON_CONTEXT` | `false`. Claude Code's own auto-compact keeps the brain's context (S-06.8, ADR-010). `true` turns the S-06.7 rollover by context size back on; anything other than true or false stops the hub at start |
| `brain_context_window` | `MARCEL_BRAIN_CONTEXT_WINDOW` | `200000`, the Opus context window in tokens. Only with `brain_rollover_on_context`: once a brain turn ends with its context past 70 % of this, the brain rolls over (reason `context_full`). A brain model named with Claude Code's `[1m]` suffix (e.g. `opus[1m]`) uses 1 000 000 instead. Less than 10000, or not a number, stops the hub at start |
| `ntfy_server` | `MARCEL_NTFY_SERVER` | unset: no pushes. The ntfy server phones subscribe on, e.g. `https://ntfy.sh` or a self-hosted one. Must start with `https://` or `http://` |
| `apns_key` | `MARCEL_APNS_KEY` | unset: no APNs pushes (`push.apns` false; phones use ntfy). The APNs auth key: the text of the `AuthKey_<id>.p8` file from the Apple developer site (Keys, with Apple Push Notifications service). The whole file, the file on one line with `\n` for its line breaks, or only the lines between `BEGIN` and `END`. Set it in `deploy/.env.local`, never in the TOML file or git. Never logged or shown. A key that is not a P-256 `.p8` key stops the hub at start |
| `apns_key_id` | `MARCEL_APNS_KEY_ID` | unset. The key's 10-character id (in the file name and on the Keys page). `deploy/.env.local`; never logged |
| `apns_team_id` | `MARCEL_APNS_TEAM_ID` | unset. The Apple developer team id (Membership details). `deploy/.env.local`; never logged |
| `apns_topic` | `MARCEL_APNS_TOPIC` | unset. The iOS app's bundle id, e.g. `com.<you>.marcel`. Set all four `apns_*` or none: half of them stops the hub at start, naming what is missing |
| `apns_sandbox` | `MARCEL_APNS_SANDBOX` | `false`: Apple's production server. `true` for an app built and run from Xcode (development push environment). Check this first when pushes stop: set wrong, Apple answers `BadDeviceToken` for every push, and the hub removes each phone's token until its app opens again |
| `github_token` | `MARCEL_GITHUB_TOKEN` | unset: PR cards are not refreshed and say the token is missing. A fine-grained, **read-only** GitHub token (Pull requests: read, Checks and Commit statuses: read) on the owner's repos (ADR-002). Set it in `deploy/.env.local`, never in the TOML file or git. Never logged or shown |
| `redeploy_flag` | `MARCEL_REDEPLOY_FLAG` | `/state/redeploy.flag`. The file the hub writes the merge commit to when an approved self-change is merged ("Self-changes" below). Compose mounts the host's `~/marcel/state` at `/state`; the host's `marcel-redeploy.path` unit watches the file (S-11.2) |
| `tz` | `MARCEL_TZ` | `Europe/Brussels`. The owner's time zone (IANA name), and the only one the hub uses: the built-in digests run in it (ones the owner has not edited move when it changes), their facts and the usage guard's pushes show times in it, and the brain's nightly rollover runs at 04:00 in it. The process's own `TZ` is never used. A name that is not a time zone stops the hub at start |

`MARCEL_CONFIG` moves the config file itself (tests use it).

## Database

SQLite through SQLModel; models are in `src/marcel_hub/models.py`, revisions in `migrations/`.
After changing a model, generate a revision and read it before committing:

```bash
uv run python -c "from alembic import command; from marcel_hub import db; \
e = db.make_engine('/tmp/new.db'); command.revision(db.alembic_config(e), message='what', autogenerate=True)"
```

A test fails if the models and the migrations disagree. Timestamps are timezone-aware UTC.

**Stored for app-api 0.7, not used yet** (migration 0010, S-04.15): the `user` table (`id`, `name`,
`role` owner|member, `created_at`); on `agent` the look (`look_animal`, `look_color`,
`look_personality`, the contract's `look`), `onboarded_at`, `archived_at` and a unique `slug` that a
rename never changes (the owner's agent from before 0.7 is `marcel`); on `message`
`reply_to_message_id` and `mentions_json` (the contract's `mentions`, `[]` by default); on `project`
`tracker_url`. The routes and the owner's `user` row come with S-17.x, S-18.x and S-19.2.

## GET /api/health

Needs no token. Returns `ok` (false when the database does not answer), `version`, and
`runner.reachable`: true while the hub's event stream from the runner is open. When it is false,
`runner.reason` says why. The app shows that as "the NUC is not reachable".

## Whole-path health: `GET /internal/health`

`/api/health` says the hub is up. `/internal/health` says whether Marcel can answer: it checks the
whole path and is what `deploy/health.sh` (and so every deploy) waits for.

- `hub`: `ok` when the database answers.
- `runner`: `ok` when the runner's `GET /health` answers ok within 5 s, with `latency_ms`;
  otherwise `down` and a `reason`.
- `brains`, one per agent: `ok` when the main conversation has a brain session, its channel socket is
  connected, and it sent a frame in the last 45 s (`last_seen_s`). The channel server inside the
  brain's `claude` process pings every 20 s, so this proves the process and its link to the hub are
  alive without spending a model turn. Otherwise `down`, with a `reason`: no brain session yet, not
  connected, or silent.

200 with `ok: true` when every link is ok; 503 with the same body otherwise. With no agent yet (a
fresh install, before `setup`) `brains` is empty and a `note` says so: not a failure, so
`make -C deploy install` passes; the brain is checked from the first agent on. **Local only**: it is
outside `/api`, so the tunnel never forwards it, and a request with the tunnel's `cf-connecting-ip`
header gets 404. It takes the hub secret in `X-Marcel-Secret` (401 `bad_secret` without it), never a
device token, and changes nothing. Not a contract route (`hub/src/marcel_hub/path_health.py`).

```bash
printf 'X-Marcel-Secret: %s\n' "$(cat ~/marcel/data/hub-secret)" | curl -s -H @- http://127.0.0.1:7420/internal/health
# {"ok":true,"hub":"ok","runner":{"state":"ok","latency_ms":4},"brains":[{"agent_id":"agt_…","state":"ok","last_seen_s":7}]}
```

## The owner and their agent: `marcel-hub setup`

```bash
marcel-hub setup             # inside the hub container: docker exec -it marcel-hub marcel-hub setup
marcel-hub agent list
```

`setup` (S-17.1) is the first step on a new NUC and is safe to run again. It makes:

- **the owner** (`usr_owner`, role `owner`, named `MARCEL_OWNER_NAME`): the person whose Claude login
  the NUC uses (B-37, B-38). The hub also makes this row at every start-up, so phones paired before
  0.7 keep working.
- **the owner's agent**, named `MARCEL_AGENT_NAME` (else `Marcel`, or `--agent-name`), with slug
  `marcel`, Marcel's look (giraffe, rose, professional), `persona_path` `<brain_cwd>/CLAUDE.md`, and its
  main conversation and Inbox, in one transaction. An agent already there (an older `agent create`
  made one) is reused: it gets the slug `marcel` and the look if it has none, and nothing is made twice.
- **a QR code** that pairs the owner's first phone, only while the owner has none
  (`marcel-hub pair` pairs more).

Family members are added from the owner's app: `POST /api/users` makes the person (role `member`)
and their own agent, main conversation and Inbox; `POST /api/users/{id}/pairing-codes` makes a code
that pairs a phone to them; `DELETE /api/users/{id}` signs out their phones at once, stops their
quests and archives their agent (their chats and notes stay; the owner cannot be removed, 409). All
four are owner-only (`x-owner-only` in the contract; `require_owner` in `auth/deps.py`): a member
gets 403 `owner_only`. The pairing link always names `https://marcel-bot.com/api` (the tunnel), like
`marcel-hub pair`'s default. A member's brain workspace comes with S-17.7; a look picked from the name
when none is given comes with S-19.2.

`marcel-hub agent create` still makes an extra agent for the owner (`--another`; each agent gets its
own brain). `setup` is the way to make the first one.

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
- Every route except `GET /api/health`, `POST /api/pair` and the session API (below) needs
  `Authorization: Bearer <token>`. Without it, or with a revoked token, the answer is 401. `/docs` and `/openapi.json` are off.
- `GET /api/devices` lists your devices; `DELETE /api/devices/{id}` revokes one, and its token
  stops working at once. `PUT /api/devices/{id}/push` stores an APNs token or ntfy topic. An APNs
  token must be the hex device token iOS gives (64 to 200 characters, 0-9 and a-f), else 400.
- `GET /api/me` reports `push.ntfy` (and `push.ntfy_server`) when `ntfy_server` is set, `push.apns`
  when the four `apns_*` settings are set, and each agent's live `state` (see "The avatar's state" below).
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
answers something other than 401 (for the session API: anything but the hidden 404). A route type it cannot call (a WebSocket) fails it too, until the
test learns how.

The WebSocket follows the same rule: `tests/test_auth_routes.py` opens every socket without a token
and with an unknown one, and fails if the hub accepts it.

## Adopting a session: `POST /api/session/adopt`

`/marcel-adopt` (the plugin's command, `plugins/marcel/commands/`) hands a Claude Code session
Marcel did not start to Marcel (B-14, `contracts/session-api.yaml`). Code: `adopt.py`.

```bash
marcel-hub adopt-token     # inside the hub container: a new adopt token, printed once
```

Save the token on every computer you use Claude Code on, readable by you only:
`mkdir -p ~/.marcel && (umask 077 && cat > ~/.marcel/adopt-token)`, paste it, Enter, Ctrl-D. A cloud
session has no such file: give it the token as the `MARCEL_ADOPT_TOKEN` secret of its environment.

- **The token** is `mra_` plus 32 random bytes in hex. The hub keeps only its sha256, in
  `<data_dir>/adopt-token.sha256` (owner-only), and reads it on every call: a new token works at
  once and the previous one stops. It is not a device token, and a device token does not work here.
- **Hidden.** Without the right token the answer is exactly the hub's 404 for an unknown address,
  even for a broken body; so is any other method on the address, or a trailing slash.
- **Nobody can lock you out.** The token is checked first, in constant time, and the right one
  always gets in. Wrong tokens are counted per caller (Cloudflare's `cf-connecting-ip`, else the
  peer address): past 10 in a minute that caller is locked out, and past 1000 in a minute from
  everyone together all wrong tokens are. A lockout answers the same 404 (a 429 would show the
  route exists) and is said once in the log. A hub with no adopt token yet refuses every call and
  says so once in its log.
- **Not Marcel's own.** A session that is a brain's or a side thread's (`brain_session_id` on a
  conversation) answers 400 `not_adoptable`.
- **Checks.** A local session id is a UUID and needs `cwd`, a full path; a cloud one is
  `session_…`. The title is one line of 1 to 120 characters, without control characters, line or
  paragraph separators or bidi controls; `url` is https; `machine` a host
  name; no other fields. Each failure is a 400 that says what to change.
- **Once per session.** The first call makes the task (201); any later call for that session, or
  for a session Marcel started itself, returns its task (200) and changes nothing. A finished
  task's transcript is not followed again.
- **What it makes.** A task with origin `adopted`, model `unknown`, already `working`, with a note
  "Handed over with /marcel-adopt from <machine> in <cwd>"; filed in the inbox of the owner's first
  agent; and an `adopted` milestone in that agent's main conversation. A local session is a `nuc`
  task. When the runner lists it (`GET /sessions`), the hub follows its transcript at once
  (`RunnerWatch.follow`) and its state from the runner's events, like its own tasks: `tracked:
  live`. Otherwise (another computer, the cloud, or the runner out of reach) it is `links_only`: the
  task keeps its link (`cloud_url`, or `remote_url` when given) and nothing more.

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
  project it is about. An `artifact_id` ("Ask about this" on an item, B-21) must be one of the
  agent's items (404 `artifact_not_found` otherwise, and nothing is stored); it is stored on the
  message and passed to the brain on its `user_message`.
- `GET /api/messages/{id}`: one message in any of your conversations, for a
  `marcel://message/<id>` deep link (`conversation_id` says main or side thread). Someone else's
  message answers 404 `message_not_found`, like one that does not exist.
- `POST /api/messages/{id}/thread`: opens the side thread on a message (201), or returns the one it
  already has (200). A thread on a milestone or approval gets that message's `task_id`, and
  belongs to that task (see "Task threads" below).
- `POST /api/conversations/{id}/send-to-main`: from a side thread, `{"message_id"}` quotes one of
  its messages into the main conversation (`kind: quote`); `{"text"}` posts your own text there.

Other code posts messages (Marcel's answers, milestones) with `conversations.post_message(...)`,
which also sends the `message.created` event.

**What a message answers (B-41, S-18.1).** `reply_to_message_id` names the owner's message an
agent message answers, so the app can quote it when it is not their latest:
- the brain's `reply` keeps its `in_reply_to` when that is a user message in the same conversation;
  anything else is stored without it, with a log line, and the reply is never refused;
- every agent message posted for a task (milestones, approval cards, items) gets the message that
  started the task (`Task.parent_message_id`) when that is a user message in the same conversation.

### Task threads

A side thread opened on a task's milestone or approval card (`Conversation.task_id` set) is the
task's own thread (`task_threads.py`, S-04.9).

- **Your message steers the task, never the brain.** `POST /api/conversations/{id}/messages` there
  works like `POST /api/tasks/{id}/messages`: the task gets a `note` event and a `steer` frame for
  its session, and your message is stored in the thread (201). The brain's session gets nothing and
  the avatar does not start "thinking". The same `client_id` steers once and stores once.
- **A finished task says so.** A `stopped` or `failed` task answers 409 `task_finished` ("This task
  has stopped, so it cannot take messages. Ask Marcel to retry it instead.") and nothing is stored.
  A `done` task takes the message as a follow-up and goes back to `working`. A thread whose task is
  not on the hub answers 404 `task_not_found`.
- **The task's later milestones and approval cards show in the thread too.** They are still posted
  in the main conversation; an approval card's copy names the same `approval_id`, so answering
  either answers both; a copy goes into each thread of the task, with `client_id`
  `mirror_<original message id>`. One original makes one copy per thread: copying again returns the
  stored copy, a copy is never copied again, and a phone that reconnects with `?since=` gets each
  copy once. A copy that cannot be made is logged; the original is never lost.

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
- **Queue.** When the runner says its worker cap is full, a new task waits as `queued`
  (`queued_reason: cap`); while the usage guard holds new work, it waits with `queued_reason: usage`
  (see Usage guard). When a task finishes, fails or is stopped, the oldest waiting task starts. A
  new task never skips the queue. The queue is read from the database, so a restart keeps it.
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
  an event twice. A tail the runner answers with 503 (it could not ask `claude` just then) is
  retried after 0.5 s, 1, 2 … up to 30 s (the delay starts again at 0.5 s after a stream that gave
  events); after 5 minutes of 503s the hub logs one warning (`The transcript of task … has not been
  readable for 5 minutes`) and keeps trying. A 404 or 400 ends that tail with a log line
  (`Stopped reading the transcript of task …`). When a transcript file starts over (truncated or
  replaced, so its offsets go backwards), the hub reads it again from the start and stores what is
  new. An event that repeats one it already has (same type, time and data, matched in order, one
  for one) is not stored twice; how far that re-read got is saved with the offsets, so a hub
  restart in the middle of it is safe too.
- **Runner down.** When the runner does not answer, every NUC task that was live goes to `unknown`,
  and one "something broke" alert is pushed (see Push notifications). The hub
  retries after 0.5 s, then 1, 2, 4 … up to 30 s. When the runner answers again the alert is
  cleared and queued tasks start if there is room. The hub reconnects with `Last-Event-ID`; the
  runner sends the events it missed, then always closes with a fresh view (one `session.state`
  per session), so every task takes the state the runner reports and its transcript tail
  restarts, even when its session did not change during the outage.
- **Runner warnings.** `runner.warning` raises the "The NUC has a problem" alert;
  `runner.recovered` clears it (and the phone gets "The NUC is fine again").
- **Bad input.** An event that does not match the contract is logged (`Ignored a bad runner event`)
  and dropped; the stream carries on.

## Cloud tasks

`src/marcel_hub/cloud.py` (S-07.1). A `cloud` task runs in a claude.ai/code cloud session the
runner opens (`POST /cloud/sessions`, `claude --cloud` on a pty). The NUC cannot watch a cloud
session (SP3): all the hub learns is what the worker reports over HTTPS, plus a deadline watchdog.
The hub must be up behind `marcel-bot.com` before any cloud worker runs.

- **Spawn.** The registry hands cloud tasks to `CloudTasks` (`CloudRouter`); the usage guard holds
  them like any task, the NUC's worker cap never does. The create carries `repo: {url}`
  (`https://github.com/<owner/name>`) and a prompt that starts with the worker protocol: the report
  URL `https://marcel-bot.com/api/session/report`, the task's token, the body of each report kind
  (`started`, `progress` with `next`, `artifact`, `note`, `done`, `failed`), the repo, and what
  Marcel expects back (a PR on the repo as a `pr` artifact) by when. The task's title and text come last,
  between two lines marked with a new random word, introduced as data that does not change how the
  worker reports. The task stores the session id and `cloud_url` (`https://claude.ai/code/<id>`).
  The stored opening prompt is the task text alone; the token is never in a task event or a log.
- **Could not start.** The runner's `503` (`claude_timeout`, `claude_unavailable`, `git_timeout`)
  or `422` (`cloud_unparsed`, `clone_failed`), a created session whose URL is not on claude.ai, or
  an unreachable runner: the task goes `failed` with "Could not start:" and the reason in plain
  words, e.g. "Claude Code did not open the cloud session within a minute, so nothing is running
  for this task." Nothing is followed and the token is spent.
- **Report tokens.** `mrt_` plus 32 random bytes in hex, one per spawn and one per follow-up. Only
  the sha256 is kept, in `<data_dir>/cloud-tokens.json` (owner-only, written to a new file and
  renamed over the old one, so a crash never leaves it half-written). A token is good for its one
  task, for 48 hours, and is spent by the task's `done` or `failed` report or a stop;
  `CloudTasks.task_for_token` also refuses any token of a finished task.
- **Reports.** `CloudTasks.report(task_id, body)` checks a `session-api.yaml` `Report` (a cloud
  worker hands over `pr`, `branch` and `link` artifacts only) and stores it exactly as a NUC
  worker's channel `report`: progress events with `next`, artifacts with their cards, the B-06
  milestone. A body that does not match raises `BadReport` (400 at the route); one the gateway
  refuses raises `ReportRefused` with its code (`too_many_notes` is 409). `POST
  /api/session/report` (S-07.2) calls `task_for_token`, then `report`.
- **Watchdog.** Once a minute: a cloud task Marcel started (an adopted one was never told to
  report) still `starting` or `working` past its `expected_by` (two hours after it was created
  when the brain gave none), that has not reported in the last 30 minutes, goes `silent` ("No report from the cloud worker …; it was expected by …"). The notifier
  pushes "A task went quiet" once; a silent task is not looked at again. A later report brings it
  back to `working` (or straight to `done` / `failed`).
- **Follow-ups.** A steer to a cloud task (app or brain) goes out with `POST
  /cloud/sessions/{id}/messages`, with a new report token. A `done` task moves back to `working`
  before the send, so a report that arrives while the send is still in flight is taken. A send
  that fails is shown in the task's thread ("The follow-up did not reach the cloud session: …"),
  and a task the follow-up had revived goes back to `done`.
- **Stop.** The runner cannot stop a cloud session. Stopping the task stops following it and
  spends its tokens; the session may run on until it ends on claude.ai.

## Cloud reports: `POST /api/session/report`

Where a cloud worker reports, with the token from its prompt (`contracts/session-api.yaml`,
S-07.2). Code: `src/marcel_hub/report_api.py`; it only checks the caller and hands the report to
`CloudTasks` above, so a report is stored exactly as a NUC worker's channel `report`.

- **The token first.** It must be a `mrt_` token of a task that is still running, minted less than
  48 hours ago. Otherwise (no token, a wrong one, an expired one, or one of a task that is `done`,
  `failed` or `stopped`) the answer is exactly the hub's 404 for an unknown address, even for a
  broken body. So is any other method on the address, or a trailing slash. A report lands on the
  token's own task; the body cannot name another (an unknown field is a 400).
- **Too many wrong tokens.** Counted per caller (Cloudflare's `cf-connecting-ip`, else the peer):
  past 10 in a minute that caller is locked out, and past 1000 in a minute from everyone together
  all wrong tokens are. A lockout answers the same 404, as adopt does (a 429 would show the route
  exists; session-api 0.1.5), and is said once in the log. A good token always gets in.
- **Too many reports.** A task may send 30 reports a minute; the next gets 429 `too_many_reports`,
  the only 429 this route sends.
- **Size.** A body over 64 kB is a 400 `bad_report`, refused before it is parsed.
- **A retry is taken once.** The contract has no report id, so the same report (the same JSON, key
  order aside) from the same task within 10 minutes is answered 202 and stores nothing, also when
  both arrive at once. A `done` or `failed` report spends the token, so its retry is a 404. The hub
  forgets recent reports when it restarts.
- **Answers.** 202 `{task_id}`. 400 `bad_note`, `bad_next` or `bad_report` say what to change; 409
  `too_many_notes` / `notes_full` and 503 `memory_unavailable` tell the worker to put the fact in
  its done summary. Nothing is stored for a refused report, and it can be sent again; so can a
  report the hub failed to store for any other reason (a 500).

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
  `pr_status_from_gh` maps GitHub's REST answers to them. When one of them changed it sends
  `artifact.updated` with the current version (a refresh is not a new version); when nothing
  changed it sends nothing. A PR starts as `open`. See [PR status refresh](#pr-status-refresh).
- **Project.** An item belongs to its task's project, and moves with the task (the app gets
  `artifact.updated` for each, with its current version). One with no task goes to the inbox of
  the hub's first agent.
- **Pinning an item** (B-21, B-31). `POST /api/artifacts/{id}/move` with `{"project_id"}` files one
  item under another live project of the same agent; its `project_id` changes (it keeps its task
  and versions) and it is marked `pinned`. **An item follows its task only until it is pinned:** a
  task move takes along the items never moved on their own and leaves pinned ones where they are. The app gets `artifact.moved`, `artifact.updated`, then `project.updated` for
  the new and the old project, and both boards are rebuilt. Moving it to the project it is in
  answers 200 and sends nothing. A board stays with its project (409 `artifact_is_board`), an
  archived project takes nothing (409 `project_archived`), another agent's or a missing project is
  404 `project_not_found`, and someone else's item is 404 `artifact_not_found`. No brain turn.
- **Migration 0006** added `artifact_version` (version 1 for every existing artifact), the current
  version on `artifact`, `artifact_id` on `message`, and a board for every project. Its downgrade
  drops boards and `artifact` messages and keeps every other row. Older code reads only `<id>`, so
  after a downgrade an item that had reached version 2 or later shows its version 1 bytes.
- **Migration 0007** added `artifact.pinned` (false for every existing item). Its downgrade drops
  the column, so pinned items follow their task again.

## PR status refresh

`src/marcel_hub/github.py` (S-04.12, ADR-002) keeps PR cards in step with GitHub. Read-only: it
only ever sends `GET` requests to `https://api.github.com`.

- **When.** A PR artifact is refreshed as soon as it is made (or gets a new version), then every
  5 minutes while it is `open` or `draft`. The refresh that sees it `merged` or `closed` is its
  last. At start the hub finds every open PR artifact again, so a restart loses none. The loop
  looks for due PRs every 5 s and stops cleanly with the hub.
- **What it asks.** Per refresh, three calls: the pull request, its head commit's check runs and
  its reviews, mapped by `pr_status_from_gh`.
- **Untrusted URLs.** A PR URL comes from a worker. Only `https://github.com/<owner>/<repo>/pull/<n>`
  is read, with GitHub's own rules for owner and repository names; anything else (another host, a
  port, a login, a query, `..`) is refused in plain words and no request is sent.
- **Rate limits.** A `429`, or a `403` that is a primary or secondary rate limit (`Retry-After`,
  `X-RateLimit-Remaining: 0`, or "rate limit" in GitHub's message), pauses every GitHub call (one
  token, one limit) for `Retry-After` (seconds or a date), else until `X-RateLimit-Reset`, else
  60 s; never shorter than 60 s (a reset time in the past, from clock skew, would otherwise retry
  every tick) and never longer than an hour. Any other `403` means the token is not allowed.
- **When it fails** the last known status stays and the reason, in plain words, is kept on the
  artifact as `meta_json.pr_note` and logged once: no token, a revoked token (`401`), a token not
  allowed there (`403`), a private or missing PR (`404`), GitHub in trouble (`5xx`), or GitHub out
  of reach (network down or a timeout). The card shows it as `pr.note` (cut at 200 characters), and
  the app gets `artifact.updated` when the note appears, changes or clears. It clears (`pr.note`
  null) on the next good refresh.
- **`pr.refreshed_at`** (app-api 0.6) is the time of the last good refresh, absent before the first
  one. Every good refresh sets it; a failed one leaves it. A refresh that changes nothing else
  sends no event of its own: the new time rides on the next `artifact.updated` and is in every
  read. It lives in `meta_json.pr` next to `state`, `checks` and `review`.
- **The token** is sent only in the `Authorization` header, never in a URL (httpx logs URLs), an
  error message, an artifact or a `repr`. An httpx error (which holds the request, and so the
  header) is dropped: the hub raises its own error outside the `except`, with no `__context__` or
  `__cause__`, built from the status or the kind of failure only.
  `tests/test_github.py` captures every logger at DEBUG and fails if the token shows up.
- **After each good refresh** the hooks in `PrRefresher.after_refresh` run with the artifact
  (self-changes, below). A hook that raises `PrStatusUnavailable` makes the refresh count as
  failed, so it is tried again. `watch(id)` refreshes a PR at the next pass, whatever its state.

## Self-changes

`src/marcel_hub/selfmod.py` (S-11.1, B-24, ADR-005). Marcel changes its own code only through a PR
on `shbunder/marcel`. **The owner merges it on GitHub; the hub never merges anything** (its token is
read-only, ADR-002).

- **The flag.** The store sets `self_change: true` on a `pr` artifact whose address is exactly
  `https://github.com/shbunder/marcel/pull/<n>` (owner and repository in any case), and decides it
  again when a new version changes the address. A worker cannot set or avoid it.
- **The card.** At the PR's first good refresh the hub reads the PR from GitHub and opens an
  approval card on the task that handed it in: tool `self_change`, `artifact_id` the PR,
  `summary` "Marcel wants to change itself: <PR title>", `detail` the commit and target branch and
  the diff stats ("Approve change abc1234 into main. 3 files changed: 40 lines added, 12 removed.")
  and the PR address. It is posted in the main conversation and
  pushed like any card. It has no `request_id`, so it never goes to a session, and it stays open
  when its task ends. A PR handed in without a task gets no card (so it can never be redeployed).
- **One commit per approval.** The card is for the PR's head commit when it opened. Each refresh of
  an open PR reads GitHub again: a push while the card is open expires it and opens a new one for
  the new head; a push after the owner approved opens a fresh card and says "approve the new
  version before you merge it". New commits on a denied PR open nothing.
- **Approve** (`POST /api/approvals/{id}`, the app only) records the go-ahead and posts a note:
  "open the PR and merge it on GitHub", with the PR address and `marcel://artifact/<id>`.
- **Deny** records the no, posts a note that the owner may close the PR on GitHub, and sends the
  task a `steer` (author `marcel`): do not merge, close the PR with a comment if your git
  credentials allow it (with the owner's note, if any). A stopped or failed task gets nothing.
- **Merged.** When a refresh sees the PR merged, it reads the PR from GitHub again. The hub
  redeploys only if the card was **approved in the app** (`answered_via: app`) **for this same PR
  address**, the PR went into **`main` of `shbunder/marcel`**, and the merged PR's head is **the
  commit that was approved**. It takes `merge_commit_sha` from GitHub's answer, only when GitHub
  says `merged: true` and the SHA is 40 lowercase hex characters (otherwise it asks again at the
  next refresh). `request_redeploy(sha, flag)` writes the SHA to `redeploy_flag` and nothing else;
  the host does the rest (S-11.2). The write is a new owner-only temporary file with a random name
  in the same folder, created with `O_CREAT|O_EXCL|O_NOFOLLOW` (a file or symlink planted there is
  refused, never followed), synced, then renamed over the flag. Writing the same SHA twice (the
  outcome was not saved) is harmless.
- **Merged with commits added after the approval:** no redeploy, one alert ("New commits were
  added after you approved; not redeploying."), and a fresh card for the merged head. Approving it
  redeploys the merge; denying it ends the self-change. Merged into another branch or repository:
  no redeploy and one alert naming it. Merged without that approval
  (never answered, denied, answered in a terminal, or approved for another PR address): no
  redeploy, the open card expires, and one alert: "A change to Marcel was merged without approval
  in the app; not redeploying." A flag that cannot be written is an alert too.
- **Closed** without merging: the open card expires.
- **The record.** `meta_json.self_change` on the artifact keeps `approval_id`, `url`, `head` (the
  commit the card is for), `decision`, `after_merge` (the card was opened after the merge), `outcome` (`redeploy_requested`, `not_redeployed`, `redeploy_failed`, `closed`) and `sha`. Once
  there is an outcome nothing more happens. At start the hub refreshes again every merged or closed
  self-change with no outcome yet, so a restart between the merge and the redeploy loses nothing.

## Project boards

Every project has a board (B-35): an artifact of kind `board` under the project's
`board_artifact_id`, made in the same write as the project (`models.make_board`). The hub builds its
content itself, with no brain turn. The code is `src/marcel_hub/boards.py`.

- **What it shows.** A dashboard (`contracts/dashboard.schema.json`, checked before it is stored)
  with four sections: *Waiting on you* (open approvals, and tasks that need you without a card),
  *In flight* (each running task with its state and the last step it reported), *Recent results*
  (the newest 5 tasks done in the past 7 days, each with its summary and those of its items filed
  in this project, at their version, then *Pinned here*: items pinned to this project that no
  result above lists, newest change first),
  *Upcoming* (empty: schedules are kept per agent, not per project), then Marcel's notes, the
  newest 5.
- **When.** `BoardKeeper` watches the app event log. Any task, approval or artifact change in a
  project (and a project created, renamed or moved into), and any step a worker reports with
  `report(progress)`, marks its board. A marked board is rebuilt
  10 s after the first mark, and never sooner than 10 s after its last rebuild, so a burst of
  changes makes one rebuild. Events about boards are ignored, so a rebuild never marks itself. A
  loop on the hub's event loop rebuilds what is due once a second; at start every board is marked.
- **Versions.** A rebuild stores a new version (`artifact.updated`, author "the hub") only when
  the content or title changed. A board's first content sends `artifact.created`. A board read
  before its first build is built on the spot.
- **Marcel's notes.** `BoardKeeper.board_note(project_id, text)` adds a "what this means" line
  (cut at 400 characters) and marks the board. The brain's `board_note` tool
  (`POST /brain/projects/{project}/notes`) reaches it through `boards.BrainBoards`.
- **A failed rebuild** is logged and tried once more on the next tick; if it fails again, the board
  waits for the project's next change.

Needs `jsonschema` at run time and `contracts/dashboard.schema.json` next to `hub/`.

## Agent settings: `GET` and `PATCH /api/agents/{agent_id}`

S-17.3 (B-40, B-44), `hub/src/marcel_hub/agents/`. Only the caller's own agent: another person's
answers 404 `agent_not_found`, like one that does not exist.

- `GET` gives the contract's `Agent`, with `look` once it has one and `onboarded_at` once set (`/api/me`
  lists agents the same way).
- `PATCH` takes `name` (1 to 40 characters), `look` (the whole look, checked against the contract's
  animals, colours and personalities; `animal` follows it), `onboarded: true` (sets `onboarded_at` the
  first time, then changes nothing) and the deprecated `palette` (stored only while the agent has no
  look). Anything else, or an empty body, is 400 `bad_request`.
- A change is stored with its `agent.updated` event; a request that changes nothing sends no event.
- A rename also queues a `system_event` `agent_renamed` (`data: {name}`) for the agent's brain, so it
  answers to the new name without a restart; a brain that is not connected gets it when it is. The
  triage always wakes the brain for it. The persona file (`brain/CLAUDE.md`) is not rewritten.

## Avatars: `GET /api/agents/{agent_id}/avatar` and `GET /api/avatar-previews`

S-19.2 (B-44), `hub/src/marcel_hub/avatar/`. The hub draws avatars with the Pixel Zoo
(`shared/avatar/zoo`, a path dependency in `pyproject.toml`; the Docker image copies it in and its
build fails if it cannot draw a sheet).

- **The sheet**, for the caller's own agent (another person's is 404): the sheet's JSON
  (`contracts/avatar-sheet.schema.json`) by default, its PNG when `Accept` asks for `image/png` first.
  Both carry the same `ETag`, a hash of the look and of the zoo's drawing code, which is also
  `avatar_etag` in every `agent.updated`. `If-None-Match` with it answers 304. `Cache-Control:
  private, no-cache`, so the phone keeps the sheet but asks again.
- **The look**: the agent's `look`. A new person's agent made without one gets one picked from its
  name by the zoo (`avatar.store.look_from_name`: the same name always gives the same look), and at
  start-up every agent still without one gets one (`users.give_missing_looks`): the owner's agent
  Marcel's rose giraffe, any other one from its name. Should one still have none, it draws from its
  older `animal` and `palette` (unknown ones become giraffe and `natural`), professional.
- **A preview** of any look, for the picker: `?animal=&color=&personality=&mood=` (all checked
  against the contract's lists; `mood` defaults to the personality's resting face), one 32 x 32 PNG,
  `Cache-Control: private, max-age=86400`.
- **Cached on disk** in `<data_dir>/avatars/` (`<key>.png`, `<key>.json`, `preview-<key>.png`),
  drawn once per look (a sheet takes about 0.1 s). The key also hashes the zoo's source files, so a
  changed drawing gets new keys and ETags by itself. Deleting the folder is safe: sheets are drawn
  again. A folder that cannot be written only loses the cache: each request draws afresh.
- `If-None-Match` also takes `*` and the weak form `W/"…"`.

## The avatar's state

What each agent's avatar shows (B-28), as the contract's `AgentState`. It is derived, never stored;
the code is `src/marcel_hub/agent_state.py`.

- **Order.** `needs_you` if any of the agent's tasks needs you; else `working` with
  `active_tasks` = its tasks in `working` (the real number; the app shows at most 3 spots); else
  `thinking` while a brain turn runs; else `done` for 5 s after one of its tasks reached `done`
  (not `failed` or `stopped`); else `idle`.
- **A brain turn** starts when the owner writes in the main conversation or a side thread (not a
  task's thread), and ends at the agent's next text message there (the brain's `reply`). A turn
  with no reply stops counting after 180 s, so the avatar never thinks forever.
- **Sent on change only.** `agent.state` goes through the app event log, so phones get it on
  `/api/ws` like any other event. `GET /api/me` shows the same state.
- **Timers.** A loop on the hub's event loop ends done and thinking windows (once a second). At
  start the hub compares each agent's state with the last `agent.state` in the event log and sends
  the new one if it changed while the hub was down. Tests hold the loop back (`conftest.py`), set a
  fake clock and call `AgentStates.tick()`.

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
- **Workers' notes.** `MemoryRepo.append(path, line, by, max_appends)` adds one line at the end of
  a file (made if missing) and commits it as `memory: <path> (<by>)`, under the same lock as every
  save, so notes sent at the same time never lose one another. It refuses more than one line, a
  `by` that is not `<word> <id>`, a file that would pass 1 MB (`MemoryFileTooBig`), and, with
  `max_appends`, a writer that already has that many commits (`TooManyAppends`, counted from git,
  so a restart forgets nothing). The app reads such a file's `updated_by` as `agent`.
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
- **Notes from workers** (`report(note)`, session-api 0.1.2). A worker sends one lasting fact (1 to
  1000 characters, at least one not a space (session-api 0.1.4); anything else is answered
  `call_ack` error `bad_note`, saying what to fix and
  "Put the fact in your done summary instead.", and nothing is stored). The gateway appends
  `- <date> <text> (from <task title>)` to `memory/projects/<slug>.md` of the task's own project
  (`projects/inbox.md` for one in the Inbox), with the date in the owner's time zone, and commits it
  as `memory: projects/<slug>.md (worker <task id>)`. The file name comes from the project's slug
  in the database, never from the worker, and a slug that is not a slug is refused before any path
  is made. Text and title are put on one line, so a note cannot add a heading or another item.
  No milestone, no state change, nothing in the main chat. A task may send 10 notes; the 11th is
  answered `call_ack` error `too_many_notes` ("this thread already sent 10 notes … Put the fact in
  your done summary instead."). A full notes file (1 MB) is `notes_full`; no memory folder, or git
  failing, is `memory_unavailable`. A refused note stores nothing and is not re-sent.
- **Archive** refuses the inbox (409 `project_is_inbox`) and a project with any task that is not
  done, failed or stopped (409 `project_busy`).
- **Move.** Only into a live project of the same agent (409 `project_archived`, 404
  `project_not_found`). The task's items move with it, except those the owner pinned to a project
  of their own (see *Pinning an item*). The app gets `task.moved`, `task.updated`, then
  `project.updated` for the new and the old project, then `artifact.updated` for each item that
  moved (now in the new project). Both boards are rebuilt. Moving into the same project sends
  nothing. No brain turn.
- **Counts** on each project: `working` (queued, starting, working, silent, unknown), `needs_you`,
  and `done_7d` (done, last changed in the past 7 days). `updated_at` is the latest change to the
  project or any task in it. Every `task.created`/`task.updated` is followed by `project.updated`
  for that task's project.
- **Owner only.** Someone else's project answers 404 `project_not_found`, someone else's agent
  404 `agent_not_found`, and listing it gives `[]`.
- **Board.** Made with the project under `board_artifact_id`; see *Project boards*.

## Push notifications

`src/marcel_hub/notify/` sends pushes to paired phones (B-16, B-18). Set the four `apns_*`
settings to send through Apple (`GET /api/me` then says `push.apns: true` and the app registers
its APNs token), and `ntfy_server` to send through ntfy (the app registers an ntfy topic). Both can
be set; each device gets its pushes through the one it registered with
`PUT /api/devices/{id}/push`.

| Event | Push | Opens |
|---|---|---|
| A task opens an approval | approval, urgent: "Marcel needs your OK" | the approval |
| A task needs you (a question, a login) | task, urgent: "Marcel needs you" | the task |
| A task is done / failed | task, normal: "Done: …" / "Failed: …" | the task |
| A task went silent | broke, urgent: "A task went quiet" | the task |
| The runner is unreachable / warns | broke, urgent: "Marcel cannot reach the NUC" / "The NUC has a problem" | the status screen (`marcel://status`) |
| The runner answers again / recovers | broke, normal: "The NUC is back" / "The NUC is fine again" | the same |
| Marcel's own notice (the brain's `notify` tool) | note, normal or urgent | its message |
| The usage guard starts holding new work | note, normal: "Marcel is pausing new work" | the status screen |
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
  so a second outage is pushed again. Marcel's own notices are capped at 6 per hour per agent; the
  rest are posted in the chat but not pushed, and the log says so. Every push carries a collapse key (the task id, or the cause),
  but only APNs uses it to replace the older push on the phone: on ntfy each push shows separately.
- **A failed push never breaks the hub.** Pushes are sent in the background, tried three times
  (after 2 s and 10 s), then given up and logged. A refused push (4xx other than 429) is not
  retried. When an urgent push did not arrive, one "a notification did not arrive" push goes to
  every device.
- **Secrets.** An ntfy topic lets anyone read the pushes. Logs name the device id, never the topic.
- **APNs** (`notify/apns.py`). A POST over HTTP/2 to `/3/device/<token>` on
  `api.push.apple.com` (or `api.sandbox.push.apple.com` with `apns_sandbox`). `title` and `body` go
  in `aps.alert`, the category is `aps.category` (the app gives `approval` its `approve` and `deny`
  actions), `collapse_key` is the `apns-collapse-id` header (hashed when over 64 bytes), and
  `deep_link`, `level`, `task_id` and `approval_id` sit beside `aps`. A payload over 4 KB has its
  body cut with `…`; the ids are kept.
  - **Provider token.** An ES256 JWT signed with the `.p8` key (`cryptography`), kept and made again
    every 50 minutes: Apple refuses one older than an hour and one made more often than every 20
    minutes. When Apple says it expired, the next try makes a fresh one.
  - **A token Apple no longer knows** (410 `Unregistered`, 400 `BadDeviceToken`) is removed from
    the device, unless the app registered a new one meanwhile. The device stays paired; the app
    registers again when it next opens.
  - **Busy or down.** 429, 5xx, no answer and an expired provider token are tried again like an
    ntfy push (after 2 s and 10 s), each device on its own, so other pushes are not held up. Any
    other refusal is not retried, and the log gives Apple's reason code.
  - **Secrets.** The key, its id and the team id are never logged or shown in a repr or an error.
    The device token is in the request path, so the `httpx` request log line shows
    `/3/device/<token>` instead, and the `hpack` (HTTP/2 header) logger is kept at WARNING.
  - Without the `apns_*` settings there is no APNs backend: devices that registered an APNs token
    get nothing, and the log says so.

## Schedules

`src/marcel_hub/schedule/` runs recurring work (B-22) and says when it did not run (B-23). The
app's `/api/schedules` and the brain's `schedule_*` tools reach the same `Scheduler`.

- **The store is the `schedule` table.** Each row keeps its five-field cron, its time zone (`tz`),
  `paused`, and `next_run_at` / `last_run_at`, in UTC. A restart finds every schedule where it was.
  No APScheduler: see `project/decisions/ADR-004-scheduler-without-apscheduler.md`.
- **Times are wall-clock times in `tz`** (`cron.py`). Each matching time fires once, across the
  clock changes: a time skipped in spring (02:30 when 02:00 jumps to 03:00) fires once at 03:30; a
  time that happens twice in autumn fires at its first occurrence only.
- **Firing.** A loop looks every 30 seconds (sooner when a fire is due). A due schedule moves its
  `next_run_at` on first, then: `brain` sends a `system_event` `schedule_fired` (the prompt in
  `data`) to the brain of the agent's main conversation; `nuc` / `cloud` spawns a worker task
  (`origin: schedule`, `schedule_id`) through the task registry, so the usage guard and the worker
  cap queue it rather than drop it. The prompt is the owner's text, handed to Claude as text.
- **Missed fires are not made up.** A fire more than 10 minutes late (the hub was down) is skipped
  and the schedule moves on to its next time. Everything one look found missed makes one message in
  the main conversation and one push (category `note`), e.g. "Marcel was off when 2 scheduled jobs
  were due, so they did not run: “Garden check” (3 times), “Weekly report”. …".
- **"Did not run".** With `deadline_minutes`, every fire is watched on its own until its run
  starts: the task leaves `queued` (a task that failed without ever getting a session, e.g. the
  runner was down, did not start), or the brain's channel takes the event. If that has not
  happened by the deadline, one message and one push say so and why (waiting for a worker, held
  by the usage guard, the brain has not picked it up, or it could not start). Pausing or deleting
  a schedule drops its watches: a paused job is never reported late. Watches live in
  `<data_dir>/scheduler.json`, so a restart keeps watching and never says it twice; an unreadable
  file is logged and the scheduler starts without watches.
- **Pause, resume, change, delete** (`PATCH` / `DELETE /api/schedules/{id}`). A paused schedule has
  no `next_run_at`; resuming starts at the next time after now, and the paused stretch is not
  reported as missed. Built-in schedules (the digests, S-09.2) can be paused or changed, not
  deleted (409 `schedule_builtin`). Only the owner of a schedule's agent sees it (404
  `schedule_not_found` for anyone else). A stored cron that can no longer be read pauses its
  schedule, with a warning in the log.
- **Shutdown.** Stopping the hub lets a look in progress finish (a spawn is never cut in half),
  then ends the loop.

## Digests

`src/marcel_hub/digest.py` (S-09.2, B-17). The hub gathers the facts; the brain writes the digest
with the plugin's `digest` skill. The hub never writes a digest itself.

- **Four built-in schedules** per agent, in the `tz` setting, made at start (and within 30 seconds
  for an agent made later): *Morning digest* 07:00, *Evening digest* 21:00, *Weekly review*
  Sunday 18:00, *Usage report* 21:00. They run in the brain, can be paused or changed in the app,
  and cannot be deleted. Their ids are `sch_digest_<digest>_<agent id>`. When `tz` changes, the
  ones the owner has not edited (cron and time zone still as the hub set them) move to it at start.
- **The usage report rides with the evening digest** while both run at the same time and neither
  is paused: the evening bundle carries `usage` and the usage report sends nothing of its own.
  Moved to another time it is sent on its own (`digest: usage`); paused, the evening digest leaves
  usage out.
- **The facts bundle**, read when a digest fires: `approvals` (open cards), `questions` (threads
  waiting for an answer), `in_flight`, `done`, `failures`, `prs_ready` (open, checks passing, no
  changes asked; an approving review is not needed, as the owner reviews by merging),
  `prs_merged`, `artifacts` (new items), `stale` (no activity for 3 days), `schedules_today` (the
  other schedules' fires until midnight) and, in the evening digest, the weekly review and a lone
  usage report, `usage` (the plan's windows and usage by task since the last report). Today means
  since local midnight; the weekly review covers the last 7 days. Every list keeps its first 10,
  with `count` and `more`; every text is one line of at most 160 characters. Times are ISO in the
  owner's time zone. No settings, no approval details (the command an approval would run), and in
  titles and summaries token-shaped words (`ghp_…`, `sk-…`, `Bearer …`, device tokens `mrc_…`, any
  run of 32+ hex characters such as the hub secret) become `[hidden]`, as does the value of
  `NAME=value` when NAME ends in secret, token, key or password (`MARCEL_HUB_SECRET=[hidden]`).
- **Delivery.** The bundle goes as a `digest_due` system event (`data: {digest, title, facts,
  digest_id, artifact_id?}`) through the channel outbox, so it waits there while the brain
  restarts. `digest_id` (`dig_…`) names this one event.
- **The digest board.** The brain publishes with the event's `title` ("Evening digest — Monday 12
  October") and `in_reply_to: <digest_id>` (`POST /brain/artifacts`, brain-api 0.1.1). The hub
  keeps `in_reply_to` on the version (`artifact_version.in_reply_to`, migration 0008) and takes
  that version as the digest, whatever its title or project. Without it, the hub takes a new
  version of the board, or a doc by Marcel in the inbox with exactly that title, unless that
  version answers another digest; any other doc published meanwhile is ignored. An
  `in_reply_to` the hub never sent this agent's brain (or one older than the last 50 answered)
  is refused 404 `digest_not_found`, and nothing is stored. That includes a digest the hub gave up
  waiting for: once the next digest of its kind was sent, or 8 days passed (`LATE_FOR`), its id is
  forgotten. The brain then publishes without `in_reply_to`, and the title match takes it if the
  hub is still waiting. The first one
  becomes the agent's digest board; later events name it (`artifact_id`), so each digest is a new
  version.
  When it gets a new version, the hub posts it as a card in the main conversation and pushes it
  (category `digest`, the version's summary as the text). The brain adds its one-line headline.
- **When the brain does not publish within 30 minutes** (down, busy or failed), the hub logs a
  warning and posts one plain line of its own, with a push, made from the bundle's numbers: "The
  evening digest was not written: Marcel did not publish it within 30 minutes. In short: 2 things
  wait for you; 3 threads are working." The owner is never left guessing, and nothing waits on the
  brain. A digest published later (until the next digest of its kind) is still recognised: it
  becomes the board and gets its card, without a second push.
- **Restart.** The board ids, the digests waiting to be published, the answered digest ids and the
  usage baselines are kept
  in `<data_dir>/digests.json`; an unreadable file is logged and digests start afresh.
- **Live test** (`tests/test_digest_live.py`, marked `live`, never in a gate): on the NUC, with
  `MARCEL_LIVE_URL`, `MARCEL_LIVE_TOKEN` (a paired device's token) and `MARCEL_LIVE_AGENT`, it moves
  the morning digest to the next minute, waits for its card in the main conversation, and puts
  the time back: `make -C hub test-live`.

## Usage guard

`src/marcel_hub/usage.py` keeps Marcel inside the Claude Max plan (B-19). It reads the runner's
`GET /usage` every 5 minutes, and every minute once a window is at 70 % or more or while it holds.

- **When it holds.** A fresh reading at or above 90 % of the five-hour window, or 95 % of the week
  (for a window that has not reset yet), or a usage-limit hit. A stale reading never starts a hold.
- **What holding does.** Every new spawn (NUC or cloud: the plan is shared) waits as `queued` with
  `queued_reason: usage`, and the queue is not drained. Running tasks carry on, and so does a
  follow-up sent to a task that is already done: it goes back to `working` and spends usage.
- **One push per crossing.** When a hold starts, one push: "Marcel is pausing new work until 14:00:
  the subscription is at 91 %." Nothing for further polls, for queued spawns, or for a hold that
  ends and starts again for the same reset time. A hold whose reset is unknown (it lasts 30
  minutes) pushes at most once per 5 hours, or again after a fresh reading. Times are in the `tz`
  setting's time zone (`MARCEL_TZ`), never the container's.
- **When it lets go.** At the window's reset time, or when a fresh reading shows every window
  below its limit (not after a limit hit: that waits for the reset). The queue then starts oldest
  first through the task registry, so the worker cap still applies; a task that now waits for a
  slot says `queued_reason: cap`.
- **Fail-open.** After 30 minutes without a fresh reading while it holds, it lets go and logs a
  warning (`The usage guard lets new work start again: it has had no fresh usage reading…`). The
  phone is not pushed: the owner already knows work paused, the tasks that start show it resumed,
  and a runner that cannot be reached raises its own push.
- **Limit hits** (SP4). A transcript `error` with `usage_limit`, or a task going to `needs_you`
  because "The usage limit is reached" (a session `blocked` on the limit), starts a hold whatever the
  reading says: until the reset of the window over its limit, or of the soonest window when none
  is; for 30 minutes when no reset is known. A session blocked as "rate limited" counts as a hit
  too (SP4 cannot tell it from the plan limit), so even a passing rate limit holds new work until
  that reset, usually the five-hour window's. A task's session counts once until the task moves on.
  The first raw row of a hit is logged at WARNING, as no live hit has been recorded yet.
- **Records.** Fresh readings are stored as `usage_snapshot` rows when they change. Per-task usage
  is an **estimate**: the transcripts carry no token counts, so each rise of the five-hour window
  is split evenly over the tasks then running (`other` when none ran); `guard.usage_by_task()`
  gives it for a later usage report.
- **Restart.** The hold, the last push and the attribution live in `<data_dir>/usage-guard.json`,
  so a restart keeps holding and does not push again; the queue is in the database. A file that
  cannot be read or is not a JSON object is logged and the guard starts clean, without a hold.
- **`GET /api/usage`** answers the contract's `Usage` (the readings, `stale`, and `guard`: `active`,
  `reason`, `queued`), and `usage.updated` goes to the app when it changes. Before the first reading
  (or with no runner) it answers 503 `no_reading`. The brain's `usage_get` gets the same, once a
  runner is set up.

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
  server retries for two minutes. One exception: a new brain started by a rollover is not the
  conversation's brain yet, and its hello is the hand-over (see "The brain: keep-alive and
  rollover"). A `reply` from a session that is no longer its conversation's (the brain a rollover
  replaced) is refused with `call_ack.error` (`replaced`), so only one brain ever answers. Its
  text is not lost: the conversation's current session gets it once, as a `rollover` system event
  with `data: {unsent_reply, in_reply_to?}`, and decides whether it still needs saying. A plugin older than 0.1.0 is rejected `plugin_too_old`.
- **A session acts only for itself.** A worker's `report` lands on its own task; a brain's or side
  thread's `reply` in its own conversation. A worker's `reply` and a brain's `report` are stored
  nowhere, logged, and answered `call_ack` with `error` (`not_allowed`), so the session drops the
  call instead of re-sending it after every reconnect. A session can never answer a prompt.
- **Brain and side-thread prompts are denied.** An approval card belongs to a task, so the brain
  or a side thread cannot get one (`project/contract-requests/S-04.6.md`). Rather than wait at its
  prompt for ever, the session gets `permission_decision deny` at once, and the log says which
  tool it asked for. A re-sent prompt is denied again.
- **Calls.** Each stored `reply` or `report` is answered `call_ack`; a repeated `call_id` is not
  stored again and is acked with `duplicate: true`. A report that asks for a new version of an
  artifact it may not update is refused whole: `call_ack` with `error` (`unknown_artifact`), and
  nothing of it is stored or recorded. A `reply` or `report` that breaks the contract (a missing
  field, an unknown one, a wrong type, an unknown report kind) but names its `call_id` is answered
  `call_ack` with `error` (`bad_reply` / `bad_report`) saying what is wrong, so the session's tool
  returns it and the server does not re-send it for ever; only a frame with no usable `call_id` is
  logged and dropped. A call the hub fails to store is not acked, so the server sends it again
  after a reconnect.
- **Reports.** `started` and `progress` become `progress` task events (and move the task to
  `working`); a `progress` report's `next` (the steps the worker still plans) is copied into its
  event as `next`. A `next` past the contract (more than 20 steps, an empty step, one over 200
  characters, or not a list of text) refuses the whole report with `call_ack.error` (`bad_next`)
  saying what to fix: it is never cut short, and nothing of the report is stored. `next` on any
  other kind of report is ignored, unread (the contract gives it meaning only for `progress`).
  Artifacts go to the artifact store (files are copied from the workers' folder) and get an
  `artifact` event; one the store refuses becomes an `error` event saying why, and the rest
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
  it. A message sent from a project room carries its `project_id` on the `user_message`, so the
  brain knows which project it is about; one sent with "Ask about this" carries the item's
  `artifact_id`, so the brain can read it. A conversation with no session yet (`brain_session_id` unset) gets nothing queued: the
  session that starts for it reads the conversation. A thread that belongs to a task never goes to
  a session: it steers its task (see "Task threads"). Where each one goes is the triage's call
  (see "Waking the brain").

### Approvals

A worker's permission prompt becomes an approval card: the task goes to `needs_you`, a
`permission` task event, an `approval` message in the main conversation, `approval.created` on
the phone, and an approval push. The same `request_id` on the same task never opens a
second card, whatever state the card is in.

- `GET /api/approvals?state=` lists your cards, open ones first.
- `GET /api/approvals/{id}` reads one card in any state, for a `marcel://approval/<id>` deep link
  (404 `approval_not_found` for a missing card or someone else's).
- `POST /api/approvals/{id}` with `{"decision": "approve" | "deny", "note"?}` answers a card and
  sends the session its `permission_decision`. A denial's note follows as a `steer`. A
  self-change card (tool `self_change`, no `request_id`) is answered by `selfmod.py` instead
  ("Self-changes"). Only the
  owner of the task's agent sees or answers a card (404 for anyone else). A card that is no
  longer open answers 409 with the card as it stands.
- **Answered in a terminal.** When the runner shows the session left the permission prompt and the
  transcript has the tool's result, the card becomes `approved` (or `denied`, when the result says
  `denied: true`) with `answered_via: terminal`. The first answer wins, from the app or the
  terminal. The answer is matched by tool name: the first result of a call to the same tool that
  arrived after the card opened. Two prompts for the same tool at once can be matched the wrong
  way round (a known limit).
- **Expired.** When a task ends (`done`, `failed` or `stopped`) with an open card, the card is
  resolved from the transcript if it can be, and otherwise becomes `expired`. A self-change card
  stays open: it closes with its PR. At start the hub does
  the same for cards left open while it was down.
- Every change to a card sends `approval.resolved`.

### Side threads

Every other side thread runs in its own fork of the brain (`side.py`, S-06.3, B-08): a second
`claude` session that knows the main chat up to the moment it was forked, and answers only in its
thread.

- **Opening a thread forks the brain.** `POST /api/messages/{id}/thread` (not on a task's card)
  makes the hub ask the runner to fork the main brain (`POST /sessions/{brain}/fork`, role `side`,
  name `side-<conversation id>`, same model and permission mode as the brain). The fork's first
  prompt says it is a side thread and quotes the message it was opened on. Its session id is stored
  as the thread's `brain_session_id`.
- **What you write there reaches the fork only.** The channel gateway sends a thread's messages to
  the session stored on that thread, never to the main brain, and takes a `reply` from the fork
  into the thread. A message written before the fork runs (or while it is stopped) waits in the
  Outbox and is delivered when it says hello: nothing is lost.
- **"Send to main" is the way back.** `POST /api/conversations/{id}/send-to-main` posts a quote (or
  your own text) in the main conversation as your message, so the main brain gets it like anything
  else you write there.
- **Idle forks stop after 30 minutes.** A fork whose thread has been quiet for 30 minutes, that is
  between turns and has nothing waiting for it, is stopped. Its id stays on the thread. The next
  message wakes the same session (`runner.resume`, with its own history); if that session cannot be
  woken, the brain is forked afresh and the new fork is given the thread so far.
- **At most 3 forks run at once.** Each one is a whole `claude` process on the NUC and the runner's
  worker cap never counts it, so the hub caps them. A fourth stops the fork that has been quiet the
  longest. When all three are in a turn, the new thread waits, says so once ("Marcel is busy in 3
  other side threads right now…"), and starts as soon as one is free.
- **A fork that fails says so in the thread.** The first failure posts the runner's reason in the
  thread as a note from the hub. The hub tries 3 times in all (after 15 s, then 1 min), then posts
  a last note and stops until you write there again. A main brain that is not running counts as a
  failure too.
- **A brain rollover leaves forks alone.** A fork is its own session: when the nightly rollover
  stops the old brain, its forks keep running until they go idle, and a later fork comes from the
  new brain. The brain keeper only counts sessions with the role `brain`.
- Like the brain, forks are started through the runner directly: the usage guard never holds them
  back. A turn in a side thread shows the avatar "thinking", like one in the main conversation.

## Waking the brain: triage

Every brain turn spends plan usage, so the hub wakes the brain only when a decision needs it
(S-06.5, `plan/02-architecture.md` § Token economy). `src/marcel_hub/triage.py` is the one place
that decides: `wake_policy` is the table below, and `RulesTriage` (plain code, no model) is the
`Triage` the hub uses. A decision is one of `BrainTurn` (wake the main brain, or a side thread's
fork), `Direct(target)` (straight to a task, no brain turn) or `Store` (keep it and show it).

| Event | Decision |
|---|---|
| Your message in the main chat | brain turn (a short "status?" question is logged as `status_question`) |
| Your message in a side thread | turn in that thread's fork |
| Your message in a task's thread; approve / deny | straight to the task |
| Worker started, progress, artifact | stored |
| Worker done | stored; the milestone carries the worker's summary |
| Artifact published or updated, board rebuilt | stored |
| Worker failed, gone silent, or asked a question (not a permission prompt) | brain turn: a `task_failed`, `task_silent` or `task_question` system event |
| Digest due, a `brain` schedule, a hand-back, a rollover's hand-over note | brain turn |

- **Every system event for a conversation is checked.** `ChannelOutbox.put` asks the triage before
  it queues one, whoever writes it (the gateway, the registry's hand-back, the scheduler, the
  digests). One the triage does not wake the brain for is logged and not queued (`put` returns 0).
- **A question** is a task moving to `needs_you` with the runner's reason "The session asked a
  question". A permission prompt has its approval card instead, and a usage limit waits for the
  window to reset; neither wakes the brain.
- **The decision log.** Every decision is one line in `<data_dir>/triage.log` (rotated at 10 MB,
  five old files kept), and on the `marcel_hub.triage` logger: a plain sentence, ` | `, then the
  decision as JSON (`at`, `event`, `decision`, `brain_turn`, `target`, `reason`, `rule`,
  `agent_id`, `conversation_id`, `task_id`, `by`). S-16.2 counts brain turns by `rule`, e.g.
  `sed 's/.* | //' triage.log | jq -r 'select(.brain_turn) | .rule' | sort | uniq -c`.
  A file rather than a table: it needs no migration, nothing in the hub reads it back, and it does
  not depend on how the server's own logging is set up (by default it drops INFO lines).
- **Not yet decided at run time:** a task moved to another project (no event says so) and a steer
  sent with `POST /api/tasks/{id}/messages` (it goes to the task by its route). Neither ever woke
  the brain. The hub's watchdog that marks a task `silent` is not built yet; when it is, the
  brain is told through the same path.

## The brain: keep-alive and rollover

`brain.py` keeps exactly one brain session per agent (S-06.2, B-01). It runs only when `brain_cwd`
is set and the hub has a runner.

- **When it checks.** At start, after every runner event and every runner (re)connect, and every
  2 seconds. It lists the runner's sessions and looks at each agent's brain, the
  `brain_session_id` on the main conversation. Checks run one at a time, so a burst of runner
  events starts one brain, not several.
- **A dead brain is replaced at once.** Gone from the runner's list, `failed`, `stopped`, or
  `done` with no process left. The new session id is stored on the main conversation right away.
  Messages you send while no brain runs wait in the Outbox and reach the new brain when its
  channel says hello. A replacement shows in Marcel's notebook as a `brain.rollover` with reason
  `crashed`.
- **Nothing is started on a guess.** A runner that cannot be reached, a state the hub does not
  know, or a session the runner started less than a minute ago but does not list yet all count as
  "still running".
- **Backoff and one alert.** A spawn that fails is tried again after 5 s, 15 s, 30 s, 1 min, 2 min,
  then every 5 min. After 3 failures in a row you get one "Marcel cannot start" push with the
  runner's reason; it clears when a brain starts.
- **Rollover.** Every night at 04:00 in the owner's time zone (the `tz` setting, `MARCEL_TZ`,
  default `Europe/Brussels`; the time is worked out from the calendar day, so clock changes do not
  move it), the hub starts a fresh
  brain next to the old one. The owner can ask for one from the app
  (`POST /api/agents/{agent_id}/brain/rollover`: 202, then `brain.rollover` once it has taken
  over; 409 `rollover_running` while one is asked for or waiting for its hello; 503
  `runner_unreachable` when the runner does not answer or the hub runs no brain, and nothing
  changes). The old brain keeps answering until the new one says hello on the
  channel, and only once the old one is between turns (a hello while it is mid-turn is refused,
  and the channel server tries again 5 s later). At the hello the new id goes on the conversation,
  the old brain's socket is closed and its session stopped, and `brain.rollover` (time, reason,
  what was kept) is stored and sent to the app. A new brain that dies or does not say hello within
  150 s is stopped, the old one stays, and the rollover is tried again after the backoff. So there
  is never no brain, and never two answering.
- **What a new brain starts with.** Its first prompt is written by the hub from its own records,
  with no model call: `MEMORY.md` (the first 8 KB), the open threads (title, state, where, project),
  and the main chat of the last 24 hours (up to 60 messages, milestones included, each line cut at
  400 characters). `kept` says the same in one line, e.g. "Your notes, the last 40 messages of the
  past day and 3 open threads."
- **Left-over brains.** Any other live session the runner lists as this agent's brain (named
  `brain-<agent id>`), for example one a hub restart cut off in the middle of a rollover, is
  stopped.
- **Never held back.** The brain is started through the runner directly, not through the task
  registry, so the worker cap and the usage guard never queue it.
- **Known before its spawn returns.** The runner answers a spawn only once the new session's
  channel is registered, but the session is running by then: its `SessionStart` hook and first tool
  calls reach `/brain` first. While a fresh brain is starting, a `/brain` call from a session the hub
  does not know makes the hub ask the runner; if the runner lists that session under the agent's
  brain name (`brain-<agent id>`), it is written on the conversation at once (`BrainKeeper.claim`).
  A rollover's new brain is not claimed this way: it takes over at its hello, as before.
- **When the context fills up.** Off by default since S-06.8 (ADR-010): Claude Code compacts the
  brain's context itself, and the plugin's `SessionStart` (`compact`) hook puts back `MEMORY.md` and
  the open threads (`brain/README.md`). With `brain_rollover_on_context` on, the hub reads the current brain's transcript from the runner
  (`GET /sessions/{id}/tail`: first the latest turn already there, then each new one). At every
  `turn_end` it adds up `usage` (input + cache creation + cache read + output tokens, what the
  session carries into its next turn) and compares it with the model's context window (the
  `brain_context_window` setting, 200 000 by default; 1 000 000 for a `[1m]` model). Past 70 % it
  asks for a rollover with reason `context_full`, between turns like any other. It asks once:
  while a rollover is asked for or its new brain waits to say hello, more full turns ask for
  nothing, and a late turn of a brain that was already replaced never counts.
- **History.** Every rollover (crashed, daily, context_full, manual) is also a row in
  `brain_rollover` (time, reason, what was kept, the old and new session ids), written with its
  `brain.rollover` event. `GET /api/agents/{agent_id}/brain/rollovers?limit=` (1–50, default 10)
  lists them newest first, `[]` before the first, long after the app event stream (which keeps
  the latest 2000 events) has moved on. The session ids are kept for debugging; the route shows
  the contract's `Rollover` fields only.
- **Not yet.** The 04:00 timer is the keeper's own; it can move onto the scheduler (S-09.1) later.

## Brain tools: `/brain`

`brain_api.py` is the hub side of Marcel's brain tools (`plugins/marcel/tools`, which lists every
tool and its route). The brain's MCP server calls these routes over plain HTTP on the NUC. The
routes, bodies and error codes are the contract `contracts/brain-api.yaml`;
`tests/test_contract_routes.py` fails when the routes and the contract drift apart.

- **Outside `/api`.** The tunnel forwards only `/api/*`; a request with `cf-connecting-ip` answers
  404 anyway.
- **No device token.** A call carries the hub secret (`X-Marcel-Secret`, the channel's
  `hub-secret`) and its session id (`X-Marcel-Session`). Only a session that is a conversation's
  `brain_session_id` (the brain or a side thread) gets in; a worker gets 403, anything else 401.
  `tests/test_auth_routes.py` checks these routes refuse a caller without them, like every other.
- **One agent.** Tasks and projects are looked up within the brain's agent; another agent's
  answer 404 as if they did not exist.
- `tasks_spawn` refuses `location: auto` with a hint: the brain decides where work runs. `repo` is
  `owner/name` (no leading `-`, no `..`); `message_id` must be in the brain's own conversation.
- Bodies are read no further than 64 KB (a memory write: 2 MB), answered 413 `too_large`; every
  text field has a maximum length.
- Marcel's own artifacts are filed under the project the brain names, its agent's Inbox by default,
  by "Marcel". With `artifact_id` the brain adds a new version of one of that project's items; an
  item of another project, or a board, is refused (400).
- Memory writes go through `MemoryRepo.write` (one commit, `memory: <path> (agent)`, paths that
  cannot leave the memory folder, etag conflicts answered with the current etag).
- `notify` posts the text in the main conversation and pushes it as category `note` (its `level`
  decides how loud), linking to that message. At most 6 pushes per hour per agent: past that the
  message is still posted, the push is dropped and logged, and the answer says `pushed: false`.
  `broke` stays for the hub's own system faults.
- `GET /brain/summary` (`day_summary.py`, ADR-010): the day so far, for the brain after a
  compaction (the plugin's `SessionStart` hook). From the hub's own records, no model call, in the
  `tz` setting, oldest first: the main conversation's messages today (in the rollover seed's words),
  threads started and finished today, then one "Running now:" line. At most 4 KB of UTF-8 (the
  oldest lines go first, and one line says so); a quiet day says "Nothing happened yet today."
- Parts built by other stories sit behind `app.state.brain_ports` (`boards`, `schedules`,
  `usage`, `artifacts`). `boards` is plugged in (`boards.BrainBoards`, S-04.11), `schedules` is
  the scheduler (`schedule.Scheduler`, S-09.1), and `usage` is
  the usage guard when a runner is set up (S-10.1; before its first reading it answers 503
  `not_set_up`); until a story plugs its own in, the default answers 503 `not_set_up` with
  what is missing, and stores nothing.
