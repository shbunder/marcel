# F01 — Repo reset and monorepo skeleton  (lead · wave 0)

Goal: a clean `main` with the v3 layout, a working gate for every lane, and the old code preserved.

### S-01.1  Preserve v2 and reset main                       runs-on: cloud · size: S
depends: —   touches: everything (one-off)
do:
1. Tag the current `main` as `v2-final` and push branch `legacy/v2` from it.
2. On `main`, remove the v2 tree except `docs/design/` (logo, palette, mascot), `.claude/hooks/`
   (kept for reference) and `plan/`. Remove the v2 `.claude/rules` and `.claude/skills`; S-01.3
   recreates them.
3. Add a README with v3's one-paragraph pitch, the logo, and a link to `plan/`.
done when:
- [ ] `git show origin/legacy/v2:src/marcel_core/main.py` works. The `v2-final` tag is
  pushed by the owner, since the cloud session's git proxy refuses tag pushes.
- [ ] `main` contains only `docs/design/`, `plan/`, `README.md`, `.gitignore`, `LICENSE`.
not in scope: deleting anything on the NUC (that is F14b).

### S-01.2  Monorepo skeleton and gates                      runs-on: cloud · size: M
depends: S-01.1   touches: hub/ runner/ plugins/ brain/ ios/ shared/ contracts/ deploy/ Makefile
do:
1. Create `hub/` and `runner/` as uv projects (`marcel_hub`, `marcel_runner`) with ruff, pyright,
   pytest, pytest-cov (`fail_under = 90`) and a `make check` target each.
2. Create the `plugins/marcel/` plugin layout (`.claude-plugin/plugin.json`, `channel/` TS package
   with `npm run check`, `tools/` uv project, `skills/`, `commands/`).
3. Create the `ios/` placeholder README; the Xcode project comes in S-12.1 on the Mac.
4. Add a root `Makefile` with `check` (every lane except iOS), `ios-check`, `test`, and `fake-claude`.
done when:
- [ ] `make check` passes on an empty skeleton, including one trivial test per lane.
- [ ] Coverage below 90 % fails the gate (proved by a deliberately untested function on a throwaway branch).

### S-01.3  CLAUDE.md, rules, board                          runs-on: cloud · size: M
depends: S-01.1   touches: CLAUDE.md .claude/ project/ plan/
do:
1. Write a short root `CLAUDE.md`:
   - the core principles (Lightweight, Generic, Human-readable, Recoverable, plus "Claude Code does the thinking");
   - the commands;
   - the lanes table from `plan/04-agent-playbook.md`.
2. Port these rules, rewritten for v3: `git-staging`, `debugging`, `docs-in-impl`, `inert-controls` (from
   Harry), `self-modification` (flag-file restart), `no-model-calls` (no SDK or API key), `contracts`.
3. Create `project/`. Convert every `plan/features/*` story into `project/stories/S-*.md` and every feature
   into `project/features/F-*.md` (a script, `scripts/plan_to_board.py`). Add `lanes.md` and a lessons README.
4. Port Harry's `scripts/board.py` (list, lanes, start, collision check on `touches:`), or keep a
   simpler version.
5. Restore the guard hook so that `CLAUDE.md`, `contracts/**` and `deploy/.env*` need an unlock.
done when:
- [ ] `make board` lists all stories with their wave and status.
- [ ] `make lanes` refuses to start a story whose `touches:` overlaps one in flight (a test proves it).
- [ ] The guard hook blocks an edit to `contracts/app-api.yaml` without the unlock flag (a test proves it).

# F02 — Contracts, fakes and mocks  (lead · wave 0)

Goal: every lane can be built and tested alone. **This feature is the keystone of the parallel
plan**, so the lead writes it carefully and the owner reviews it.

### S-02.1  Data model and app API                           runs-on: cloud · size: M
depends: S-01.2   touches: contracts/app-api.yaml contracts/events.schema.json
behaviours: B-01…B-29
do:
1. Write the OpenAPI 3.1 spec for the app:
   - `POST /pair`, `GET /me`;
   - conversations and messages (main and side, cursor pagination);
   - `POST /conversations/{id}/messages`, `POST /messages/{id}/thread` (open a side thread);
   - tasks: list, get, events with cursor, `POST /tasks/{id}/messages` (steer), `/stop`, `/handback`;
   - `POST /approvals/{id}` (approve or deny);
   - artifacts: list, get, content;
   - memory: tree, get, put with an etag;
   - schedules: CRUD, pause;
   - `GET /usage`;
   - agents: get, put palette and name;
   - devices: list, register an APNs token or ntfy topic.
2. Define WebSocket `/ws` events in a JSON Schema: `message.created`, `task.created`, `task.updated`,
   `task.event`, `approval.created`, `approval.resolved`, `artifact.created`, `agent.state`
   (`idle|thinking|working(n)|needs_you|done`), `usage.updated`.
3. Define the task state machine (states, transitions, and who triggers each) in `contracts/task-states.md`.
done when:
- [ ] The spec validates (`openapi-spec-validator`), and every schema has an example.
- [ ] A contract test in `contracts/tests/` round-trips every example through the schema.

### S-02.2  Runner API, transcript schema, channel protocol  runs-on: cloud · size: M
depends: S-01.2, SP2, SP1   touches: contracts/runner-api.yaml contracts/transcript.schema.json contracts/channel-protocol.md
do:
1. Specify the runner API: JSON over HTTP on a unix socket.
   - Operations: `spawn{role,model,cwd|repo,prompt,permission_mode,env}`, `list`, `get`, `send`, `stop`, `fork`,
     `tail` (Server-Sent Events, resumable by offset), `cloud_spawn`, `cloud_send`, `usage`, `health`.
   - Event stream: `GET /events` (SSE) for state changes.
2. Specify the normalised transcript event schema (from SP2's fixtures): `text`, `tool_call`,
   `tool_result`, `diff`, `permission`, `subagent_start/stop`, `error`, `state`.
3. Specify the channel protocol between the plugin channel server and the hub's `/channel` WS:
   - hello (role, agent_id, conversation_id | task_id, session_id);
   - inbound `user_message` / `steer` / `system_event`;
   - outbound `reply` / `report{progress|artifact|done|failed}` / `permission_request` /
     `permission_result`;
   - heartbeat;
   - auth with a per-session token minted by the hub.
done when:
- [ ] The schemas validate, and SP2 fixtures convert to transcript events in a contract test.

### S-02.3  Fake claude shim                                 runs-on: cloud · size: M
depends: S-02.2   touches: runner/tests/fake_claude/ scripts/fake-claude
do: Build a `claude` stand-in executable that implements `--bg`, `agents --json [--all]`, `stop`,
`attach` (no-op), `--cloud` and `-p --cloud`. It is driven by a scenario file (YAML) that replays
SP2 and SP3 fixtures over time and writes JSONL transcripts where real `claude` would.
done when:
- [ ] The runner's tests can run the "worker happy path", "worker needs approval", "worker fails" and
  "limit reached" scenarios with no network.

### S-02.4  Mock hub for the app                              runs-on: cloud · size: M
depends: S-02.1   touches: contracts/mock-hub/
do: Build a small FastAPI app generated from the OpenAPI examples. It has a scripted scenario player: a user
message → "started" milestone → task events → approval card → done + artifact. Run it with `make mock-hub`.
done when:
- [ ] The iOS lane can build every screen against it. A curl script walks the whole scenario.
