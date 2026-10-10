# Channel protocol

How a Claude Code session and the hub talk. Each NUC session (the brain, a side thread, a worker)
runs the marcel plugin's **channel server**, an MCP stdio server that Claude Code starts because the
session was launched with `--channels plugin:marcel@marcel-local` (ADR-001). The channel server
holds one WebSocket to the hub. Frames are JSON objects defined in
[`channel.schema.json`](channel.schema.json); this page describes the flow.

Cloud workers have no channel (the hub cannot reach them): they use
[`session-api.yaml`](session-api.yaml) `POST /api/session/report` instead.

## Where it connects

- **URL:** `ws://127.0.0.1:7420/channel` — the hub's port on the NUC, **outside `/api`**, so the
  Cloudflare tunnel (which only forwards `/api/*`) never exposes it.
- **Identity:** `CLAUDE_CODE_SESSION_ID`, which Claude Code gives every plugin server. Environment
  set on the `claude --bg` command line never reaches the server (SP1, SP7), so there is no role,
  task id or token in the environment.
- **Secret:** the hub secret in `$CLAUDE_PLUGIN_DATA/hub-secret` (written at deploy, F14). Every
  session on the NUC shares it; what makes a session legitimate is that the runner reported its id.

## The flow

```
channel server                                   hub
     | -- hello {session_id, secret, plugin_version} -->  |  look up session id (runner told the hub at spawn/fork)
     | <-- welcome {role, conversation|task} ----  |  or rejected {reason} (see Failure below)
     |                                             |
     | <-- user_message / steer / system_event --  |  each with seq; queued while disconnected
     | -- ack {seq} -----------------------------> |  after handing it to Claude Code
     |                                             |
     | -- reply {call_id, text} -----------------> |  brain and side threads (the `reply` tool)
     | -- report {call_id, kind, …} -------------> |  workers (the `report` tool)
     | <-- call_ack {call_id} ------------------  |  stored; the tool call returns
     |                                             |
     | -- permission_request {request_id, …} ----> |  Claude Code relayed a prompt → approval card + push
     | <-- permission_decision {request_id, …} --  |  the owner tapped Approve or Deny
     |                                             |
     | -- ping / <-- pong every 20 s --            |
```

## Mapping onto Claude Code

| Frame | What the channel server does in the session |
|---|---|
| `user_message` | `notifications/claude/channel` with `content` = text, `meta` = `{kind: "user_message", message_id, conversation_id, quoted_message_id?}` |
| `steer` | the same, `meta` = `{kind: "steer", task_id, note_id, author, handback?}` (`handback` as `"true"` when set) |
| `system_event` | the same, `meta` = `{kind: "system_event", event: <kind>, task_id?}`; `data` goes in the content as a fenced JSON block |
| `permission_decision` | `notifications/claude/channel/permission` `{request_id, behavior}` |
| Claude Code's `notifications/claude/channel/permission_request` | → `permission_request` frame, fields unchanged |
| the `reply` tool call | → `reply` frame; the tool returns when `call_ack` arrives (or after 10 s with "queued") |
| the `report` tool call | → `report` frame; same |

`meta` keys must be identifiers (letters, digits, underscores): Claude Code silently drops other keys.

The runner pre-allows the plugin's own tools on every spawn, fork and resume
(`--allowedTools mcp__plugin_marcel_marcel__reply mcp__plugin_marcel_marcel__report`, runner-api.yaml),
or a session in `manual` mode prompts for every reply (SP1). A plugin cannot do this itself: Claude
Code keeps only `agent` and `subagentStatusLine` from a plugin's settings.

The channel server holds hub frames, unacked, until Claude Code has sent `notifications/initialized`
and a short settle delay has passed: the session registers its channel handler a little after
`initialized` (SP1), and a notification sent before that is lost.

## Delivery guarantees

- **Hub → session:** every frame carries `seq` (per session, increasing), and the hub sends them in
  `seq` order, re-sends included. The hub keeps frames until they are acked, and re-sends everything
  after `resume_after` when the server says hello again. The server ignores any `seq` at or below the
  highest it has delivered.
- **Permission prompts:** Claude Code relays a prompt once. The server keeps each
  `permission_request` until its `permission_decision` arrives and re-sends it after every welcome.
  The hub treats a `request_id` it already holds, in any card state, as a repeat and opens no second
  card. The server is never told about a prompt answered in a terminal, so it may keep re-sending
  that one; the hub ignores it.
- **Session → hub:** `reply` and `report` carry a `call_id`, and the hub answers each with
  `call_ack {call_id}` once it is stored. The server keeps every call without an ack and re-sends
  it after a reconnect. The hub stores each `call_id` once and acks a repeat again (`duplicate:
  true`), so a re-send is harmless.
- **Session not connected:** the hub queues its frames (the Outbox, S-04.6). A worker's frames for a
  `queued` or `starting` task wait for its hello.
- **Approvals answered elsewhere:** a prompt can also be answered in a terminal attached to the
  session. The channel is not told. The hub learns it when the runner reports that the session is
  no longer `waiting_for: permission prompt`, and reads the answer from the transcript: a
  `tool_result` with `denied: true` for that tool call means denied, anything else approved. The
  card becomes `approved` or `denied` with `answered_via: terminal`. A prompt that disappears
  because the session stopped or failed makes the card `expired`.
- **A denial with a note:** Claude Code's permission answer carries only allow or deny. When the
  owner adds a note to a denial (app-api.yaml `ApprovalAnswer.note`), the hub sends it right after
  the `permission_decision`: as a `steer` to a worker, or a `user_message` to the brain or a side
  thread.

## Failure, and what the owner sees

| Failure | Behaviour |
|---|---|
| Hub down | The server retries the WebSocket with backoff (1 s → 30 s). While it is not connected, `reply` and `report` return at once with "queued, Marcel is reconnecting" (or "connecting" before the first welcome), and the call goes out after the next welcome. While connected, they wait up to 10 s for `call_ack`, then return "queued". |
| Hub silent | The hub accepts the socket but answers no hello within 15 s, or sends nothing for 60 s after welcome: the server closes the socket and reconnects with backoff. |
| Server gives up | After a rejection it does not retry, or the end of the `unknown_session` window: tools return an error, "Marcel is unreachable", and the server logs how many queued calls it dropped. |
| Unknown session id | `rejected unknown_session`; retried every 5 s for 2 minutes, then the server logs it and stops. The runner's spawn check (`channel_registered`) and the hub's watchdog surface it as a task that never said hello. |
| Wrong secret | `rejected bad_secret`; no retry. The hub raises one "something broke" alert: a deploy wrote a different secret. |
| Plugin too old | `rejected plugin_too_old`; no retry. The hub raises one "something broke" alert naming the plugin version it needs: the plugin was not updated with the hub. |
| Channel not registered at all | Caught at spawn: the runner reads `Channel notifications skipped` in the session's debug log and fails the spawn with that reason (runner-api.yaml `channel_skipped`). |
