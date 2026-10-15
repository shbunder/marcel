# SP13 — Buddies message each other with Claude Code's own messages: RESULT

**PASS, with one correction to ADR-013.** Two brain-like background sessions message each other with Claude Code's
built-in `SendMessage`, with no terminal attached; workers and side threads are shut out by a `permissions.deny` and a
`--settings` `refuse`; and Claude Code fires hooks on **both** sides, so the hub can show "Marcel → Pip: …".

The correction: `crossSessionInbound: accept` in a **project** settings file (the brain folder's `.claude/settings.json`)
is **ignored** — Claude Code only honours a project or local value when it is *stricter* (`hold`, `refuse`). The brain's
`accept` must come from the `--settings` flag (the runner's role settings), user settings or managed settings. Passed with
`--settings` it works, and it survives `claude respawn`.

## Where and with what

- **Ran on macOS** (the owner's Mac, 2026-10-15), not the NUC: the NUC session was offline. Same-machine messaging uses a
  per-session Unix socket on both macOS and Linux (docs), so nothing here should differ on the NUC; re-check step 1 there
  when S-22.13 lands.
- **Claude Code:** the `claude` CLI said `2.1.290`; the background sessions ran on the supervisor's **2.1.300** (shown in
  `claude logs`). The NUC is pinned to 2.1.296.
- Throwaway sessions in `~/Developer/projects/sp13-scratch/` (outside any repo, so no repo `CLAUDE.md`), all started with
  `--bg --setting-sources project,local --plugin-dir <spike plugin>`. Names were prefixed `sp13-` so nothing could reach the
  other sessions running on the Mac. All six were stopped and removed (`claude stop`, `claude rm`); no live Marcel touched.
  The spike was driven from the Claude session `marcel-src-f8` (bypass mode), which sent some test messages itself
  and received the buddies' replies; it is not part of any Marcel install.
- Instead of the full marcel plugin (its channel needs a hub), a spike plugin in [`plugin/`](plugin/) with two hooks that
  append their JSON input to a log: `PostToolUse` matcher `SendMessage`, and `UserPromptSubmit`. The trimmed log is
  [`evidence/hooks.jsonl`](evidence/hooks.jsonl) (paths removed).

Docs read: https://code.claude.com/docs/en/cross-session-messaging and the `crossSessionInbound` entry of
https://code.claude.com/docs/en/settings-reference.

**Evidence.** [`evidence/hooks.jsonl`](evidence/hooks.jsonl) backs the hook, worker-open and burst claims. The rest was
read live by the spike runner and is quoted here word for word: the worker-deny answer (from `claude logs`), the hold,
expiry and refusal notices (delivered to `marcel-src-f8`), the transcript `origin` line, `state.json` and the respawn
note (from the `SendMessage` result). Rows 15 and 58 of the log are hold notices for Pip2's replies to the bypass-mode
runner, not for the Pip test.

## Per step

| Step | Result |
|---|---|
| 1. A brain sends to another; the receiver gets a new turn with no terminal | **PASS** (see the correction above) |
| 2. Deny is the control for workers; a side thread's `--settings` `refuse` beats the brain folder | **PASS** |
| 3. A `PostToolUse` hook on `SendMessage` fires with target and text | **PASS**, and a better hook exists on the receiver |
| 4. A brain's `--name` is stable across `respawn` and `--resume` | **PASS** |
| 5. 20 messages: rate limit or drops | **PASS (20 distinct, one way)**: 20 of 20 sent and delivered, no refusal |

### 1. Brain to brain

```bash
cd sp13-scratch/brains/sp13-pip
claude --bg --name sp13-pip --setting-sources project,local --plugin-dir ../../plugin --permission-mode dontAsk "…reply to any message…"
cd ../sp13-marcel
claude --bg --name sp13-marcel --setting-sources project,local --plugin-dir ../../plugin --permission-mode dontAsk \
  "Use SendMessage to send 'Hello Pip, this is Marcel (test 1)' to the session named sp13-pip."
```

- Marcel's `SendMessage` to `sp13-pip` succeeded (its `PostToolUse` at 12:27:41); Pip had already started a new turn on
  its own at 12:27:40 (no terminal attached) and replied; Marcel got the reply as a new turn at 12:27:47. Round trip
  about 7 s.
- **But this first run proved less than it seemed:** both sessions were `dontAsk` ("prompting" class), and Claude Code
  delivers between two prompting sessions by default. A message from a session in the other class (this spike's
  bypass-mode runner session) to the same Pip was **held**, then expired undelivered: the folder's
  `.claude/settings.json` `crossSessionInbound: accept` did nothing. The settings reference says so: *"A project or local
  value applies only when it's stricter than the value managed settings, the `--settings` flag, or user settings give."*
- With `--settings brains/brain-accept.json` (`{"crossSessionInbound":"accept", "permissions":{"allow":["SendMessage","ListAgents"]}}`)
  a new buddy `sp13-pip2` **received** the bypass-mode message, and again after `claude respawn`.

**What the receiver's transcript records** (`~/.claude/projects/<cwd>/<id>.jsonl`): a `queue-operation` `enqueue` line,
then a `type: "user"` line, `message.role: "user"`, `isMeta: true`, `userType: "external"`, with a structured
`origin`:

```json
{"kind": "peer", "from": "uds:/tmp/cc-socks/85301.sock", "verifiedPeerPid": 85301,
 "msg_id": "5e598c43-…", "name": "sp13-marcel", "fromMode": "prompting", "body": "Hello Pip, this is Marcel (test 1)"}
```

`message.content` is the text Claude sees: "Another Claude session sent a message:\n<cross-session-message from=… from-name=\"sp13-marcel\" …>…</cross-session-message>"
plus Claude Code's safety note. So the runner's transcript reader can turn `origin.kind == "peer"` into a "message from
a buddy" event (sender `origin.name`, text `origin.body`) without parsing the wrapper.

### 2. Workers and side threads

Workers ran in `sp13-scratch/worker` with `--permission-mode auto` and the runner's
`role_settings/worker.json` (already `crossSessionInbound: refuse`), asked to `SendMessage` `sp13-pip`:

- [`worker/worker-open.json`](worker/worker-open.json) (as today): **sent**. "Allowed by auto mode classifier"; Pip got
  "Hello from worker-open".
- [`worker/worker-deny.json`](worker/worker-deny.json) (`deny` + `SendMessage`, `ListAgents`): **cannot send**. The
  tool is gone: its search for it answered "No matching deferred tools found", and nothing reached Pip. So the deny
  rule is the control; without it a worker can steer a brain.
- Side thread: `claude --bg --resume <pip id> --fork-session --name sp13-pip-side --settings brains/side.json`
  (`refuse` + the same deny), started in the brain's folder. A message to it was **refused**: "that session is not
  accepting cross-session messages". (`refuse` would win from any source; the point is it does not depend on the folder.)
  Note: `ListAgents` shows a refusing local session like any other; only the send reveals it.

### 3. Hooks

- **Sender, `PostToolUse` matcher `SendMessage`: fires.** Its input names the sender only by `session_id` (no
  `session_title`; only `UserPromptSubmit` carries that), plus `tool_input {to, summary, message}` and `tool_response {success, message, msg_id}`. **Caveat:** `to` is the name only
  when Claude addressed it by name; a *reply* is addressed to the socket (`"to": "uds:/tmp/cc-socks/85301.sock"`), which
  the hub cannot map to a buddy.
- **Receiver, `UserPromptSubmit`: fires on every delivered message** (ADR-013 assumed no hook fires on an inbound
  message — wrong). Its `prompt` is the `<cross-session-message from=… from-name="sp13-marcel" from-mode=…>text</…>`
  wrapper and `session_title` is the receiver's name. That gives sender, receiver and text every time, replies
  included.
- **Recommendation for S-22.13:** report from the **receiver** (the `UserPromptSubmit` hook, or better the runner's
  transcript tail reading `origin`, which needs no new hook), keyed on `msg_id` so it is shown once. Use the sender's
  `PostToolUse` only for failures (`success: false`, "Not sent").

### 4. Names

- `claude stop bff2d142` then `claude --bg --resume bff2d142-ffed-45be-b64e-5d174d75b13d` (full id, no flags): same id,
  still `sp13-marcel` in `claude agents --json`. `~/.claude/jobs/bff2d142/state.json` keeps `respawnFlags` (with
  `--name`, `--setting-sources`, `--plugin-dir`, `--permission-mode`) and `name`, `nameSource: "user"`.
- `claude respawn bff2d142` and `claude respawn 43f745ba`: same names. A send by name after respawn worked; the result
  adds "Note: messaging a new session for the first time under a previously used name (was it restarted?)".
- A fork (`--fork-session`) takes the new `--name` it is given.

### 5. Twenty messages

`sp13-pip2` made 20 `SendMessage` calls to `sp13-marcel` in one parallel batch: **20 × `success: true`, no refusal,
and all 20 reached `sp13-marcel`** (20 `UserPromptSubmit` hooks, one message each, in order). Only one direction and
only distinct texts: the burst limit and loop throttling the docs describe ("rate-limits repeated messages per sender,
drops identical repeats arriving within a short window, and queues at most 50 accepted messages") were not reached.
Buddy chatter in Marcel will be far below this.

## What this changes

1. **ADR-013 / S-02.16:** the brain's `crossSessionInbound: accept` goes in the brain's **`--settings`** role file (as
   `worker.json` does for `refuse`), not in the brain folder's `.claude/settings.json`. Without it, delivery between
   buddies depends on both being in the same permission-mode class, and anything from a bypass-mode session is held.
2. **Workers** keep `crossSessionInbound: refuse` and add `"SendMessage", "ListAgents"` to `permissions.deny` in
   `role_settings/worker.json`; **side threads** get the same in their `--settings`. Both are controls: S-22.13 needs a
   test that fails when either is removed.
3. **"Marcel → Pip: …" in the app:** read it on the receiving side — the runner's transcript tail already sees
   `origin.kind: "peer"` with `name` and `body` — not only from the sender's `PostToolUse`, whose `to` can be a socket.
   The fallback (a hub-delivered `buddy_message` tool) is **not needed**.
4. Address buddies by slug: `--name` survives `respawn` and a flag-less `--resume`.
