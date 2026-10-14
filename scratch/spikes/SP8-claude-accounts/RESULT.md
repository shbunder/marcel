# SP8 — Several Claude logins on one NUC — RESULT

**Verdict: not finished** (2026-10-14, Claude Code 2.1.296). Step 1 needs a second Claude account to log in, which
only the owner can do (`/login` prints a link to open with that account). What does not need a login was checked.

## What was run

```bash
X=<a fresh folder>
CLAUDE_CONFIG_DIR=$X claude auth status       # {"loggedIn": false, "authMethod": "none", …}
CLAUDE_CONFIG_DIR=$X claude daemon status     # not running; sock dir /tmp/cc-daemon-1000/bcee427e
CLAUDE_CONFIG_DIR=$X claude agents --json --all   # []
```

## Answers so far

- **Each config folder is its own world.** A second `CLAUDE_CONFIG_DIR` has its own login state, its own supervisor
  (a different socket folder: `bcee427e`, against `6298d98a` for `~/.claude`) and its own `claude agents` list. So a
  second account needs its own supervisor unit (`claude daemon run` with that `CLAUDE_CONFIG_DIR`), not only a flag on
  a spawn.
- **Where the runner assumes `~/.claude`** (each would take the account's folder): `transcript.py` (`~/.claude/projects`
  for tails), `resume.py` (`~/.claude/jobs/<short>/state.json` for saved options, `~/.claude/daemon/roster.json`),
  `sessions.py` and `workspaces.py` (the `home` they pass, `~/.claude.json` for trust), `cloud.py` (`home`).
  `claude_env.scrubbed_env` removes `CLAUDE_CONFIG_DIR` on purpose, so today every session uses the owner's account.
- **Not answered:** two logins at once, background sessions under each, channels and `/usage` for each.

## What changes in the plan

- Nothing yet. To finish: the owner runs `CLAUDE_CONFIG_DIR=~/.claude-<name> claude` → `/login` with the second
  account; then steps 2–3. If it works, a runner story adds `account` to `SpawnRequest` and one supervisor unit per
  account; the fallback stays one Linux user per account.
- Whether family members may use the owner's Max login at all is a question of Anthropic's terms for the owner, not
  something this spike can settle.
