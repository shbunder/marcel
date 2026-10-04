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
     | -- hello {session_id, secret, version} -->  |  look up session id (runner told the hub at spawn/fork)
     | <-- welcome {role, conversation|task} ----  |  or rejected {reason}; then retry every 5 s for 2 min
     |                                             |
     | <-- user_message / steer / system_event --  |  each with seq; queued while disconnected
     | -- ack {seq} -----------------------------> |  after handing it to Claude Code
     |                                             |
     | -- reply {call_id, text} -----------------> |  brain and side threads (the `reply` tool)
     | -- report {call_id, kind, …} -------------> |  workers (the `report` tool)
     |                                             |
     | -- permission_request {request_id, …} ----> |  Claude Code relayed a prompt → approval card + push
     | <-- permission_decision {request_id, …} --  |  the owner tapped Approve or Deny
     |                                             |
     | -- ping / <-- pong every 20 s --            |
```

## Mapping onto Claude Code

| Frame | What the channel server does in the session |
|---|---|
| `user_message` | `notifications/claude/channel` with `content` = text, `meta` = `{kind: "user_message", message_id, conversation_id}` |
| `steer` | the same, `meta` = `{kind: "steer", task_id, note_id, author}` |
| `system_event` | the same, `meta` = `{kind: "system_event", event: <kind>, task_id?}`; `data` goes in the content as a fenced JSON block |
| `permission_decision` | `notifications/claude/channel/permission` `{request_id, behavior}` |
| Claude Code's `notifications/claude/channel/permission_request` | → `permission_request` frame, fields unchanged |
| the `reply` tool call | → `reply` frame; the tool returns once the hub acks it (or after 10 s with "queued") |
| the `report` tool call | → `report` frame; same |

`meta` keys must be identifiers (letters, digits, underscores): Claude Code silently drops other keys.

The plugin's settings pre-allow its own tools (`mcp__plugin_marcel_marcel__reply` and
`mcp__plugin_marcel_marcel__report`), or a session in `manual` mode prompts for every reply (SP1).

## Delivery guarantees

- **Hub → session:** every frame carries `seq` (per session, increasing). The hub keeps frames until
  they are acked, and re-sends everything after `resume_after` when the server says hello again. The
  server ignores a `seq` it has already delivered.
- **Session → hub:** `reply` and `report` carry a `call_id`. The hub stores each `call_id` once, so a
  repeat after a reconnect is harmless.
- **Session not connected:** the hub queues its frames (the Outbox, S-04.6). A worker's frames for a
  `queued` or `starting` task wait for its hello.
- **Approvals answered elsewhere:** a prompt can also be answered in a terminal attached to the
  session. The channel is not told; the hub learns it when the runner reports that the session is no
  longer `waiting_for: permission prompt`, and resolves the card as answered from the terminal. A
  prompt that disappears because the session stopped or failed expires the card.

## Failure, and what the owner sees

| Failure | Behaviour |
|---|---|
| Hub down | The server retries the WebSocket with backoff (1 s → 30 s). `reply` and `report` return "queued, Marcel is reconnecting" to Claude Code, which carries on. |
| Unknown session id | `rejected`; retried for 2 minutes, then the server logs it and stops. The runner's spawn check (`channel_registered`) and the hub's watchdog surface it as a task that never said hello. |
| Wrong secret | `rejected bad_secret`; no retry. The hub raises one "something broke" alert: a deploy wrote a different secret. |
| Channel not registered at all | Caught at spawn: the runner reads `Channel notifications skipped` in the session's debug log and fails the spawn with that reason (runner-api.yaml `channel_skipped`). |
