# F04 — Hub core  (wave 1)

Goal: the source of truth and the app's backend. It is built and tested against a fake runner, generated from
`runner-api.yaml`, and fake channel clients.

### S-04.1  App skeleton, DB, migrations                      runs-on: cloud · size: M
depends: S-02.1   touches: hub/src/marcel_hub/{app,db,models}.py hub/migrations/ hub/tests/
do: FastAPI app factory; SQLModel models for the data model in `02-architecture.md`; Alembic
migrations; settings read from `/data/marcel.toml` plus env; `GET /health`.
done when:
- [ ] Migrations go up and down on an empty DB, and a model round-trip test passes.

### S-04.2  Device pairing and auth                           runs-on: cloud · size: M
depends: S-04.1   touches: hub/src/marcel_hub/auth/ hub/tests/
behaviours: B-27
do:
1. `marcel-hub pair` (CLI) prints a QR code with the URL and a one-time code, valid 10 minutes.
   `POST /pair` exchanges the code for a device token (32 random bytes; only the sha256 is stored).
2. Every route except `/health` and `/pair` requires a bearer token. Revoking a device works.
done when:
- [ ] No token → 401. Revoked token → 401. Expired code → 400 with a readable message.
- [ ] Removing the auth dependency from one router makes the auth test fail (the test enumerates routes).

### S-04.3  Conversation store and app WS                     runs-on: cloud · size: M
depends: S-04.2   touches: hub/src/marcel_hub/{conversations,ws}.py hub/tests/
behaviours: B-01, B-02, B-06, B-08
do:
1. Main and side conversations, with messages paginated by cursor.
2. `/ws` per device, with event fan-out to every device of the user and a replay from `?since=<event_id>`.
3. `POST /messages/{id}/thread` creates a side conversation linked to its parent message.
done when:
- [ ] Two WS clients both receive `message.created`. A reconnect with `since` replays the missed events.

### S-04.4  Task registry and state machine                   runs-on: cloud · size: M
depends: S-04.1   touches: hub/src/marcel_hub/tasks/ hub/tests/
behaviours: B-05, B-07, B-10, B-15
do:
1. Implement the task CRUD and the state machine from `contracts/task-states.md`.
2. Illegal transitions raise an error and are logged.
3. Write `task_event` rows for every runner state event and transcript event.
4. A queue holds tasks that hit `cap_reached` and starts them in FIFO order when capacity frees up.
done when:
- [ ] A table test covers every allowed and forbidden transition.
- [ ] Queue test: a cap of 1 with 3 spawns gives 1 working and 2 queued; stopping one starts the next.

### S-04.5  Runner client and event ingest                    runs-on: cloud · size: M
depends: S-04.4, S-02.2   touches: hub/src/marcel_hub/runner_client.py hub/tests/
do:
1. An async client for the runner socket.
2. A background task that consumes `/events` and per-task `tail` streams and maps them to task state, `task_event` rows,
   and app WS events.
3. Reconnect with backoff. "Runner unreachable" puts every NUC task in `unknown` and raises one
   "something broke" notification (through the F08 interface; a stub is fine here).
done when:
- [ ] The fake runner scenario drives a task from start to done. Killing the fake runner raises exactly one alert,
  and reconnecting clears it.

### S-04.6  Channel gateway                                   runs-on: cloud · size: M
depends: S-04.3, S-04.4, S-02.2   touches: hub/src/marcel_hub/channel/ hub/tests/
behaviours: B-11, B-12
do:
1. Implement the `/channel` WS per `channel-protocol.md`. Each channel connection is authenticated with a per-session token minted at spawn.
2. Route each connection:
   - **brain or side** connections: deliver the conversation's user messages; store replies as agent messages;
   - **worker** connections: deliver steer messages; `report` becomes task events, artifacts and milestones;
     `permission_request` creates an `approval` plus a card plus a push; `POST /approvals/{id}` sends
     `permission_result` back.
3. Queue messages for a session that is not connected; deliver them on hello.
done when:
- [ ] Test: approve flow end to end with a fake channel client.
- [ ] Test: an approval answered in the terminal (`permission_result` arriving first from the session) closes the card.
- [ ] Test: a message for an offline session is delivered after reconnect.

### S-04.7  Artifacts and memory API                          runs-on: cloud · size: M
depends: S-04.4   touches: hub/src/marcel_hub/{artifacts,memory}.py hub/tests/
behaviours: B-20, B-21
do:
1. Artifacts:
   - kinds pr, doc, file, dashboard, link;
   - file blobs under `/data/artifacts/`;
   - a PR artifact refreshes its CI and review status through `gh api`, run by the runner on the host, or a GitHub token on the hub (decided in F02).
2. Memory: a git working copy at `/data/memory`. `GET` the tree and files; `PUT` with an etag (409 on conflict).
   Every write commits with the message `memory: <path> (app|agent)`.
done when:
- [ ] Memory: concurrent edit → 409. The git log shows one commit per write.
- [ ] Artifacts: content served with the right content type.

### S-04.8  Agent state for the avatar                        runs-on: cloud · size: S
depends: S-04.4, S-04.6   touches: hub/src/marcel_hub/agent_state.py hub/tests/
behaviours: B-28
do: Derive `agent.state` from the brain's activity and the tasks:
- `needs_you` if any approval is open;
- else `working(n)` with n = working tasks (the app shows at most 3 glowing spots);
- else `thinking` while the brain turn is running;
- else `done` for 5 s after a completion;
- else `idle`.
Emit on change only.
done when:
- [ ] A table test covers every precedence case.

### S-04.9  Projects                                          runs-on: cloud · size: M
depends: S-04.4, S-04.7   touches: hub/src/marcel_hub/projects/ hub/migrations/ hub/tests/
behaviours: B-30, B-31, B-32, B-33, B-33a
do:
1. `project` model and migration; an `Inbox` project per agent, created on first start. `task.project_id` and
   `message.project_id` (nullable for messages).
2. App API: `GET /projects`, `POST /projects`, `PATCH /projects/{id}`, `POST /projects/{id}/archive`,
   `POST /tasks/{id}/move`. Posting a message with `project_id` stores the tag and forwards it to the brain as part
   of the channel message (B-32).
3. Project notes: creating a project creates `memory/projects/<slug>.md`; the worker spawn prompt includes the
   project's instructions and notes (with the size cap used for memory slices).
4. WS events `project.created|updated`, `task.moved`.
done when:
- [ ] Spawning without a project files the task in Inbox; moving it emits `task.moved` and fixes both boards.
- [ ] A spawned worker's prompt contains its project's notes (fake-runner test).

### S-04.10  Living artifacts and project boards               runs-on: cloud · size: M
depends: S-04.7, S-04.9   touches: hub/src/marcel_hub/artifacts/ hub/src/marcel_hub/boards.py hub/tests/
behaviours: B-34, B-35, B-36
do:
1. `artifact` + `artifact_version`. `report(artifact)` and `artifacts_publish` without an id create an artifact
   (v1); with an id they add a version. `GET /artifacts/{id}/versions`, `GET /artifacts/{id}?version=n`.
   WS `artifact.created` (v1) and `artifact.updated` (v2+).
2. The first version of an artifact produced by a task adds an `artifact` message to the main chat (the item-get
   milestone); later versions do not (the existing cards show "updated").
3. **Boards:** every project has a `board` artifact (dashboard JSON). The hub rebuilds it, debounced to once per
   10 s per project, on any task, approval or artifact change in that project: *waiting on you*, *in flight*
   (with step progress), *recent results* (with their items), *upcoming schedules*, and Marcel's notes added through
   `board_note`. A rebuild is a new version only when the content changed.
4. The morning digest (S-09.2) is published as a board artifact of the agent, not a long message.
done when:
- [ ] A version bump does not post a second item-get message; an unchanged rebuild does not create a version.
- [ ] Board test: an open approval appears under "waiting on you" and disappears when answered.
