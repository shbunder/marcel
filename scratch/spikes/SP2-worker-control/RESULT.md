# SP2 — Worker control from a script — RESULT

**Verdict: PASS.** A script can spawn, watch, read, approve, deny, stop, respawn and resume `--bg` workers.
State changes reach `agents --json` in ≤ 0.2 s. There are two traps (workspace trust, and the env
inherited from a parent Claude session) and one gap (steering a *live* session needs SP1's channel).

## Question

Can the runner drive background workers purely from a script — spawn in a worktree, observe state through
`claude agents --json --all`, tail the transcript JSONL, stop/respawn/resume — and how do those sources map
onto `contracts/transcript.schema.json`?

## What was run

- NUC, Claude Code **2.1.289** (`claude --version`), Max subscription, 2026-10-04 09:02–09:08 UTC.
- Sandbox: `sandbox/repo` (a two-function `calc.py` with a bug in `sub()`), worktrees `wt-t1`, `wt-t2`, `wt-t3`
  (gitignored, not committed).
- Every `claude` call that starts a session goes through [`../cclean`](../cclean) — `env -i` with only
  HOME/USER/PATH/LANG/XDG_RUNTIME_DIR/TERM — to match what a systemd user service sees (see trap 2).
- [`record_agents.py`](record_agents.py) polled `claude agents --json --all` every 0.25 s for the whole run and
  wrote one line per change → `fixtures/agents/lifecycle.jsonl`.

```bash
# trust once (trap 1): drives an interactive `claude` on a pty and picks "Yes, I trust this folder"
python3 accept_trust.py sandbox/repo
# t1 — happy path, auto mode, uses a subagent + Edit + Bash + git commit
cd sandbox/wt-t1 && ../../../cclean --bg --name t1 --model sonnet --permission-mode auto "calc.py has a bug in sub(). …"
# t2 — manual mode, one Bash prompt, approved through `claude attach` on a pty
cd sandbox/wt-t2 && ../../../cclean --bg --name t2 --model sonnet --permission-mode manual "Run … touch made-by-t2.txt && ls"
python3 attach_answer.py b18d7c1c 1
# t3 — same, denied
python3 attach_answer.py 7aa6a32d 4
# lifecycle on t1
claude stop bf4bd301; claude respawn bf4bd301
cclean --bg --resume bf4bd301-… "Follow-up: add mul…"      # t1 still live → started a COPY (4d14e24f)
claude stop bf4bd301; sleep 3; cclean --bg --resume bf4bd301-… "Follow-up 3: reply RESUMED"   # woke the original
python3 build_fixtures.py     # copies + sanitises everything into fixtures/
```

## Answers

### Spawn

- `claude --bg …` returns in **0.6–1.4 s** and prints `backgrounded · <short-id> · <name>`. The first call
  starts a per-user background service (a supervisor plus one worker process per session; the roster is in
  `~/.claude/daemon/roster.json`). The short id is the first 8 hex digits of the session UUID.
- Trust is checked per **git root**. `marcel-src` was trusted, but a nested repo under it was not. A
  **worktree inherits trust from its main repo**: trusting `sandbox/repo` was enough for `wt-t1/2/3`.
  So the runner trusts each `~/marcel/workspaces/<repo>` clone **once** when it clones it, and
  every per-task worktree after that just works.
- Every `--bg` session is also bridged to claude.ai Remote Control (`bridgeSessionId: cse_…`, and the transcript
  has a `claude.ai/code/session_…` URL). The owner can open any worker from the Claude app.

### `agents --json --all` — observed states (fixtures in `fixtures/agents/`)

| `status` | `state` | `pid` | `waitingFor` | Meaning | Fixture |
|---|---|---|---|---|---|
| — | `working` | — | — | dispatched, process not up yet (~1–2 s) | `starting.json` |
| `busy` | `working` | ✓ | — | running a turn | `working.json` |
| `waiting` | `blocked` | ✓ | `"permission prompt"` | permission prompt open | `blocked-permission*.json` |
| `idle` | `done` | ✓ | — | turn finished, process alive | `done-idle.json` |
| — | `done` | — | — | stopped (`claude stop`); conversation kept | `done-stopped.json` |
| `idle` | **`working`** | ✓ | — | **after a denial**: turn interrupted, waiting for direction | `jobs/t3-denied/` |
| `idle` | `blocked` | ✓ | — | **model ended its turn asking for input**; `jobs/<id>/state.json` `needs` holds its ask (seen in SP1) | — |
| — | `stopped` | — | — | stopped **mid-turn** (t3 after the denial); a finished session that gets stopped stays `done` | — |

The daemon state is partly model-narrated. `waitingFor` is the only reliable discriminator for "permission prompt".

- Interactive sessions appear in the list too (`kind: "interactive"`, no `state`). The runner filters `kind == "background"`.
- `startedAt` changes when the process (re)starts, and `pid` changes on respawn/resume. **Key on `id`, never on
  `name`**: a resume-copy briefly shows up under the original's name (`t1`) before it gets its own auto-title.
- `--all` keeps stopped sessions in the list.
- `failed` was not observed in this spike (SP4 covers the limit-hit case).

### Latency (state change → visible in `agents --json`)

| Transition | Source timestamp | First seen by the poll (start of `agents --json` call) |
|---|---|---|
| t1 → done | timeline `09:04:27.736` | `09:04:27.909` |
| t2 → blocked | tool_use `09:05:02.118` | poll started `09:05:02.089`, returned ~`.205` |
| t2 approve → busy | key sent `09:05:24.370` | `09:05:24.578` |
| t2 → done | turn_duration `09:05:27.657` | `09:05:27.839` |
| t1 resumed → done | timeline `09:06:34.779` | `09:06:34.976` |

**≤ ~0.2 s plus the poll interval.** One `agents --json --all` call costs **105–138 ms** of wall time.
At the planned 2 s poll, the runner sees a change within ≈ 2.2 s.

### Daemon job files — a richer, undocumented source

`~/.claude/jobs/<short-id>/state.json` and `timeline.jsonl` (fixtures in `fixtures/jobs/`):

- `state.json`: `state`, `tempo` (`idle|blocked|…`), a one-line `detail` ("Finding buggy function"),
  **`needs`** while blocked (`"approve Bash: rm README.md && ls"`, which is approval-card text for free), **`output.result`**
  on done (a one-line summary for free), `fan` (subagents with label/start/done), `tokens`, `intent`,
  `respawnFlags`, `bridgeSessionId`, `firstTerminalAt`/`lastTerminalAt`.
- `timeline.jsonl`: one line per narrated step, `{at, state, detail, text}`, where `text` is the assistant's visible message.

These are internal files with no documented contract. Use them **best effort, version-gated**: they enrich
milestones and approval cards, but `agents --json` stays the source of truth for state.

### Transcript JSONL

- Path: `~/.claude/projects/<cwd with "/" → "-">/<sessionId>.jsonl`.
  Subagents: `<sessionId>/subagents/agent-<id>.jsonl` + `agent-<id>.meta.json`
  (`agentType`, `description`, `toolUseId`, `requestShape: "background"`).
- Append-only, one JSON object per line. **A pending tool_use is not flushed while its permission prompt is
  open.** It appears, with its original timestamp, only once the prompt is answered.
- Resume under the same id **appends to the same file**. A copy/fork gets a **new file that contains the full
  prior history** under its own `sessionId` (no `forkedFrom` field). SP7 relies on this.
- Most lines are metadata the runner ignores: `custom-title`, `agent-name`, `mode`, `permission-mode`,
  `atis-latch`, `last-prompt`, `file-history-snapshot`/`-delta`, and `attachment` (context: system-prompt
  snapshot, skill listings, CLAUDE.md, MCP instructions, token reminders, …).

### Mapping → normalised transcript events (input for S-02.2 / S-03.4)

| Normalised event | JSONL source | Key fields | Fixture (`fixtures/events/`) |
|---|---|---|---|
| `text` (user) | `type:"user"`, `message.content` is a **string**, no `isMeta` | `content`, `origin`, `promptSource` | `user_prompt.json` |
| `text` (agent) | `type:"assistant"`, content block `type:"text"` | `text`, `message.model`, `usage` | `assistant_text.json` |
| *(drop or `thinking`)* | content block `type:"thinking"` (empty `thinking` plus `signature`) | `thinkingDurationMs` | `assistant_thinking.json` |
| `tool_call` | assistant block `type:"tool_use"` | `id`, `name`, `input` | `tool_use_bash.json` |
| `tool_result` | `type:"user"`, block `type:"tool_result"` | `tool_use_id`, `is_error`, `content`; the structured result is in top-level **`toolUseResult`** (Bash: `stdout/stderr/interrupted`) | `tool_result_bash.json` |
| `diff` | `tool_result` whose `toolUseResult` has **`structuredPatch`** (Edit/Write/MultiEdit) | `filePath`, `structuredPatch[{oldStart,oldLines,newStart,newLines,lines}]`, `originalFile` | `diff_edit_tool_*.json` |
| `permission` (pending) | **not in the JSONL.** `agents --json` `state:"blocked"`, `waitingFor:"permission prompt"`, plus `jobs/<id>/state.json` `needs` | `needs` text | `permission_pending.json` |
| `permission` (approved) | no explicit record; only the tool_use → tool_result timestamp gap | — | `permission_approved.json` |
| `permission` (denied) | `tool_result` with `is_error:true`, top-level `toolDenialKind:"user-rejected"`, `toolUseResult:"User rejected tool use"`, then a user text `"[Request interrupted by user for tool use]"` | `toolDenialKind` | `permission_denied.json` |
| `subagent_start` | assistant `tool_use` `name:"Agent"`, then a `tool_result` "Async agent launched… agentId: <id>" | `input.description`, `subagent_type`, agentId parsed from text (or `subagents/*.meta.json` `toolUseId`) | `subagent_spawn_*.json`, `subagent_meta.json` |
| `subagent_stop` | `queue-operation` (`enqueue`/`remove`) whose `content` holds `<agent-message from="<agentId>">` / `<task-notification>…<status>completed`, then a `type:"user"` string starting `"Another Claude session sent a message"` | agentId, status, `<usage>` | `subagent_handback_*.json` |
| `state` | `type:"system"`, `subtype:"turn_duration"` marks end of turn (`durationMs`, `messageCount`, `pendingBackgroundAgentCount`); lifecycle state comes from `agents --json` | | `turn_end.json` |
| `error` | `tool_result.is_error:true` (non-denial); API errors were not observed in this spike | | — |
| `raw` | anything else (`attachment`, `queue-operation` without a hand-back, unknown `type`) | | full transcripts |

Whole sanitised sessions for replay: `fixtures/transcripts/` (happy path with subagent, approved, denied,
resume-copy). The context-attachment payloads (system prompt, CLAUDE.md, skill/MCP listings, environment) are
replaced by `"<elided: N chars>"`, and the owner's email and account/org UUIDs are pseudonymised.
[`sanitize.py`](sanitize.py) / [`build_fixtures.py`](build_fixtures.py) rebuild them.

### Stop / respawn / resume

- `claude stop <id>` returns at once ("stopped …"). The row drops `pid`/`status` and keeps `state:"done"`.
- `claude respawn <id>` restarts the process with its saved options: idle, same id, **no new turn**.
- `claude --bg --resume <uuid> "<msg>"` on a **stopped** session wakes **the same session** ("woke session …
  with its saved options") and runs `<msg>` as a new turn in the same JSONL. **Pass.**
- **Race:** a `--resume` issued ~1 s after `stop` still saw the session as live and started a **copy** instead
  (`note: session … is already running in the background, so this started a copy as <id>`). The runner must wait
  until the id is gone from `~/.claude/daemon/roster.json` (it was after 3 s) or parse the `note:` line and treat a copy as a failed resume.
- `--resume` on a **live** session always copies (a fork with full history; see SP7).

### Approvals and steering from a script

- **`claude attach <id>` on a pty works as a last-resort controller:** wait for `Do you want to proceed?`, send
  `1` (Yes) / `4` (No), then Ctrl+Z to detach while the session keeps running ([`attach_answer.py`](attach_answer.py)). This is a
  screen-scraping fallback, not the design path. Approvals belong to SP1's permission relay.
- There is **no `claude send`**. Without a channel, the ways into a live session are: the channel (SP1), typing
  into `claude attach` on a pty, or stop → `--resume "<msg>"` (only between turns).

## Traps found

1. **Workspace trust.** `--bg` in an untrusted dir fails with `Workspace not trusted. Run claude in <dir> once
   and accept the trust prompt` (this is why the first, pre-session attempt silently never started t1). Trust
   is per git root; worktrees inherit it from their main repo. The prompt's default is **"No, exit"**, so
   automation has to select "Yes" explicitly ([`accept_trust.py`](accept_trust.py)).
2. **Env leaking from a parent Claude session.** Starting `claude` from inside a Claude session inherits
   `CLAUDE_CODE_CHILD_SESSION=1` (+ `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, …), and the child prints
   "Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker". The runner as a systemd unit is
   clean, but anything that shells out to the runner from a Claude session (tests on the NUC, `/marcel-adopt`
   helpers) must scrub `CLAUDE*` env vars.

## What the plan changes

- **02-architecture / runner:**
  - `workspaces.ensure(repo)` accepts the trust prompt once per clone. Worktrees need nothing.
  - The runner scrubs `CLAUDE*` env vars from every `claude` it launches.
  - Watching stays `agents --json --all` every 2 s, keyed on `id` and `kind=="background"`.
  - Add an optional enrichment read of `~/.claude/jobs/<id>/{state,timeline}` (`needs`, `detail`, `output.result`), version-gated.
- **Task state machine (F04):** map `idle+working` (post-denial, or an interrupted turn) and `idle+blocked` without
  `waitingFor` (the model asked a question) to **`needs_you`**, the
  same as `blocked`. `done-stopped` is `stopped` when the hub asked for the stop, otherwise `done`.
- **S-02.2 transcript schema:**
  - use the mapping table above;
  - `permission` comes from the runner's state stream, not from the JSONL;
  - add a `raw` passthrough.
- **S-03.5 `send`:** a live session can be steered only through the channel (SP1). stop → resume is the
  between-turns fallback and must guard against the copy race.
- **S-02.3 fake shim:** replay `fixtures/agents/lifecycle.jsonl` + `fixtures/transcripts/*` + `fixtures/jobs/*`.
  Implement `--bg` output (`backgrounded · <id> · <name>`), the resume `note:` lines, and the trust error.
- **F01/F02:** move `fixtures/` to `runner/tests/fixtures/` when the runner package exists. It stays in
  `scratch/` for now because this run was told not to touch anything outside it.
