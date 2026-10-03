# F03 — Runner (native, host-side process control)  (wave 1)

Goal: the only process that runs `claude`. It is small, has no business logic, and is fully tested against
the fake `claude` shim.

### S-03.1  Socket server and health                          runs-on: cloud · size: S
depends: S-02.2, SP6   touches: runner/src/marcel_runner/{server,health}.py runner/tests/
do: An HTTP server on a unix socket (`/run/user/$UID/marcel/runner.sock`, mode 0660, group `marcel`),
with `GET /health` reporting the version, the `claude --version` output, the cap and the active count.
done when:
- [ ] A test starts the server on a temp socket and gets health.
- [ ] A test proves that a missing `claude` binary yields `ok=false` with a human-readable reason.

### S-03.2  Spawn, list, get, stop                            runs-on: cloud · size: M
depends: S-03.1, S-02.3   touches: runner/src/marcel_runner/sessions.py runner/tests/
behaviours: B-10, B-13, B-15
do:
1. `spawn` builds the `claude --bg` command line: name, model, permission mode, the channel flag from SP1,
   plugin dir, env `MARCEL_ROLE/TASK_ID/HUB_CHANNEL_URL/CHANNEL_TOKEN`, and cwd.
2. It returns the session id. `list` and `get` come from `claude agents --json --all`, normalised.
   `stop` calls `claude stop`.
3. Enforce the **concurrency cap**: when the cap is reached, `spawn` returns `409 cap_reached`. Brain and side
   sessions do not count toward the cap.
done when:
- [ ] Each command line is asserted exactly against a golden file.
- [ ] Cap test: with the cap at 2, the third spawn fails, and it passes again after a stop. Removing the cap check makes this test fail.
- [ ] A session that the shim reports as `failed` is reported as failed, with the shim's reason.

### S-03.3  State watcher and event stream                    runs-on: cloud · size: M
depends: S-03.2   touches: runner/src/marcel_runner/watch.py runner/tests/
do: Poll `agents --json --all` every 2 s, diff the results, and emit `session.state` events on `GET /events` (SSE)
with a monotonically increasing id. Clients resume with `Last-Event-ID`.
done when:
- [ ] Scenario test: happy path → `working`, `done`.
- [ ] Scenario test: needs approval → `blocked` with `waitingFor=permission prompt`.
- [ ] A reconnect with `Last-Event-ID` gets no duplicates and misses nothing.

### S-03.4  Transcript tailing                                runs-on: cloud · size: M
depends: S-03.2   touches: runner/src/marcel_runner/transcript.py runner/tests/
behaviours: B-11
do: Find each session's JSONL, tail it, convert entries to `transcript.schema.json` events, and
serve them on `GET /sessions/{id}/tail?offset=` (SSE). Handle file rotation and a file that does not exist yet.
done when:
- [ ] Every SP2 fixture type maps correctly (table test).
- [ ] An unknown entry type becomes a `raw` event and does not crash.

### S-03.5  Send, fork, worktrees                             runs-on: cloud · size: M
depends: S-03.2, SP7   touches: runner/src/marcel_runner/{send,fork,workspaces}.py runner/tests/
do:
1. `send` delivers through the mechanism SP1 proved: the channel is preferred; otherwise cross-session messaging.
2. `fork` creates a background fork of a session (SP7).
3. `workspaces.ensure(repo)` clones into `~/marcel/workspaces/<repo>` once and creates a worktree per
   task on branch `marcel/<task-id>-<slug>`.
done when:
- [ ] Worktree create and cleanup tests run against a local bare repo.
- [ ] A fork test runs against the shim.

### S-03.6  Cloud spawn and send                              runs-on: cloud · size: S
depends: S-03.2, SP3   touches: runner/src/marcel_runner/cloud.py runner/tests/
behaviours: B-04
do: `cloud_spawn` runs `claude --cloud` and parses the id and URL. `cloud_send` runs `claude -p --cloud <id>`.
done when:
- [ ] The shim scenario passes. An unparsable output returns a readable error that includes the raw output.

### S-03.7  Usage probe                                       runs-on: cloud · size: S
depends: S-03.1, SP4   touches: runner/src/marcel_runner/usage.py runner/tests/
do: Expose `GET /usage` → `{five_hour:{used_pct,resets_at}, weekly:{…}, source, stale}`. It reads whatever SP4 proved works.
done when:
- [ ] A fixture-based test passes, and the "source unavailable" case returns `stale: true` instead of an error.

### S-03.8  Live integration on the NUC                       runs-on: NUC · size: S
depends: S-03.2…S-03.7   touches: runner/tests/live/
do: Write `live`-marked tests that run the real `claude` with a trivial prompt. They cover spawn → done, tail,
stop, and the cap. They are run by hand on the NUC.
done when:
- [ ] `make -C runner test-live` passes on the NUC. The output is pasted into the story's PR.
