# The channel server

The marcel plugin's channel server carries messages between one Claude Code session and the hub.
Every session on the NUC (the brain, a side thread, a worker) runs its own copy, because Claude
Code starts it from the plugin. It speaks MCP over stdio to Claude Code and holds one WebSocket to the hub.
The frames are defined in `contracts/channel.schema.json`; the flow is in `contracts/channel-protocol.md`.

## What it needs

| Need | Where it comes from |
|---|---|
| Session id | `CLAUDE_CODE_SESSION_ID`, which Claude Code gives every plugin server |
| Hub secret | the file `hub-secret` in `$CLAUDE_PLUGIN_DATA`. Deploy writes it. It is not an environment variable, because environment set on `claude --bg` never reaches the server |
| Hub address | `ws://127.0.0.1:7420/channel`. `MARCEL_HUB_URL` overrides it for development |
| Build | `npm ci && npm run build` in the **deployed copy** of `plugins/marcel/channel/` makes `dist/main.js`, which `plugin.json` starts with `node`. `dist/` is gitignored, so a fresh checkout has none |
| Node | `node` 22 or newer on the PATH of the claude daemon, not just of a login shell |
| Start flag | `claude --bg --channels plugin:marcel@marcel-local` (ADR-001) |
| Pre-allowed tools | The session must be started with `--allowedTools mcp__plugin_marcel_marcel__reply mcp__plugin_marcel_marcel__report`, or a session in `manual` mode asks before every reply. A plugin's own `settings.json` cannot do this: Claude Code keeps only `agent` and `subagentStatusLine` from it. The runner passes it on every spawn and fork; a resume passes no flag and keeps it through the options Claude saved with the session, and the runner refuses a resume whose saved options lack it (`docs/runner/workspaces.md`). |

The server has no runtime dependencies.

## What the session gets

- Messages arrive as channel events. `meta.kind` is `user_message`, `steer` or `system_event`.
  A quoted message adds `meta.quoted_message_id`; a hand-back steer adds `meta.handback` = `"true"`.
  A message with `@` or `#` mentions adds `meta.mentions`: the hub's checked list as a JSON string
  (`meta` values are strings), e.g. `[{"kind":"task","id":"tsk_…","title":"Fix flaky login test"}]`.
- Tool `reply(text, in_reply_to?)` for the brain and side threads.
- Tool `report(kind, …)` for workers: `started`, `progress` (needs `text`), `artifact` (needs `artifact`), `done` and `failed` (need `summary`),
  and `note` (needs `text`, 1 to 1000 characters: one lasting fact for the project's notes).
  A report that breaks these rules is refused with a plain message before it leaves the session.
- When the hub refuses a call (its `call_ack` carries an error, e.g. `too_many_notes`, `notes_full`,
  `memory_unavailable`, `bad_note`), the tool returns an error with the hub's code and plain message, and the call is not sent again.
- Permission prompts go to the hub as approval cards. The owner's answer comes back to the session.

A tool call returns when the hub confirms it stored the call. If the hub has not confirmed in 10 s, the tool
answers "Queued" and the session carries on. The call is sent again after a reconnect, and the hub stores it once.

## When something is down

| What | What the server does | What the session sees |
|---|---|---|
| Hub down at start, or the connection drops — including a hub that answers the WebSocket upgrade with an HTTP error (404, 5xx) or refuses the connection | Retries after 1 s, 2 s, 4 s, up to every 30 s, and logs "Could not connect to the hub … Trying again in N s." once; while it stays down, at most one "Still can't reach the hub after N tries; still retrying every 30 s." every 5 minutes; when it is back (also on the first connect after a hub that was down at start), "Reconnected to the hub after N s." Only a `rejected` frame stops it: `bad_secret` and `plugin_too_old` at once, `unknown_session` after 2 minutes (below). Frames wait in the hub's queue | `reply` and `report` answer "Queued: Marcel is connecting" (first connect) or "reconnecting" at once |
| Hub takes the connection but never answers hello | Closes it after 15 s and reconnects with the same backoff. Logged like a hub that is down: once, then at most one "Still can't reach the hub…" every 5 minutes, then "Reconnected to the hub after N s." | Same as above |
| Hub drops in the middle of a call | Sends the call again after the reconnect | "Queued" after 10 s |
| A permission prompt is waiting during a reconnect | Sends it again after the reconnect. The answer still reaches the session | Nothing changes |
| Hub goes quiet (nothing for 60 s) | Drops the connection and reconnects | Nothing changes |
| `unknown_session` | Tries again every 5 s for 2 minutes, then logs it and stops | "Marcel is unreachable" once it stops |
| `bad_secret` | Logs it, never retries | "Marcel is unreachable. A deploy wrote a different secret." |
| `plugin_too_old` | Logs it, never retries | "Marcel is unreachable. Update the plugin." |
| No `hub-secret` file, or no session id | Does not connect. It still answers MCP | "Marcel is unreachable", with the reason |

When it gives up (`unknown_session` after 2 minutes, `bad_secret`, `plugin_too_old`) it logs how many queued calls and pending permission prompts it dropped.

Log lines go to stderr, which Claude Code keeps in the session's debug log. They start with `[marcel-channel]`.
The hub raises the owner-facing alert for a wrong secret or an old plugin.

## Why frames wait after the handshake

Claude Code registers the channel about 60 ms after `notifications/initialized` (SP1). A frame handed over before
that can be dropped and still acked, so it would be lost without a trace. The server holds every hub frame, unacked,
for 500 ms after `initialized`, then delivers in `seq` order.

The live check (S-05.2, `tests/live/`) shows a frame queued before hello reaching the session after a spawn (a worker messaged while still starting) and after a respawn (the brain replaced while a message waited).
