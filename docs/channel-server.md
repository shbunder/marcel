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
| Pre-allowed tools | The runner pre-allows `mcp__plugin_marcel_marcel__reply` and `__report` at spawn (`--allowedTools`), so a session in `manual` mode does not ask before every reply. A plugin's own `settings.json` cannot do this |

The server has no runtime dependencies.

## What the session gets

- Messages arrive as channel events. `meta.kind` is `user_message`, `steer` or `system_event`.
- Tool `reply(text, in_reply_to?)` for the brain and side threads.
- Tool `report(kind, …)` for workers: `started`, `progress` (needs `text`), `artifact` (needs `artifact`), `done` and `failed` (need `summary`).
  A report that breaks these rules is refused with a plain message before it leaves the session.
- Permission prompts go to the hub as approval cards. The owner's answer comes back to the session.

A tool call returns when the hub confirms it stored the call. If the hub has not confirmed in 10 s, the tool
answers "Queued" and the session carries on. The call is sent again after a reconnect, and the hub stores it once.

## When something is down

| What | What the server does | What the session sees |
|---|---|---|
| Hub down at start, or the connection drops | Retries after 1 s, 2 s, 4 s, up to every 30 s. Frames wait in the hub's queue | `reply` and `report` answer "Queued: Marcel is connecting" (first connect) or "reconnecting" at once |
| Hub takes the connection but never answers hello | Closes it after 15 s, logs it, and reconnects with the same backoff | Same as above |
| Hub drops in the middle of a call | Sends the call again after the reconnect | "Queued" after 10 s |
| A permission prompt is waiting during a reconnect | Sends it again after the reconnect. The answer still reaches the session | Nothing changes |
| Hub goes quiet (nothing for about a minute) | Drops the connection and reconnects | Nothing changes |
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

The live check (S-05.2) must show that a frame queued at hello reaches the session after a spawn and after a respawn.
