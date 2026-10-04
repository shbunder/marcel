# Marcel v3 — architecture

Marcel v3 is **a shell around Claude Code**, not a new agent harness. Every model token is spent by the
unmodified `claude` CLI on the owner's Max subscription. Marcel's own code does four things:

- keeps state (the conversation, tasks, artifacts, schedules);
- moves messages between the iPhone and Claude Code sessions;
- supervises those sessions;
- renders it all in an iOS app.

## Why this shape

- **Subscription terms.** Anthropic allows a subscription login in the *unmodified* `claude` binary for
  individual use. It tells developers building on the Agent SDK to use API keys. So no Agent SDK and no
  setup-token anywhere. The brain and every worker is a real `claude` process.
- **The conversation lives in the hub, not in a context window.** The brain session is
  replaceable: it may compact or roll over without the user's transcript changing (B-01).
- **One context per task.** Dots' published numbers show permission errors more than doubling
  when one context juggles many tasks. Each thread is its own session; the brain only routes and summarises.
- **Lightweight.** No ACP, A2A or AG-UI layers. The building blocks are Claude Code's own features:
  - background sessions (`claude --bg`, `claude agents --json`);
  - channels (a two-way MCP push, with permission relay);
  - plugins (skills, MCP, commands, hooks);
  - cloud sessions (`claude --cloud`);
  - fork (`--fork-session`).

## Components

```
                         iPhone (SwiftUI app)
                                │  HTTPS + WebSocket, device token
                                ▼
            cloudflared tunnel  marcel-bot.com  ──►  NUC
┌──────────────────────────────────────────────────────────────────────────────┐
│ NUC                                                                          │
│                                                                              │
│  ┌──────────── hub (Docker) ────────────┐    unix socket    ┌─ runner ────┐ │
│  │ FastAPI · SQLite · scheduler          │ ◄──────────────► │ (native,    │ │
│  │ conversation store (main + side)      │  /run/marcel/    │ systemd     │ │
│  │ task registry + state machine         │   runner.sock    │ --user)     │ │
│  │ artifacts · memory (git) · usage guard│                  │ spawns and  │ │
│  │ push (APNs / ntfy) · app API + WS     │                  │ watches     │ │
│  │ channel gateway (WS for sessions)     │                  │ `claude`    │ │
│  └───────────────▲───────────────────────┘                  └─────┬───────┘ │
│                  │ WS (localhost)                                 │ spawns  │
│      ┌───────────┴──────────────────────────────────────────┐     ▼         │
│      │ marcel plugin, loaded by every session:              │               │
│      │  • channel server  (msgs in, reply out, perm relay)  │               │
│      │  • MCP tools        (brain: tasks/memory/schedule…)  │               │
│      │  • /marcel-adopt, skills, hooks                      │               │
│      └──────────────────────────────────────────────────────┘               │
│   brain:  claude --bg --name marcel --model opus --channels plugin:marcel@marcel-local │
│   side:   forks of the brain session               (one per side thread)     │
│   workers: claude --bg --model sonnet --permission-mode auto --channels … (worktrees)│
└──────────────────────────────────────────────────────────────────────────────┘
        cloud workers: claude --cloud "…"  (claude.ai/code sessions)
        Harry (separate service, MCP on :7430) — a connector, later
```

### hub (Python 3.12, FastAPI, SQLite, runs in Docker)

The source of truth. It holds:

| Area | What the hub owns |
|---|---|
| Data | agents, users, devices, the conversation (main and side), tasks, events, artifacts, schedules, usage snapshots |
| App API | REST + one WebSocket per device (`contracts/app-api.yaml`, `contracts/events.schema.json`) |
| Channel gateway | One WebSocket endpoint, `/channel`, that every session's channel server connects to. Messages for a session are pushed down it; replies, progress, artifacts and permission requests come up it. |
| Brain lifecycle | Asks the runner to keep exactly one brain session alive per agent, restarts it, and triggers rollovers (F06). |
| Task state machine | Nine states (`queued`, `starting`, `working`, `needs_you`, `done`, `failed`, `stopped`, `silent`, `unknown`), every transition listed in `contracts/task-states.md`, driven by runner and channel events. |
| Scheduler | APScheduler with a SQLite job store; digests, user schedules, watchdogs. |
| Push | Notifier interface with APNs and ntfy backends. |
| Memory | A git working copy at `~/marcel/memory`. The app edits through the API; the hub commits. |
| Usage guard | See F10. |

### runner (Python 3.12, native on the host, systemd user service)

The only component that executes `claude`. The hub is in Docker and the workers are native, so this
boundary is deliberate. The runner is small and has no business logic.

| Area | What the runner does |
|---|---|
| Supervisor | Claude's own background supervisor runs in **its own** user unit, `marcel-claude-daemon.service` (`claude daemon run`), never inside the runner's cgroup, so restarting the runner never kills a session (SP6). The runner refuses to start if no supervisor is running outside its cgroup. |
| Launching | Every `claude` it starts gets a scrubbed environment (no inherited `CLAUDE*` variables), an absolute path to the binary, and the prompt after `--` (the `--channels` flag is variadic). It checks the session's debug log for `Channel notifications registered` and fails the spawn loudly on `skipped` (SP1, SP2). |
| API | Speaks only on a unix socket, `/run/user/<uid>/marcel/runner.sock` on the host (systemd `RuntimeDirectory=marcel`, `RuntimeDirectoryPreserve=yes`, mode 0660), mounted into the hub container as `/run/marcel` (SP6). Operations: `spawn`, `list`, `get`, `send`, `stop`, `fork`, `tail`, `cloud_spawn`, `cloud_send`, `usage`, `health` (`contracts/runner-api.yaml`). |
| Watching | Polls `claude agents --json --all` every 2 s (≈0.12 s per call), keyed on the session `id` and `kind == background`, and emits state-change events to the hub. It may enrich them, best effort and version-gated, from the undocumented `~/.claude/jobs/<id>/state.json` (`needs`, `detail`, `output.result`), which gives approval-card text and one-line summaries for free (SP2). |
| Transcripts | Tails session transcripts (`~/.claude/projects/<cwd with / → ->/<session-id>.jsonl`, plus `subagents/`) and streams normalised transcript events (`contracts/transcript.schema.json`, mapped as in SP2's table), resumable by byte offset. The hub never mounts `~/.claude` (SP6). |
| Workspaces | Manages worktrees under `~/marcel/workspaces/<repo>`. Workspace trust is per git root and worktrees inherit it, so the runner accepts the trust prompt once per clone (SP2). |
| Cloud | `cloud_spawn` runs `claude --cloud "<prompt>"` under a pty (it refuses without a terminal) and parses the session id and URL; `cloud_send` runs `claude -p "<msg>" --cloud <id> --output-format json`, which returns an ack. Nothing can poll a cloud session's state: cloud workers report to the hub over HTTPS (SP3). |
| Usage | `claude -p "/usage" --no-session-persistence` (no model call, 2–3 s) parsed from its text, every 5 minutes and before each spawn. A status-line command may also publish `rate_limits` from live sessions (SP4). |
| Limits | Enforces the NUC concurrency cap. |
| Testing | Never calls a model directly. Tests use a **fake `claude` shim** (`runner/tests/fake_claude/`) that replays recorded `agents --json` and JSONL fixtures. |

### marcel plugin (a Claude Code plugin in `plugins/marcel/`)

Loaded by the brain, its side threads and every NUC worker.

- **How it loads (SP1, option A; ADR-001):** the plugin is installed from a local marketplace,
  `marcel-local`, that points at a deployed copy of `plugins/marcel/` (never a working tree, because a
  directory-source plugin runs in place). `/etc/claude-code/managed-settings.json` turns channels on and
  allowlists `marcel@marcel-local`, so a `--bg` session started with `--channels plugin:marcel@marcel-local`
  registers the channel with no terminal. The development-channels flag silently does nothing in `--bg`.
- **Channel server** (`marcel-channel`): a TypeScript MCP stdio server. It declares
  `claude/channel` and `claude/channel/permission` and connects to the hub's `/channel` WS. It
  **identifies itself by `CLAUDE_CODE_SESSION_ID`**, which Claude Code gives every plugin server: env set
  on the `claude --bg` command line never reaches it (SP1, SP7). The hub maps that session id to brain,
  side thread or task, because the runner reported the id at spawn. It authenticates with a hub secret
  in the plugin's data folder (`CLAUDE_PLUGIN_DATA`); the hub only accepts session ids the runner started.
  The plugin's settings pre-allow its own tools (`mcp__plugin_marcel_marcel__reply`, `…__report`) so
  replies never prompt.
  - It pushes inbound user and steering messages as `<channel source="marcel" …>` events.
  - It exposes `reply` (brain and side) and `report` (workers: progress, artifact, done) tools.
  - It relays permission prompts as approval cards.
- **Brain MCP tools** (`marcel-tools`, Python MCP server over HTTP to the hub):
  - tasks: `tasks_spawn`, `tasks_list`, `tasks_get`, `tasks_send`, `tasks_stop`;
  - artifacts and memory: `artifacts_add`, `memory_read`, `memory_write`;
  - schedules: `schedule_create`, `schedule_list`, `schedule_delete`;
  - other: `notify`, `usage_get`.
- **`/marcel-adopt` command**: registers the current session (id, cwd, cloud or local) with the hub through
  `marcel-bot.com/api/session/adopt` with an adopt token (not a device token; see
  `contracts/session-api.yaml`). The session becomes a tracked task (B-14).
- **Skills**:
  - `orchestrate` (the brain's playbook: routing, where to run, milestone style);
  - `worker-protocol` (how a worker reports progress, artifacts and completion);
  - `digest` (how to write each digest).

### brain workspace (`brain/` template, deployed to `~/marcel/brain`)

- `CLAUDE.md`: Marcel's persona and operating rules.
- `MEMORY.md` plus `memory/`: symlinked to the memory repo.
- `.claude/settings.json`: plugin enabled, auto mode, the channel flag, Opus.

### iOS app (Swift 6, SwiftUI, iOS 18+, `ios/`)

- **Screens:**
  - Chat (main + side threads, milestone and approval cards);
  - Activity;
  - Thread view (live transcript, steer, approve, stop);
  - Library (PR cards, Markdown/HTML in `WKWebView`, file preview, native widgets);
  - Memory editor;
  - Settings.
- **Networking:** `URLSession` + `URLSessionWebSocketTask`; a local cache in SwiftData.
- **Avatar:** RealityKit in a `RealityView` with a virtual camera, procedurally built from
  `shared/avatar/recipes/*.json` + `shared/avatar/palettes.json` + a seed (F13).

## Key flows

**1. A message in the main chat.** App → `POST /api/conversations/{main_conversation_id}/messages` → hub stores it → the channel
gateway pushes it to the brain session → the brain either answers through `reply` (hub stores it, WS
to the app) or calls `tasks_spawn` → the hub creates the task and the runner spawns the worker → a
"started" milestone is posted.

**2. A worker needs approval.** The worker's channel server receives a permission request from Claude
Code → hub → task becomes `needs_you`, approval card plus push → I tap Approve → hub → channel server → Claude Code
continues. Answering in a terminal also works; the first answer wins.

**3. Steering.** I type in the thread view → `POST /api/tasks/{id}/messages` → hub → that worker's channel
server pushes it into the session.

**4. Completion.** The worker calls `report(done, summary, artifacts)`, or the runner sees
`state=done` → the hub builds the milestone from the worker's own summary and posts it, with **no brain
turn**. The brain is woken only by the rules in "Token economy" below.

**5. Side thread.** Reply-in-thread → hub asks the runner to `fork` the brain session
(`--resume <brain> --fork-session`, background, same channel) → messages in that side thread go to the
fork only.

**6. Cloud worker.** The brain chooses cloud → runner `cloud_spawn` (`claude --cloud "<prompt with
worker-protocol>"`) → the hub stores the session id and URL. Status and completion come from whatever the
de-risking experiment SP3 proves works. The fallback is a link plus the cloud session's own reports to the hub over HTTPS.

## Token economy

Workers spend most of the tokens; a coding session burns far more than a routing turn. The brain is
Opus, though, and every turn re-reads its context, so the hub keeps brain turns for **judgement only**.

**When the brain is woken (S-06.5):**

| Event | Brain turn? | Handled by |
|---|---|---|
| User message in main chat | yes | brain |
| User message in a side thread | yes | that side fork |
| Steer typed in a thread view, or "reply in thread" on a milestone card | **no** | hub forwards it straight to that task (explicit routing chosen in the UI) |
| Approve / deny tap | **no** | hub → channel |
| Worker progress | **no** | hub stores it; thread view and Activity only |
| Worker done | **no** | hub posts a milestone from the worker's own summary |
| Worker failed, silent, or asked a question the user can't answer with a button | yes | brain decides: retry, re-route, or ask |
| Digest due | yes | brain writes it from the hub's facts bundle |
| "status?"-style message | yes in v1 | brain via `tasks_list`, with a cached system prompt |

Other levers:

- Nightly or threshold rollover keeps the brain's context small (S-06.2).
- Worker prompts include only the memory slices that matter.
- Workers default to Sonnet.

**Decision models (Jev / Clef): not in v1.** S-16.2 measures whether they would pay off.

- **Jev** needs an API key and a waitlisted paid API. That breaks the subscription-only, no-API-key
  decision.
- **Clef-flash** has open weights but needs about 41 GB of GPU memory, which the NUC (integrated GPU)
  does not have. Hosted Clef on Workers AI is again a separate paid API.
- At personal volume (tens of brain turns a day), these models' main strength — very cheap decisions
  over high-volume queues — barely registers. The UI's explicit routing already removes the main
  ambiguity a router would resolve.

The hub has a `Triage` seam with a rules implementation, so a decision model can be added later
without touching the brain.

## Data model (summary; where it and `contracts/` disagree, the contract wins)

```
agent(id, owner_user_id, name, animal, palette, seed, persona_path, model_brain, model_worker,
      main_conversation_id)
device(id, user_id, name, token_hash, apns_token?, ntfy_topic?, created_at, last_seen)
conversation(id, agent_id, kind[main|side], parent_message_id?, task_id?, brain_session_id?)
message(id, conversation_id, role[user|agent|system], kind[text|milestone|approval|quote], body_json,
        task_id?, client_id?, created_at)
task(id, agent_id, title, location[nuc|cloud], state, queued_reason?[cap|usage], model,
     session_id?, cloud_url?, remote_url?, repo?, branch?, worktree?,
     origin[marcel|adopted|schedule], parent_message_id?, schedule_id?, summary?, waiting_for?,
     created_at, updated_at, last_activity_at, expected_by?, archived_at?)
task_event(task_id, seq, type, data_json, at)                    -- normalised transcript + hub events
approval(id, task_id, request_id, tool, summary, detail?, artifact_id?,
         state[open|approved|denied|expired], answered_via?[app|terminal], answered_at?)
artifact(id, task_id?, kind[pr|branch|doc|file|dashboard|link], title, url?, content_type?,
         size_bytes?, trusted, self_change, meta_json, created_at)
schedule(id, agent_id, name, cron, tz, location[nuc|cloud|brain], prompt, paused, builtin,
         deadline_minutes?, last_run_at?, next_run_at?)
usage_snapshot(id, taken_at, window[five_hour|weekly], used_pct, resets_at, source)
```

## Repo layout (monorepo)

```
hub/          Python package `marcel_hub` (+ tests)            → Docker image
runner/       Python package `marcel_runner` (+ fake claude)   → systemd user unit
plugins/marcel/   Claude Code plugin (channel server TS, tools py, skills, commands)
brain/        brain workspace template (CLAUDE.md persona, settings)
ios/          Xcode project `Marcel` (app + MarcelKit + AvatarKit packages)
shared/avatar/    recipes/*.json, palettes.json  (consumed by iOS; validated by a py test)
contracts/    app-api.yaml, events.schema.json, task-states.md, dashboard.schema.json (S-02.1);
              runner-api.yaml, transcript.schema.json, channel-protocol.md, session-api.yaml (S-02.2)
deploy/       docker-compose.yml, systemd units, cloudflared ingress, redeploy.sh, watchdog
project/      board: features/, stories/, decisions/, lessons/, contract-requests/
docs/         operator + developer docs
plan/         this plan: spec, architecture, spikes, playbook
```

## Tech choices (fixed; change only by an ADR)

| Area | Choice | Reason |
|---|---|---|
| Hub / runner | Python 3.12, uv, FastAPI, SQLite (SQLModel), APScheduler 3, pytest, ruff, pyright | Same toolchain as Harry; boring |
| Channel server | TypeScript, `@modelcontextprotocol/sdk`, bun or node 22 | Matches the official channel examples |
| iOS | Swift 6, SwiftUI, iOS 18 minimum, RealityKit, SwiftData, XCTest | RealityView needs iOS 18; SceneKit is soft-deprecated |
| Push | APNs (token-based .p8) + ntfy fallback | B-18 |
| Auth | Device tokens (random 32 bytes, stored hashed); QR pairing; Cloudflare tunnel | B-27 |
| Deploy | Hub in Docker Compose; runner and claude native; flag-file redeploy + health watchdog + git revert | B-24 |

## Risks (after the spikes, 2026-10-04)

The seven risks the plan started with were all tested on the NUC and the Mac (`03-spikes.md`, results in
`scratch/spikes/`). What is left:

| Risk | Status | Mitigation |
|---|---|---|
| 1. Custom channel in `--bg` | **Resolved** with a managed-settings allowlist (SP1, ADR-001) | The policy file applies to every `claude` on the NUC and replaces the default channel allowlist (Telegram and iMessage are re-listed). Fallback: a runner-owned pty session for the brain |
| 2. Observing cloud sessions | **Resolved by design**: workers report over HTTPS with a per-task token (SP3) | No polling path exists; a silent cloud task becomes `silent` and then needs you |
| 3. Reading plan usage | **Resolved**: `claude -p /usage` text, plus the status-line `rate_limits` (SP4) | `/usage` is presentation text: one regex, with a contract test on recorded output |
| 4. Steering a live session | **Resolved**: only through the channel; stop then `--resume` between turns, guarding the copy race (SP2) | — |
| 5. Hub ↔ runner socket | **Resolved** (SP6) | Separate claude supervisor unit; `RuntimeDirectoryPreserve`; linger on |
| 6. Forking the brain | **Resolved**: `--resume <brain> --fork-session` (SP7) | A fork reloads the brain's whole context once: keep the brain small (S-06.2) |
| 7. Avatar on iPhone | **Open** (SP5): works in the simulator; iPhone frame rate, colours and the owner's verdict pending | Boxes cannot make the logo's neck curve; one custom mesh may be needed |
| New: undocumented internals | `~/.claude/jobs/*` and `/usage` wording may change with any Claude Code update | Used best effort only, behind a version check; `agents --json` stays the source of truth |
