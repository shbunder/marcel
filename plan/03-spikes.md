# Phase 0 — spikes

Seven unknowns could each reshape the architecture. Each one is a small, throwaway experiment (a spike)
that answers a single question. They all run **before** feature work fans out. Spike code goes in
`scratch/spikes/SPx-*/` and is never imported by product code.

Every spike ends with a short `RESULT.md` in its folder:

- the question;
- what was run (exact commands and versions);
- the answer;
- what the plan changes because of it.

The lead, an Opus session, folds each answer into `02-architecture.md` and the affected stories before
wave 1 starts.

**Runs on:** `NUC` means a Claude Code session on the NUC (Remote Control, or a background session started there),
logged in with the owner's subscription. Claude Code must be **v2.1.280 or newer**; the NUC had 2.1.220 on
2026-09-19, so upgrade first. `Mac` means a session on the owner's Mac with Xcode.

---

### SP1 — A custom channel in an unattended background session  ·  NUC  ·  blocks F05, F06

**Question:** Can a `claude --bg` session load our custom two-way channel with permission relay
through `--dangerously-load-development-channels`, with no human at a terminal?

**Steps:**
1. Build the minimal fakechat-style channel from the docs, with:
   - `claude/channel` and `claude/channel/permission` capabilities;
   - a `reply` tool;
   - an HTTP endpoint that pushes a message in.
2. Start the session with `claude --bg --name sp1 --dangerously-load-development-channels server:sp1 "wait for messages"`.
3. Check whether the startup confirmation prompt blocks (`claude agents --json` → `waitingFor`).
   If it does, find out whether answering it once persists (settings key, or approval stored per entry).
4. Push a message, get a reply, then trigger a `Bash` permission prompt and answer it through the relay.
5. Repeat with the channel packaged as a plugin in a local marketplace
   (`plugin:marcel@marcel-local`).

**Pass:** messages in and out, plus a relayed approval, all work in a background session with no terminal attached.

**Fallbacks, in order:**
1. Accept the confirmation once with `claude attach` at deploy time, if it persists.
2. Use a `PermissionRequest` hook that calls the hub and blocks until the app answers (approvals),
   plus cross-session messaging (`crossSessionInbound: accept`) for steering.
3. Use the official Telegram channel as the transport for the brain only.

### SP2 — Worker control from a script  ·  NUC  ·  blocks F03

**Steps:**
1. Spawn a worker: `claude --bg --name t1 --model sonnet --permission-mode auto "<task>"` in a worktree.
2. Record `claude agents --json --all` across its whole life. Save the fixtures; the fake `claude` shim
   replays them.
3. Find the transcript JSONL for its session id. Tail it, and record one fixture per event type:
   assistant text, tool_use, tool_result, diff, permission, subagent.
4. Stop it (`claude stop`), respawn it, and resume it.
5. Measure the latency from a state change to its appearance in `agents --json`.

**Pass:** a written mapping from `agents --json` and JSONL to `contracts/transcript.schema.json`, plus
fixtures committed in `runner/tests/fixtures/`.

### SP3 — Cloud sessions from the NUC  ·  NUC  ·  blocks F07

**Steps:**
1. Run `claude --cloud "<task>"` and capture the session id and URL it prints. Try
   `--output-format json` if that is supported.
2. Send a follow-up: `claude -p "<msg>" --cloud <id> --output-format json`. What comes back?
3. Observability. Can a session on the NUC that is connected to Remote Control see the cloud session
   through `ListAgents`, and `SendMessage` to it?
4. Reporting back. Can the cloud session reach `https://marcel-bot.com` (cloud environment network policy)
   and POST a report with a scoped token?
5. Does `claude --teleport <id>` give us the transcript?

**Pass:** a defined way to learn a cloud task's state and get its result, even if that way is
"the worker reports over HTTPS".

### SP4 — Reading plan usage  ·  NUC  ·  blocks F10

**Steps:**
1. Check whether the status-line JSON input carries rate-limit fields (`rate_limits`, five-hour and weekly
   percentages, reset times) on the current version. If it does, a status-line command can write them to a file.
2. Check whether `/usage` has a non-interactive or JSON form.
3. Record the exact limit-reached message and state (`agents --json` → `blocked` / `failed`?), so
   that hitting the limit can be detected.

**Pass:** a reliable source of `used_pct` and `resets_at`, or a documented "detect on hit" fallback.

### SP5 — RealityKit giraffe  ·  Mac  ·  blocks F13

**Steps:**
1. Build the giraffe from rounded boxes in a `RealityView` with `.virtual` camera:
   - recipe JSON from `shared/avatar/recipes/giraffe.json`;
   - colour roles from `docs/design/palette.json` (rose `#cc5e76`, maroon `#8e3447`, teal `#5e807f`,
     orange `#f6aa1c`).
2. Compare `UnlitMaterial` (the flat logo look) with PBR (soft depth).
3. Build an idle animation (breathing and blinking) and `working(3)` (three spots glow) in a custom `System`.
4. Measure frames per second and energy use on the owner's iPhone.

**Pass:** the owner looks at a screenshot next to `docs/design/logo.png` and says "that's Marcel", and it
runs at a steady 60 fps.

### SP6 — Hub in Docker ↔ native runner  ·  NUC  ·  blocks F03, F04, F14

**Steps:**
1. Expose a unix socket from a systemd user service and mount it into a container.
2. Find a UID/GID mapping that lets the container read and write it.
3. Mount `~/.claude/projects` read-only only if the hub needs it; the preferred design is that the runner streams transcripts.
4. Confirm the runner survives `docker compose down` and that its sessions survive a runner restart.

**Pass:** a hello-world round trip, plus a documented compose and systemd snippet.

### SP7 — Forking the brain for side threads  ·  NUC  ·  blocks F06 (side threads)

**Steps:**
1. With SP1's channel brain running, fork it into a new background session:
   `claude --bg --resume <id> --fork-session …`, or `/fork` through the brain.
2. Check that the fork has the main thread's context, has its own channel connection (a different
   `MARCEL_CONVERSATION_ID`), and does not write into the original.

**Pass:** two independent background sessions that share history up to the fork.
**Fallback:** start a fresh side session seeded with a hub-generated summary of the main thread.
