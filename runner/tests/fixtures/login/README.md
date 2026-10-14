# Login fixtures (S-03.13)

Recorded on the NUC on 2026-10-14 with Claude Code 2.1.296 (the pinned version). The home path is
`/home/marcel`; the email, org id and org name are placeholders.

| File | What it is |
|---|---|
| `auth-status-logged-in.json` | `claude auth status --json`, logged in (exit code 0) |
| `auth-status-logged-out.json` | the same with no login (exit code **1**, the JSON still on stdout), recorded with an empty `HOME` |
| `daemon-2026-10-14-0251.log` | `~/.claude/daemon.log` 02:51–05:24 UTC: an update restarts the supervisor, its login refresh fails at 02:52 and it has no token until 05:02 |
| `daemon-2026-10-14-1258.log` | the same log 12:58–13:15 UTC: four refresh failures with the token still valid, then the same loss at 13:02, back at 13:15 |
