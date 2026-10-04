# SP3 — Cloud sessions from the NUC — RESULT

**Verdict: PASS.** There is a defined way to create a cloud task, steer it, and get its state and result:
- **create** through `claude --cloud` on a pty (it prints the id and URL, then exits);
- **steer** with `claude -p "<msg>" --cloud <id> --output-format json` (headless, acked, acted on within seconds);
- **state and result** come from **the worker reporting over HTTPS to `marcel-bot.com`** with a scoped token (verified end to end).

There is no CLI for a cloud session's *state*: `agents --json` doesn't list cloud sessions. `ListAgents` (inside a
Claude session) shows coarse state, and `--teleport` shows the log on screen only.

## Question

How does the NUC create a cloud session, talk to it, observe it, get its result, and can the cloud worker reach `marcel-bot.com`?

## What was run

- NUC, Claude Code 2.1.289, Max subscription, 2026-10-04 ~10:16–10:24 UTC (session `session_0168oBNCs8Q2BrHDFu6tYUdh`,
  created from the `shbunder/marcel` checkout).
- `marcel-bot.com` → cloudflared (`tunnel run --url http://localhost:7420 marcel`). The old Marcel is not running
  (origin → 502), so for this test **[`receiver.py`](receiver.py) ran on `127.0.0.1:7420` for ~8 minutes**. It accepts
  only `POST /sp3/report` with a one-off bearer token, and 404s everything else. It was stopped afterwards
  (`marcel-bot.com` → 502 again). The token was generated per run and never committed.
- Screens were captured with `../SP1-bg-channel/pty_screen.py`. Evidence is in [`evidence/`](evidence/) (token removed, home IP pseudonymised).

## Answers

### 1. Create — `claude --cloud "<task>"`

| Invocation | Result |
|---|---|
| stdout not a TTY | `Error: --cloud requires an interactive terminal. Non-interactive invocations (piped stdout, --init-only, --sdk-url) run locally and would silently ignore --cloud.` (rc 1) |
| `-p "<task>" --cloud --output-format json` | `Error: --cloud cannot be combined with --print. Starting a new cloud session with --cloud is interactive only…` (rc 1) |
| **on a pty** | prints `Created cloud session: <title> View: https://claude.ai/code/session_<id>?from=cli&m=0 Resume with: claude --teleport session_<id>` **and exits** (no TUI to drive) |

So `cloud_spawn` = run `claude --cloud "<prompt>"` under a pty (Python `pty.fork`), and parse `session_[A-Za-z0-9]+`
and the URL from the output. The session title is derived from the prompt's first words. The cloud box started
working within ~20 s (its first curl reached the NUC 23 s after the create call).

### 2. Follow-up — `claude -p "<msg>" --cloud <id> --output-format json`

Works **headless** (no TTY needed) and returns an **ack, not the answer**:

```json
{"ok":true,"session_id":"session_0168oBNCs8Q2BrHDFu6tYUdh","url":"https://claude.ai/code/session_0168oBNCs8Q2BrHDFu6tYUdh?from=cli&m=0"}
```

The follow-up ("POST again with step=cli-followup") was **acted on: the report arrived 4.4 s after the send.**
This is `cloud_send`.

### 3. Observability from the NUC

| Path | Result |
|---|---|
| `claude agents --json --all` | **Cloud sessions are not listed** (kinds seen: `background`, `interactive` only) |
| `ListAgents` from a local Remote Control-connected session | ✅ lists it: `SP3 cloud spike [7efd33] · cloud · idle` (coarse `running`/`idle`) |
| `SendMessage` to it from a local session | Accepted (`success: true`), but "not confirmed read … may hold it … one-way". **Not acted on within 5 min**, and nothing in the log teleport rendered. Unreliable for steering. Use `-p --cloud <id>` instead. |

`ListAgents` is a tool *inside* a Claude session, not a CLI. The brain could call it to answer "status?", but the
runner cannot poll it without spending a model turn.

### 4. Reporting back over HTTPS — ✅

The cloud session ran `curl https://marcel-bot.com/health` (→ our 404, so the network was reached) and
`curl -X POST -H 'authorization: Bearer <scoped token>' … https://marcel-bot.com/sp3/report`, which returned **200,
authorized**. Its egress came from `160.79.106.133/135/143` (Anthropic's cloud range). The default cloud environment network
policy **allows `marcel-bot.com`**, with no allowlist change needed.
Side note: in ~8 minutes of exposure an internet scanner probed `GET /.git/config`. The hub's report endpoint must
reject everything without a valid per-task token (as `receiver.py` did).

### 5. `claude --teleport <id>`

On a pty, in a throwaway clone of the repo: `Validating session ✔ → Fetching session logs ✔ → Getting branch info ✔ →
Checking out branch`. It then **renders the conversation** (both follow-ups and the `cli-followup` turn) and resumes the
session locally ("Session resumed without branch: Failed to checkout branch 'claude/sp3-cloud-spike-9s6dt2'", because the cloud
session pushed nothing). **No local JSONL is written** unless the resumed session takes a turn. It also switches the
working tree's branch when the branch exists. So teleport is a human tool, not a transcript API. Never run it in a
live working tree.

## What the plan changes

- **F07 / S-03.6:**
  - `cloud_spawn` = `claude --cloud "<prompt>"` on a pty, parsing the session id and URL.
  - `cloud_send` = `claude -p "<msg>" --cloud <id> --output-format json`, which returns an ack only.
- **State and result:** the cloud worker's prompt (worker-protocol skill) must include the hub URL and a **per-task
  scoped token**. The worker reports `started`/`progress`/`done{summary, artifacts, branch/PR}` by POSTing to
  `https://marcel-bot.com/…`. This is the primary path. A task that is silent past `expected_by` becomes
  `needs_you` with the claude.ai link (B-14 watchdog).
- **No polling path** exists for cloud state from the runner. Do not plan for one. Optionally, the brain may use
  `ListAgents` when asked "status?".
- **Steering:** use `-p --cloud <id>`, never `SendMessage`.
- **Hub security (F04):** `/tasks/{id}/report` requires the per-task token, is rate-limited, and 404s anything
  else. The origin sees scanners within minutes.
- **F14:** the hub must be up behind `marcel-bot.com` before any cloud worker runs. Today the origin is down (502).
