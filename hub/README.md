# Marcel hub

State, app API, channel gateway and scheduler. Runs in Docker on the NUC. This page covers what
exists today: the app skeleton, the database, `GET /api/health`, pairing a phone, and the task
routes.

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

Not wired yet: the channel gateway. Until it exists, steer and hand-back messages wait in memory.

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
