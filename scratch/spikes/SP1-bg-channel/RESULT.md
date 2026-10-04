# SP1 — A custom channel in an unattended background session — RESULT

**Verdict: FAIL as specified. Decision needed (see the end).** The channel protocol and the permission relay work end
to end, but only when the session shows the development-channels dialog and someone accepts it. A `claude --bg`
session never shows that dialog. It silently drops every dev-channel entry, and the daemon strips the flag
from its respawn flags. Accepting the dialog persists nothing, so the plan's **fallback 1 is ruled out**.

## Question

Can a `claude --bg` session load our custom two-way channel, with permission relay, through
`--dangerously-load-development-channels`, with no human at a terminal?

## What was run

- NUC, Claude Code **2.1.289**, Max subscription, 2026-10-04 09:12–09:20 UTC. Sessions started through
  [`../cclean`](../cclean) (clean env, like a systemd unit).
- [`sp1_channel.py`](sp1_channel.py): a stdlib-only Python MCP stdio server. It declares
  `experimental: {"claude/channel": {}, "claude/channel/permission": {}}`, has a `reply` tool, and an HTTP side
  door standing in for the hub (`POST /message`, `POST /permission`, `GET /events`). It speaks the protocol of the
  official fakechat/telegram plugins:
  - in: `notifications/claude/channel {content, meta{chat_id,message_id,user,ts}}`;
  - from Claude Code: `notifications/claude/channel/permission_request {request_id, tool_name, description, input_preview}`;
  - answer: `notifications/claude/channel/permission {request_id, behavior: allow|deny}`.
- Brain workspace: `sandbox/brain` (its own git repo, trusted once with `../SP2-worker-control/accept_trust.py`).
- [`pty_screen.py`](pty_screen.py) drives an interactive `claude` on a pty and can press keys when a regex appears.
- `--debug-file` on every run. Channel-related lines are in [`evidence/`](evidence/). The full debug logs stay in
  the gitignored `sandbox/` because they include the owner's permission rules.

```bash
# 1. as written in the plan — FAILS before init: the flag is variadic and eats the prompt
cclean --bg --name sp1 --mcp-config sp1-mcp.json --dangerously-load-development-channels server:sp1 "wait for messages"
#   → state "failed", detail "exit 1 before init — …entries must be tagged: wait for messages …"
# 2. fixed with `--` — starts, MCP connects, but the channel is not registered
cclean --bg --name sp1 --mcp-config sp1-mcp.json --dangerously-load-development-channels server:sp1 -- "wait for messages"
curl -XPOST localhost:8791/message -d '{"text":"… what is 6 times 7?"}'      # → nothing, ever
# 3. same, interactive on a pty, pressing Enter on the dialog — WORKS
python3 pty_screen.py 75 --send 'Iamusingthisforlocaldevelopment' '\r' -- claude --mcp-config … --dangerously-load-development-channels server:sp1
# 4. relay test: interactive, --permission-mode manual, approve via POST /permission — WORKS
# 5. packaged as a plugin (--plugin-dir marketplace/marcel), in --bg, with --channels and with the dev flag — both skipped
```

## Answers

### Does the startup confirmation block a `--bg` session?

It neither blocks nor gets accepted. `claude agents --json` shows no `waitingFor`, and the session runs its prompt
and goes idle. But the debug log says:

```
[WARN] [jobs] stripped non-allowlisted respawnFlags token(s) from persisted job state: --dangerously-load-development-channels server:sp1
[DEBUG] MCP server "sp1": Channel notifications skipped: server sp1 not in --channels list for this session
```

The MCP server starts and its `reply` tool is available, but inbound `notifications/claude/channel` are dropped.
The session never sees the message, and nothing on the screen or in `agents --json` says so.

In an interactive session the same flags show
`WARNING: Loading development channels … 1. I am using this for local development / 2. Exit`.
After accepting, the log says `Channel notifications registered`, and messages arrive.

### Does accepting it once persist?

**No.** I diffed `~/.claude.json`, `~/.claude/settings.json` and `settings.local.json` before and after
accepting. Nothing related to channels was written: no settings key and no per-entry approval. Each new process has to accept again,
and a `--bg` process can't, because its startup dialogs never run. Fallback 1 ("accept once with `claude attach`")
cannot work: by the time you can attach, the session has already skipped the channel.

### Messages in/out and relayed approval (interactive, after the dialog)

| Step | Result |
|---|---|
| `POST /message` → `<channel source="sp1">` in the session | arrived (09:16:08.392) |
| `reply` tool → our server | "6 × 7 = 42" at +4.0 s |
| Bash prompt → `permission_request` | `{"request_id":"cjtgb","tool_name":"Bash","description":"Write date to file and print it","input_preview":"{ \"command\": \"date > relay-test.txt && cat relay-test.txt\", … }"}` |
| `POST /permission {cjtgb, allow}` | debug `cjtgb → allow (matched pending)`, and the command ran |
| `reply` itself, in `manual` mode | **also prompts** (`mcp__sp1__reply`). The plugin's settings must allow its own tools. |

Request ids are 5 lowercase letters (`[a-km-z]{5}`, the same regex Telegram uses for "yes abcde" text replies).
Evidence: [`evidence/interactive-relay-events.jsonl`](evidence/interactive-relay-events.jsonl),
[`evidence/sp1i-debug-channel-lines.log`](evidence/sp1i-debug-channel-lines.log).

### Packaged as a plugin (`plugin:marcel@marcel-local`), in `--bg`

| Flag | Debug reason |
|---|---|
| `--channels plugin:marcel@marcel-local` (via `--plugin-dir`) | `you asked for plugin:marcel@marcel-local but the installed plugin is marcel@inline` |
| `--dangerously-load-development-channels plugin:marcel@marcel-local` | `not in --channels list for this session` (stripped, as above) |

`--plugin-dir` plugins have the marketplace `inline`. To install it from a real local marketplace I would have to write
under `~/.claude/plugins`, which this run deliberately avoided. The gate after that is still the allowlist (next section).

### How the gate actually works (from the 2.1.289 binary)

```
register(server):
  first-party provider? channels feature on? policySettings not blocking (channelsEnabled)?
  server in this session's --channels list?            ← dev entries only get here via the dialog
  if plugin entry and not dev:
     installed plugin's marketplace must match
     {plugin, marketplace} ∈ (policySettings.allowedChannelPlugins  — if set, replaces the default
                              else Anthropic's default allowlist)
  server: entries are always dev-only
```

The startup code adds the dev entries **only inside `DevChannelsDialog.onAccept`**. Background sessions skip startup
dialogs, so in `--bg` there are exactly two ways to register a channel:

1. A plugin on **Anthropic's default allowlist** (for example `telegram@claude-plugins-official`) via `--channels`.
2. Our plugin, installed from a marketplace, listed in **managed settings**:
   ```json
   // /etc/claude-code/managed-settings.json   (root-owned; policy for every claude on the NUC)
   { "channelsEnabled": true,
     "allowedChannelPlugins": [ {"plugin": "marcel", "marketplace": "marcel-local"},
                                {"plugin": "telegram", "marketplace": "claude-plugins-official"} ] }
   ```
   then `claude --bg --channels plugin:marcel@marcel-local -- "<prompt>"`. Setting the list *replaces* the default
   allowlist, which is why Telegram is re-listed. This is an Anthropic-documented admin control (the settings schema
   describes `allowedChannelPlugins` as "Managed-org allowlist of channel plugins … Requires channelsEnabled: true").
   **Not tested**: it needs root and writes outside `scratch/`.

### Other findings

- **The plan's command line is wrong.** `--dangerously-load-development-channels <servers...>` (and `--channels`) are
  variadic. The prompt must come after `--` or before the flag. Otherwise the session fails before init
  (`state: failed`, readable `detail` in `~/.claude/jobs/<id>/state.json`).
- `agents --json` `state: blocked` without `waitingFor` (and `jobs/<id>/state.json` `needs`) also means **"the model
  ended its turn asking for input"** ("waiting for user messages" / needs "send a message to proceed"). The daemon
  state is partly model-narrated. Added to SP2's state table.
- Every MCP server gets one log line, either `Channel notifications registered` or `… skipped: <reason>`. The runner
  can check that line (with `--debug-file`, or `--debug mcp`) to fail loudly instead of silently.

## Options (needs the owner's decision)

| Option | What it takes | Pros | Cons |
|---|---|---|---|
| **A. Managed-settings allowlist** (new, not in the plan) | One-time root write of `/etc/claude-code/managed-settings.json` on the NUC; install the marcel plugin from a local marketplace; brain and workers start with `--channels plugin:marcel@marcel-local` | Keeps the architecture unchanged: real `--bg` sessions, two-way channel, permission relay. Supported admin knob, no screen-scraping | Policy file applies to every `claude` on the NUC and replaces the default channel allowlist. Still untested (next step: test it) |
| **B. Runner-owned pty instead of `--bg`** (new) | Runner launches an interactive `claude` on a pty/tmux and presses Enter on the dev-channel dialog (proven above) | Works today, no root, no policy change | Leaves the daemon: `kind: interactive` in `agents --json`, so no `claude stop/respawn/attach`; the runner supervises and restarts it. Relies on matching a warning dialog meant for humans |
| **C. Plan fallback 2** | `PermissionRequest` hook → hub (blocking) for approvals; `crossSessionInbound: accept` + `SendMessage` for steering | Works in `--bg` without channels | Loses the push channel for user messages (the hub must inject into sessions another way). Untested; more moving parts |
| **D. Plan fallback 3** | Official Telegram channel as the brain's transport | On the default allowlist, works in `--bg` today | Brain I/O goes through Telegram, not the app. Workers still need A, B or C |

Recommendation: **A**, then fall back to **B** for the brain if the policy file turns out to have side effects. SP7 (fork the
brain) waits on this, because it needs a running channel brain.

## What the plan changes (whichever option)

- Fix the command line in 03-spikes / F05 / F06: prompt after `--`.
- The channel server must allow its own tools (`reply`, `report`) in the plugin's settings, or `manual`-mode
  sessions prompt for every reply.
- Runner: check the `Channel notifications registered|skipped` debug line at startup and fail the spawn loudly
  (Core principle: Human-readable — "the brain can't hear you" must never be silent).
- 02-architecture "Known risks" #1: the confirmation does not block. It silently disables the channel in `--bg`.
