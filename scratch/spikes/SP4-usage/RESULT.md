# SP4 — Reading plan usage — RESULT

**Verdict: PASS.** There are two reliable sources of `used_pct` and `resets_at`, neither of which costs a model token:

1. **Primary: `claude -p "/usage" --no-session-persistence`.** Headless, zero model turns, 2–3 s, works in any directory.
   Prints the 5-hour ("session") and weekly windows with reset times.
2. **Push: the status-line JSON.** It carries `rate_limits.{five_hour,seven_day}.{used_percentage,resets_at}` in
   interactive **and `--bg`** sessions, from the first API response on. A status-line command can write it to a file
   on every refresh.

The limit hit could not be observed without burning ~34% of the weekly quota. Its detection strings and job state
come from the 2.1.289 binary (see section 3), not from a live hit.

## Question

Is there a programmatic, reliable read of plan usage (`used_pct`, `resets_at`)? If not, how do we detect hitting the limit?

## What was run

NUC, Claude Code 2.1.289, Max subscription, 2026-10-04 ~10:30 UTC. Sessions in the trusted SP2 sandbox repo, launched
with `../cclean`.

```bash
# status line: dump the JSON Claude Code pipes to the status-line command (passed with --settings; no settings file touched)
S='{"statusLine":{"type":"command","command":"SL_OUT=… python3 statusline_capture.py"}}'
python3 ../SP1-bg-channel/pty_screen.py 45 --send '❯' 'Reply with just the word hi.\r' -- claude --model haiku --settings "$S"
cclean --bg --name sp4-sl --model haiku --settings "$S" -- "Reply with just the word hi."
# /usage
cclean -p "/usage" [--output-format json] [--no-session-persistence]
```

## Answers

### 1. Status-line JSON — has rate limits ✅

The keys on 2.1.289 are `context_window, cost, cwd, exceeds_200k_tokens, fast_mode, model, output_style, prompt_cache,
prompt_id, rate_limits, scratchpad_dir, session_id, session_name, thinking, transcript_path, version, workspace`.

```json
"rate_limits": {
  "five_hour": {"used_percentage": 3,  "resets_at": 1791124200},
  "seven_day": {"used_percentage": 66, "resets_at": 1791511200}
}
```

- `resets_at` is **unix seconds**. `used_percentage` is an integer %.
- **Absent before the session's first API response.** The first two renders had no `rate_limits`, the third
  (after the reply) did. A fresh, idle session therefore knows nothing.
- **Works in `--bg` sessions** (`sl-bg`: 3 renders, `rate_limits` present). So the brain, which is always running, and every worker can
  publish usage for free whenever they make a call.
- Evidence: [`evidence/statusline-input-bg.json`](evidence/statusline-input-bg.json),
  [`evidence/statusline-input-before-first-call.json`](evidence/statusline-input-before-first-call.json),
  and the capture script [`statusline_capture.py`](statusline_capture.py).

### 2. `/usage` — has a non-interactive form ✅ (text; JSON is just a wrapper)

`claude -p "/usage"` runs the slash command locally. `num_turns: 0`, `duration_api_ms: 0`, `total_cost_usd: 0`, so **no
model call**. It took 1.9–3.2 s, and ran fine from `/tmp` (print mode skips the trust check). Output
([`evidence/usage-p-text.txt`](evidence/usage-p-text.txt)):

```
Current session: 4% used · resets Oct 4, 2:29pm (UTC)
Current week (all models): 66% used · resets Oct 9, 1:59am (UTC)
Current week (Fable): 0% used · resets Oct 9, 2am (UTC)
… (a local breakdown: requests, sessions, context size, top skills/subagents)
```

- `--output-format json` puts the same text in `.result` ([`evidence/usage-p-json.json`](evidence/usage-p-json.json)), and
  there are no structured fields. **Parse the text** with
  `^Current (session|week \(all models\)): (\d+)% used · resets (.+) \((\w+)\)$`.
- Use **`--no-session-persistence`**. Without it, every call leaves a session JSONL behind.
- Side effect: `/usage` refreshes `cachedUsageUtilization` in `~/.claude.json`
  (`{fetchedAtMs, utilization: {five_hour|seven_day: {utilization, resets_at (ISO)}}}`), but **throttled**. The first
  call refreshed a 5-day-old cache, and a call ~1 min later didn't. It's handy as a structured cross-check, not as the
  source of truth.

### 3. Hitting the limit (from the binary, not observed live)

- **Daemon job state** (`~/.claude/jobs/<id>/state.json`, mapped from the API error kind):
  | API error | `state` | `needs` |
  |---|---|---|
  | `billing_error` | `blocked` | `usage limit reached — check plan` |
  | `rate_limit` | `blocked` | `rate limited — wait and retry` |
  | `overloaded` | `blocked` | `API overloaded — wait and retry` |
  | `server_error` | `blocked` | `API unavailable — retry` |
  | `authentication_failed` | `blocked` | `login required — run /login` |
  | unknown / `dlp_request_denied` | `failed` | `API error` |

  So a limit hit is **`blocked`**, not `failed`. `agents --json` shows `state: "blocked"` **without**
  `waitingFor: "permission prompt"` (compare SP2). The discriminator is `jobs/<id>/state.json` `needs`.
- **Transcript:** the assistant entry carries `isApiErrorMessage: true` (plus `api_error_status`, e.g. 429 for
  `rate_limit_error`), and its text starts with one of the client's own limit prefixes:
  `"You've hit your"`, `"You've reached your"`, `"You're out of usage credits"`, `"You're out of extra usage"`,
  `"Your org is out of usage · …"`, `"Your seat type doesn't include usage…"`, `"Your usage allocation has been disabled by your admin"`,
  `"Fable 5 requires usage credits"`.
- To confirm on the first real hit, the runner should log the raw `agents --json` row, `state.json` and the last transcript
  entry when it sees `blocked` + `needs ~ /usage limit|rate limited/`. Add those three to the SP2 fixtures.

## What the plan changes

- **F10 usage guard:**
  - Poll `claude -p /usage --no-session-persistence` from the **runner** every 5 min, plus right before spawning a
    worker. Parse the session and weekly lines into `usage_snapshot(window, used_pct, resets_at, source="cli_usage")`.
  - Add an optional `source="statusline"` from a tiny status-line command in the brain and worker settings that
    appends `rate_limits` to a file the runner tails. It's fresher during bursts but silent while idle.
  - Expose `usage` on the runner API (already in `runner-api.yaml`).
- **Detect on hit as a backstop:**
  - `blocked` with `needs` matching `usage limit reached|rate limited` → task `needs_you` (reason `usage_limit`),
    paused until `resets_at`;
  - a transcript `isApiErrorMessage` whose text starts with one of the prefixes above → same.
- **02-architecture "Known risks" #3** is resolved: a programmatic read exists (`/usage` text and the status-line
  `rate_limits`). Note that `/usage` is presentation text and may change wording between versions, so keep the
  regex in one place and give it a contract test against [`evidence/usage-p-text.txt`](evidence/usage-p-text.txt).
