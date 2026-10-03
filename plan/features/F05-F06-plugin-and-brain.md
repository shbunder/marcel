# F05 — The marcel Claude Code plugin  (waves 1–2)

Goal: the glue every session loads. It has two parts:

- **channel** (TypeScript): messages and approvals between a session and the hub;
- **tools** (Python MCP): what the brain can do.

### S-05.1  Channel server                                    runs-on: cloud · size: M
depends: S-02.2, SP1   touches: plugins/marcel/channel/ plugins/marcel/.claude-plugin/
behaviours: B-02, B-11, B-12
do:
1. Build an MCP stdio server declaring `claude/channel` and `claude/channel/permission`, with `instructions`
   that differ per `MARCEL_ROLE`.
2. Connect to the hub's `/channel` WS with the token from the environment. Reconnect with backoff.
3. Inbound messages become `notifications/claude/channel` with meta `kind`, `message_id` and `task_id`.
4. Tools:
   - `reply(text, cards?)` for brain and side sessions;
   - `report(kind, …)` for workers: progress, artifact, done, failed.
5. Relay permission requests both ways.
done when:
- [ ] Unit tests against an in-process fake hub WS and a fake MCP client cover inbound, reply, report, relay round trip, and
  a reconnect while a permission is pending.

### S-05.2  Channel live test                                 runs-on: NUC · size: S
depends: S-05.1, S-04.6   touches: plugins/marcel/channel/tests/live/
do: Run a real background session with the plugin against a local hub. Send a message, get a reply, trigger an approval,
approve it through the API.
done when:
- [ ] The live test passes on the NUC, with its output in the PR.

### S-05.3  Brain tools (MCP)                                 runs-on: cloud · size: M
depends: S-04.4, S-04.7   touches: plugins/marcel/tools/ hub/src/marcel_hub/brain_api.py
behaviours: B-03…B-07, B-13, B-20, B-22
do:
1. Build the Python MCP server (HTTP to the hub with the brain's session token). Tools:
   - `tasks_spawn(title, prompt, location=auto|nuc|cloud, repo?, model?, expected_minutes?)`
   - `tasks_list(state?)`
   - `tasks_get(id)`
   - `tasks_send(id, text)`
   - `tasks_stop(id)`
   - `artifacts_add`
   - `memory_read(path)` and `memory_write(path, content)`
   - `schedule_create(name, cron, tz, location, prompt)`, `schedule_list`, `schedule_delete`
   - `notify(text, level)`
   - `usage_get()`
2. Tool descriptions are written for the model: one verb each, saying when to use it and when not.
done when:
- [ ] Each tool has a test against a hub test client.
- [ ] `tasks_spawn` with `location=auto` is resolved by the brain, not the tool. The tool rejects `auto` with a hint to choose; this keeps the decision visible.

### S-05.4  /marcel-adopt command                             runs-on: cloud · size: S
depends: S-04.4   touches: plugins/marcel/commands/marcel-adopt.md plugins/marcel/scripts/adopt.py hub/src/marcel_hub/adopt.py
behaviours: B-14
do:
1. A slash command that runs a script. The script reads the current session id, cwd, and whether this is a cloud session (from the environment),
   then POSTs to `https://marcel-bot.com/adopt` with an adopt token from `~/.marcel/adopt-token`.
2. The hub creates a task with origin `adopted` and posts "Adopted *<title>*" in the main chat.
3. For a local session, the hub asks the runner to start tailing it.
done when:
- [ ] Hub test: adopt creates a task and a message.
- [ ] Script test: a missing token gives a readable instruction.

### S-05.5  Worker protocol and orchestration skills          runs-on: cloud · size: S
depends: S-05.1, S-05.3   touches: plugins/marcel/skills/
do: Write three skills:
- `worker-protocol`: when to `report(progress)` (at milestones, not every step), how to attach
  artifacts (PR URL, files), and to always finish with `report(done|failed, summary)`;
- `orchestrate`: routing rules (B-04, B-05), milestone style (B-06), status answers from `tasks_list` (B-07),
  model choice (B-13), never ask before starting (B-03);
- `digest`: the structure of the four digests (B-17).
done when:
- [ ] A skill-lint test checks frontmatter and size.
- [ ] A scripted `live` eval on the NUC: 5 canned requests → the expected tool calls (recorded in `lessons/`).

# F06 — Brain lifecycle and side threads  (wave 2)

### S-06.1  Brain workspace and persona                       runs-on: cloud · size: S
depends: S-05.5   touches: brain/
behaviours: B-01…B-07, B-13
do: Create the `brain/` template:
- `CLAUDE.md`: Marcel's persona (friendly family giraffe, concise, leads with the outcome) and hard rules (use tools, never
  pretend a task is running, read `MEMORY.md` at start);
- `.claude/settings.json`: Opus, auto mode, plugin enabled, `crossSessionInbound` per SP1;
- the `MEMORY.md` symlink target.
done when:
- [ ] A test checks that the template renders with `deploy/` variables and that the settings JSON validates.

### S-06.2  Keep-alive and rollover                           runs-on: cloud · size: M
depends: S-04.5, S-04.6, S-06.1   touches: hub/src/marcel_hub/brain.py hub/tests/
behaviours: B-01
do:
1. On startup and on every runner event, keep **exactly one** brain session per agent. Spawn it with role
   `brain` if it is missing or `failed`.
2. Store `brain_session_id` on the main conversation.
3. Rollover: when the brain's context passes a threshold, or nightly at 04:00, spawn a new brain, seeded with:
   - a hub-generated summary of the last 24 h;
   - open tasks;
   - `MEMORY.md`.
   Retire the old brain only after the new one has said hello.
done when:
- [ ] Test: kill the brain → a new one within 10 s, and messages sent during the gap are delivered.
- [ ] Test: rollover never leaves zero brains, and never two answering at once.

### S-06.3  Side threads                                      runs-on: cloud · size: M
depends: S-06.2, S-03.5, SP7   touches: hub/src/marcel_hub/side.py hub/tests/
behaviours: B-08
do:
1. Opening a side thread forks the brain (`runner.fork`) with role `side` and the side `conversation_id`.
2. A "send to main" action posts the chosen text into the main conversation as a user-quoted message.
3. Idle side sessions are stopped after 30 minutes and re-forked on the next message, or resumed by session id.
done when:
- [ ] Test: a message in the side conversation never reaches the main brain.
- [ ] Test: "send to main" does reach it.

### S-06.5  Brain wake policy and Triage seam                runs-on: cloud · size: S
depends: S-04.6   touches: hub/src/marcel_hub/triage.py hub/src/marcel_hub/channel/ hub/tests/
behaviours: B-05, B-06, B-11
do:
1. Implement the "When the brain is woken" table in `02-architecture.md` § Token economy as one
   policy function.
2. Define the `Triage` protocol (`decide(event) -> BrainTurn | Direct(target) | Store`) with a
   rules implementation. Log every decision with its reason, so S-16.2 can measure.
done when:
- [ ] A table test covers each row of the wake table.
- [ ] Removing the policy (always wake the brain) makes the "worker done → no brain turn" test fail.

### S-06.4  Brain end-to-end on the NUC                       runs-on: NUC · size: S
depends: S-06.2, S-05.2, S-03.8   touches: hub/tests/live/
do: A live run of three scenarios:
1. "what's 2+2" is answered directly.
2. "make a hello-world PR in shbunder/marcel-sandbox" starts a NUC or cloud worker and produces a "started" milestone.
3. "status?" lists the task.
done when:
- [ ] All three pass, with the transcripts in the PR.
