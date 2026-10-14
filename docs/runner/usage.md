# Runner — usage probe

`GET /usage` tells the hub how much of the Claude plan is used: the 5-hour window and the weekly
window, each with the percentage used and when it resets. The hub's usage guard reads it before it
spawns a worker.

## Where the numbers come from

The runner runs `claude -p "/usage" --no-session-persistence`. This makes no model call and costs
no tokens (SP4). It takes 2–3 seconds. It runs with `TZ=UTC`, so reset times always end in `(UTC)`. The runner does it once at start, then every 5 minutes.

`/usage` prints text meant for people:

```
Current session: 4% used · resets Oct 4, 2:29pm (UTC)
Current week (all models): 66% used · resets Oct 9, 1:59am (UTC)
```

The runner reads it with one regex, `USAGE_LINE` in `runner/src/marcel_runner/usage.py`.
"Current session" becomes `five_hour`, "Current week (all models)" becomes `weekly`. Reset times
carry no year: the runner picks the year that puts the time closest to now, and converts it to UTC.
A percentage above 100 is reported as 100.

## What it answers

| Situation | Answer |
|---|---|
| A reading from the last 15 minutes, and the latest attempt worked | 200, `stale: false` |
| The latest attempt failed (claude missing, an error, a timeout, Claude Code logged out, or wording it cannot read) | 200 with the last values, `stale: true` |
| The last good reading is older than 15 minutes | 200 with the last values, `stale: true` |
| No reading has worked since the runner started | 503, `code: no_reading`; `detail` says why the last attempt failed |

When the latest attempt failed, `reason` says why in plain words (runner-api 0.1.13); the next good
reading drops it. A reading that is only old has no `reason`. `reason` never repeats what `claude`
printed, which can name the account: that goes to the runner's log only.

The fields are in `contracts/runner-api.yaml` (`UsageReading`). `source` is always `cli_usage`
today. The status-line source (`statusline`) is not built yet.

Claude Code 2.1.296 and 2.1.299 print the same two lines, plus a per-model weekly line and a "what's contributing"
section, which the runner ignores.

## When Claude Code is logged out

Logged out, or when its login has expired, Claude Code is not on the subscription, and `/usage` prints the
pay-as-you-go cost view (`Total cost: $0.0000 …`) instead. The runner logs:

```
marcel-runner: could not read usage. Claude Code on the NUC is logged out. Log in again: on the NUC run
`claude auth login`. It said: Total cost: …
```

`reason` is the message without the `It said: …` part, word for word the `/health` `login.message`, and `login.state` turns
`lost` ([runner.md](../runner.md#login-is-claude-code-still-logged-in)), so the owner reads one sentence in
both places. Before the first good reading, `/usage` answers 503 `no_reading` with no `reason`: then only
`/health` `login` says it. Log in again with `claude auth login` on the NUC.

## When Claude Code changes the wording

Output that is neither format: the runner does not crash. It keeps the last values, marks them stale, and logs
one line, for example:

```
marcel-runner: could not read usage. Claude Code's /usage output changed: no "Current session"
line was found. Update USAGE_LINE in runner/src/marcel_runner/usage.py and re-record the SP4 fixture.
```

To fix it: record the new output of `claude -p /usage` into
`scratch/spikes/SP4-usage/evidence/usage-p-text.txt` (and the fake claude's copy in
`runner/tests/fake_claude/fixtures/usage/`), then update `USAGE_LINE` until
`runner/tests/test_usage.py` passes.
