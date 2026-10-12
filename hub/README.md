# Marcel hub

State, app API, channel gateway and scheduler. Runs in Docker on the NUC. This page covers what
exists today: the app skeleton, the database, `GET /api/health`, pairing a phone, and the task
routes, artifacts and memory.

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
`runner.reachable`. Until the runner client exists, `runner.reachable` is `false` with a reason.
The app shows that as "the NUC is not reachable", so it is expected for now.

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

Not wired yet: the runner client and the channel gateway. Until they exist, starting or stopping a
session answers that the runner is out of reach, and steer and hand-back messages wait in memory.

## Artifacts

`/api/artifacts` lists items newest first (filters `task_id`, `kind`), reads one, and serves its
content. The code is `src/marcel_hub/artifacts.py`.

- **Handing one in.** A worker's `report` artifact (`channel.schema.json` `Artifact`) goes to
  `ArtifactStore.publish_reported`. `pr`, `branch` and `link` need an `http(s)` URL; a `pr` URL must
  end in `/pull/<n>`. `doc` (Markdown or HTML), `file` and `dashboard` need an absolute path, and the
  hub copies the bytes to `<data_dir>/artifacts/<id>`, only from inside the workers' folder
  (`files_root`) and never through a symlink out of it. Over 25 MB is refused. Until the channel
  gateway sets `files_root`, file artifacts are refused with that reason.
- **Dashboards** are checked against `contracts/dashboard.schema.json`, and every chart series must
  have one value per label. A bad one is refused with a message that says where and what to fix.
- **Trust.** HTML a worker hands in is stored with `trusted: false`. Only HTML the hub renders
  itself (`publish_hub_html`) is trusted. The hub never renders content: it serves the bytes with
  their own content type, `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`
  (`sandbox allow-scripts` for trusted HTML).
- **Recorded** on the task as an `artifact` event, and announced to the app as `artifact.created`.
- **PR status.** `refresh_pr` asks a `PrStatusSource` for the PR's state, checks and review;
  `pr_status_from_gh` maps `gh api` answers to them. Where `gh api` runs is not decided yet, so the
  hub has no source and a refresh answers that it cannot ask GitHub. A PR starts as `open`.
- Not yet: projects (`project_id` answers 400) and versions (every item is at version 1). They come
  with S-04.10 and S-04.11.

Needs `jsonschema` at run time and `contracts/dashboard.schema.json` next to `hub/`.

## Memory

Markdown files in a git working copy at `<data_dir>/memory` (B-20). The hub creates the repo on
first use. The code is `src/marcel_hub/memory.py`.

- `GET /api/memory` lists every `.md` file; `GET /api/memory/files/{path}` reads one, with its
  `ETag` (the git blob id of the file on disk, so an edit made anywhere changes it).
- `PUT` with `If-Match: <etag>` saves only if the file has not changed, else 409 with the current
  version. Without `If-Match` it creates the file, and answers 409 if it exists. An `If-Match` for a
  file that is gone answers 404. Over 1 MB is refused.
- **Every save is one git commit**, `memory: <path> (app)` from the app, `(agent)` from the brain.
  An unchanged save makes no commit. Only the saved file is committed. If git fails, the file is put
  back and the answer is 503 `memory_unavailable`.
- **Paths** must match the contract's pattern (no `..`, no leading `/`, no part starting with `.`,
  ending in `.md`), and no part may be a symlink, so nothing outside the memory folder or in
  `.git/` can be read or written. Anything else answers 400 `invalid_memory_path`.
- Needs `git` on the path. Git runs without the host's global config or hooks.
