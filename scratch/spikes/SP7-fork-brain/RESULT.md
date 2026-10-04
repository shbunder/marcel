# SP7 — Forking the brain for side threads — RESULT

**Verdict: PASS.** `claude --bg --resume <brain> --fork-session` produces a second, independent background session.
It has the brain's history up to the fork and its own channel connection, and it never writes into the brain's
transcript. The fallback (a fresh side session seeded with a summary) is not needed.

One plan assumption is wrong: the fork cannot be told its identity through env
(`MARCEL_CONVERSATION_ID`). The channel server identifies itself by `CLAUDE_CODE_SESSION_ID` instead, and the hub maps it.

## Question

With SP1's channel brain running, can we fork it into a new background session that:
- has the main thread's context;
- has its own channel connection (a different conversation identity);
- does not write into the original?

## What was run

- NUC, Claude Code 2.1.289, 2026-10-04 13:28–13:33 UTC.
- The brain is SP1's option A setup: `sp1-brain` (`f3545f76`), `--bg --channels plugin:marcel@marcel-local --permission-mode manual`,
  in `../SP1-bg-channel/sandbox/brain`. The spike channel server is
  [`../SP1-bg-channel/sp1_channel.py`](../SP1-bg-channel/sp1_channel.py), and [`ask.py`](ask.py) sends one channel message and waits for the
  reply (auto-approving only `reply` prompts).

```bash
python3 ask.py 8794 <events> "Remember this code word for later: GIRAFFE-42. …"                 # main thread
MARCEL_CONVERSATION_ID=side-1 claude --bg --resume f3545f76-… --fork-session --name sp7-side \
  --channels plugin:marcel@marcel-local --permission-mode manual --model sonnet \
  -- "You are now the side thread side-1, forked from the main thread. Wait for channel messages …"
python3 ask.py <fork port> <events> "…what code word were you asked to remember…?"               # fork
python3 ask.py <fork port> <events> "Side-thread-only fact: remember ZEBRA-7. …"                  # fork
python3 ask.py 8794 <events> "…what code words have you been asked to remember so far?…"         # main
```

## Answers

| Check | Result |
|---|---|
| Fork created from a **live** brain | `backgrounded · 3986e1fc · sp7-side`, a **new session id**, and **no** "started a copy" note (that note appears only for `--resume` *without* `--fork-session`; see SP2) |
| Fork has the main thread's context | fork → **"The code word was GIRAFFE-42."** Its transcript opens with the brain's original prompt and contains `GIRAFFE-42` (5×) |
| Fork has its own channel connection | its own MCP server process (pid 3036045, parent = the fork's pty host), `Channel notifications registered`, its own HTTP port. Every event is tagged `session: 3986e1fc-…` |
| Messages to the fork reach only the fork | Q1 and Q2 went to the fork's channel. The main brain didn't react to either |
| Fork does not write into the original | main transcript: 80 lines before the fork. After Q1+Q2 to the fork and Q3 to main it was 86 (only main's own Q3 turn). `ZEBRA` occurs **0** times in the main transcript. Each file carries a single `sessionId` (the fork re-stamps the copied history with its own id) |
| Main stays independent | main → **"Only one code word so far: GIRAFFE-42."** (it doesn't know ZEBRA-7) |
| Both run at the same time | yes. Both `--bg`, both answered on their own channels while the other was live |

**Passes:** two independent background sessions that share history up to the fork.

### What did not work: identity through env

`MARCEL_CONVERSATION_ID=side-1` on the `claude --bg` command line **did not reach** the fork's channel server. Its
environment (`/proc/<pid>/environ`) had no `MARCEL_*` at all, because the daemon spawns workers with its own env. What every
plugin MCP server *does* get, per session:

```
CLAUDE_CODE_SESSION_ID=3986e1fc-278e-4b56-ba17-ad79a8130252     ← distinct per session (main had f3545f76-…)
CLAUDE_JOB_DIR=~/.claude/jobs/3986e1fc
CLAUDE_PROJECT_DIR=…/sandbox/brain
CLAUDE_PLUGIN_ROOT=…/marketplace/marcel     CLAUDE_PLUGIN_DATA=~/.claude/plugins/data/marcel-marcel-local
(+ the plugin's own .mcp.json env)
```

So the channel server identifies itself with `CLAUDE_CODE_SESSION_ID`. The hub already knows which session id is the
brain, which is side thread N, and which is task T, because the runner returns the id from every spawn and fork (the
`backgrounded · <short>` line; the full UUID comes from `agents --json`). The spike server was changed to do exactly
this (`conversation: "session:3986e1fc"`).

Evidence: [`evidence/fork-channel-events.jsonl`](evidence/fork-channel-events.jsonl),
[`evidence/fork-debug-channel-lines.log`](evidence/fork-debug-channel-lines.log).

### Notes

- The fork inherits the brain's workspace, and therefore the brain's project-scoped plugin and settings. Pass
  `--channels plugin:marcel@marcel-local` again on the fork. The daemon keeps it in the fork's `respawnFlags`.
- A fork costs one full context load of the brain's history on its first turn, so side threads are cheap only while the
  brain's context is small. This is one more reason for S-06.2's rollover.
- `/fork` from inside the brain was not tried. The CLI path is deterministic and is the one the runner will use.

## What the plan changes

- **F06 side threads / S-03.5 `fork`:**
  - `runner.fork(brain_session_id, name)` = `claude --bg --resume <uuid> --fork-session --name <name>
    --channels plugin:marcel@marcel-local … -- "<side-thread opening prompt>"`;
  - it returns the new session id, which the hub stores as `conversation.brain_session_id` for that side conversation.
- **F05 channel protocol (S-02.2):** the hello is `{session_id: $CLAUDE_CODE_SESSION_ID, …}`.
  - Drop `MARCEL_SESSION_ROLE` / `MARCEL_TASK_ID` / `MARCEL_CONVERSATION_ID` as env inputs: they cannot be delivered.
  - The hub resolves the session id → role + conversation | task. A hello from an unknown session id is rejected
    (or held until the runner reports the spawn).
- **02-architecture flow 5** stands as written (`--resume <brain> --fork-session`, background, same channel plugin),
  with "own channel connection" meaning its own server process identified by its session id.
