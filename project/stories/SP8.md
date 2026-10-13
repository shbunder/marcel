---
id: SP8
title: Several Claude logins on one NUC
feature: F00
wave: 4
runs_on: NUC
size: S
depends: 
touches: scratch/spikes/SP8-claude-accounts/
behaviours: B-38
---

# SP8 — Several Claude logins on one NUC

For later, when family members bring their own Claude account (B-38). Not needed while everyone shares the owner's login.

**Question:** can two Claude accounts be signed in on the NUC at the same time, each with its own config folder
(`CLAUDE_CONFIG_DIR`), including background sessions, `claude agents --json`, channels and `/usage`?

**Steps:**
1. `CLAUDE_CONFIG_DIR=~/.claude-emma claude` → `/login` with a second account (over SSH; the login prints a link).
2. Start a background session under each folder at the same time; list them; read `/usage` for each.
3. Note every place the runner assumes `~/.claude` (`sessions.py:601,611`, `workspaces.py:296`, `cloud.py:273`) and that
   `claude_env.scrubbed_env` removes `CLAUDE_CONFIG_DIR` on purpose.

**Pass:** two logins work side by side → a runner story adds `account` to `SpawnRequest`.
**Fallback:** one Linux user per account, each with its own runner.
