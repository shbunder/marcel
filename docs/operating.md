# Operating Marcel on the NUC

Marcel runs on the NUC as four parts:

| Part | What runs it | Where |
|---|---|---|
| Claude's supervisor (`claude daemon run`) | user unit `marcel-claude-daemon.service` | native |
| Runner | user unit `marcel-runner.service` | native, from `~/marcel/src/runner` |
| Hub | Docker container `marcel-hub` (`restart: unless-stopped`) | `127.0.0.1:7420` |
| Brain | a `claude --bg` session the hub has the runner keep alive | `~/marcel/brain` |

The Cloudflare tunnel `marcel` forwards `https://marcel-bot.com/api/*` to the hub. Everything else
gets a 404, so the channel (`/channel`) is reachable from the NUC only.

Everything lives under `~/marcel`:

| Folder | What it is |
|---|---|
| `src/` | The deployed checkout of `shbunder/marcel` (`main`). Run every `make -C deploy …` from here |
| `data/` | The hub's `/data`: the database, `hub-secret`, `memory/` (the brain links into it), `marcel.toml`, `avatars/` (drawn avatars; safe to delete) |
| `state/` | The hub's `/state`: `redeploy.flag` (the hub asks for a redeploy), `redeploy.result` (how it went, until the hub has told the owner; then `redeploy.result.told`), `redeploy.log` |
| `run/` | The runner's socket, mounted into the hub at `/run/marcel` |
| `brain/` | The brain workspace, rendered from `brain/` |
| `plugins/` | The `marcel-local` marketplace: a built copy of `plugins/marcel/` |
| `workspaces/` | Worker clones and worktrees, and `scratch/` for tasks with no repository |
| `node/` | Node 22 for the plugin's channel server. The system node is not used |

## Install

Once per machine, as root:

```bash
sudo loginctl enable-linger $USER             # user units start at boot and survive logout
sudo systemctl disable --now marcel.service   # old Marcel, if present: it takes port 7420 at boot
```

`/etc/claude-code/managed-settings.json` (root, ADR-001) must allow the channel. The list replaces
Claude Code's default allowlist, so it names every channel plugin that may run. The file to install is
`deploy/managed-settings.json`:

```bash
sudo install -m 644 deploy/managed-settings.json /etc/claude-code/managed-settings.json
```

It also sets `DISABLE_AUTOUPDATER=1` for every `claude` on the machine, the owner's own included (see
"Upgrading Claude Code"). That part is optional: without it, Marcel's units still turn updates off for
Marcel's sessions.

Then, as the owner:

```bash
git clone git@github.com:shbunder/marcel.git ~/marcel/src
cd ~/marcel/src
$EDITOR deploy/.env.local             # see "Secrets"
make -C deploy install
make -C deploy tunnel                 # then run the two sudo lines it prints
```

On a new install, set up the owner once: it makes you the owner (`MARCEL_OWNER_NAME`, the person
whose Claude login the NUC uses), your agent (`MARCEL_AGENT_NAME`, else Marcel), and prints a QR code
that pairs your first phone. The hub then starts the agent's brain. It is safe to run again, and it
reuses an agent an older `agent create` made:

```bash
docker exec -it marcel-hub marcel-hub setup
make -C deploy health                 # waits up to a minute for "hub /internal/health" to say ok
```

Family members are added from the app (Settings › People), not here.

`make -C deploy install` is safe to run again. It installs node 22, builds the plugin, points the
`marcel-local` marketplace at `~/marcel/plugins`, writes the hub secret, renders the brain workspace,
installs the plugin in it and in `workspaces/scratch` and trusts both, installs and starts the units,
builds and starts the hub, then prints the health check. The deployed plugin's version is
`<version>+<commit>`: Claude Code caches a plugin by version, so each deploy gets a fresh copy, and
install stops if a folder did not get it. A unit with
the same name from old Marcel is copied to `~/marcel-v2-units/` before it is replaced.

It installs the pinned Claude Code first when it is missing or another version. It stops with one sentence when
something is missing: Docker, Python 3.12, `.env.local` or
`MARCEL_OWNER_NAME`, or when it is not run from `~/marcel/src` (the units name that path). It warns when
linger is off or old Marcel's `marcel.service` is enabled.

## Secrets: `deploy/.env.local`

Never committed (`.gitignore`). The hub reads all of it. The runner and Claude's supervisor get only
`MARCEL_HARRY_TOKEN` (copied to `~/.config/marcel/session.env`), so no other secret is in a session's
environment. That covers the environment only: sessions run as the owner and can read the file itself.

| Name | Needed | What |
|---|---|---|
| `MARCEL_OWNER_NAME` | yes | The owner's first name |
| `MARCEL_AGENT_NAME` | no | Default `Marcel` |
| `MARCEL_TZ` | no | Default `Europe/Brussels`. Digests, schedules and the usage pause use it |
| `MARCEL_GITHUB_TOKEN` | no | Fine-grained, **read-only** (Pull requests, Checks, Commit statuses: read; ADR-002). Unset: PR cards say the token is missing |
| `MARCEL_HARRY_TOKEN` | no | Harry's bearer token (`docs/connectors.md`). Unset: sessions run without Harry's tools |
| `MARCEL_NTFY_SERVER` | no | e.g. `https://ntfy.sh`. Unset: no ntfy pushes |
| `MARCEL_APNS_KEY`, `_KEY_ID`, `_TEAM_ID`, `_TOPIC` | no | All four or none (`hub/README.md`). The key's text on one line with `\n`. Never the `.p8` file in the repo or image |
| `MARCEL_APNS_SANDBOX` | no | `true` only for an app run from Xcode |

To get pushes on the phone (without them the owner sees each notice only in the app, e.g. how a redeploy went),
add one or both to `deploy/.env.local`:

```bash
# ntfy
MARCEL_NTFY_SERVER=https://ntfy.sh
# APNs: all four from the Apple developer site (Keys); the .p8 file's text on one line, \n for its line breaks
MARCEL_APNS_KEY=-----BEGIN PRIVATE KEY-----\n<the key's lines>\n-----END PRIVATE KEY-----
MARCEL_APNS_KEY_ID=<10 characters, as in AuthKey_<id>.p8>
MARCEL_APNS_TEAM_ID=<the team id, Membership details>
MARCEL_APNS_TOPIC=<the app's bundle id, e.g. com.<you>.marcel>
MARCEL_APNS_SANDBOX=false   # true only for an app run from Xcode
```

After changing it: `make -C deploy deploy` for the hub. For Harry's token, `make -C deploy install`, then
restart Claude's supervisor when no work is running (`systemctl --user restart marcel-claude-daemon`, which
stops every session; the hub starts the brain again): sessions get the supervisor's environment.

## Day to day

```bash
make -C deploy health    # units, runner, hub, and the whole path (hub → runner → a live brain); exit codes below
make -C deploy logs      # hub, runner and supervisor logs, followed
make -C deploy deploy    # rebuild and restart the hub only; sessions keep running
```

`deploy/health.sh` (`make -C deploy health`) exits with:

| Code | Means | Do |
|---|---|---|
| 0 | Everything is up: both units, the runner, the hub, and a live brain | Nothing |
| 1 | Something is down | Read the lines above it, then the troubleshooting table below |
| 2 | Only Claude Code's version drifted: everything runs, but the installed `claude` is not the pinned one, so the runner starts no new sessions | Run `deploy/install-claude.sh` (or `make -C deploy install`). Nothing needs rolling back: `deploy/redeploy.sh` treats 2 as "not a broken deploy" |

## Pairing a phone

```bash
docker exec -it marcel-hub marcel-hub pair
```

It prints a QR code and an 8-character code that work once, for 10 minutes. Scan it with the app.

## Upgrades

### Self-changes

A self-change the owner approves in the app and merges on GitHub deploys itself (`.claude/rules/self-modification.md`):

1. The hub writes the merge commit to `~/marcel/state/redeploy.flag`.
2. `marcel-redeploy.path` sees it and starts `marcel-redeploy.service`, which runs `deploy/redeploy.sh`.
3. The script refuses a flag that is not one full commit id, or a commit that is not on `main` on GitHub. It
   fast-forwards `~/marcel/src` to it and runs `make -C deploy install`.
4. It runs `deploy/health.sh` every 5 s for up to 120 s. Healthy: done. Exit 2 (only Claude Code's version drifted):
   done, nothing reverted. Still not healthy: it reverts the commit (`git revert`, `-m 1` for a merge), pushes the
   revert to `main`, deploys again and checks again.
5. It writes the outcome to `~/marcel/state/redeploy.result`. The hub posts it in the owner's main conversation
   and pushes it unless the change simply went in.

| The owner reads | Means | Do |
|---|---|---|
| "Marcel is updated with …" | Deployed and healthy | Nothing |
| "Marcel is updated … Claude Code is not the version …" | Deployed; only the Claude version drifted | `make -C deploy install` |
| "An update was rolled back" | Not healthy in 120 s; reverted on GitHub and on the NUC | Read `~/marcel/state/redeploy.log`; fix it in a new PR |
| "Marcel may be down" | The revert failed, or Marcel is still unhealthy after it | `make -C deploy health`, `~/marcel/state/redeploy.log` |
| "An update was not deployed" | The flag or the commit was refused; nothing changed | The reason says why |

The service gets 30 minutes in all (`TimeoutStartSec`). One redeploy runs at a time; a commit the deployed copy
already has does nothing. The push uses `~/marcel/src`'s own git login (SSH to GitHub). If it fails, the revert is
on the NUC only, and the message says so.

`MARCEL_REDEPLOY_WAIT` (seconds, default 120) changes how long health is given. `MARCEL_REDEPLOY_BRANCH`
(default `main`) is **for drills only**: it points the script at a throwaway branch, so a deliberately broken commit
never lands on `main`. Set either with `systemctl --user edit marcel-redeploy.service` (`[Service]
Environment=…`), and remove it after the drill.

### By hand



```bash
cd ~/marcel/src && git pull --ff-only
make -C deploy install
```

That rebuilds the plugin and the hub and restarts the runner. It never restarts
`marcel-claude-daemon`, which would stop every session. After a Claude Code update, run
`claude respawn --all` rather than restarting the supervisor. Stopping the supervisor any way
(including `claude daemon stop --keep-workers`) stops every session under this unit: systemd ends
the unit's whole process group.

## Upgrading Claude Code

Marcel is tested with one Claude Code version: `runner/claude-version` (2.1.296 today). `make -C deploy install`
installs exactly it (`deploy/install-claude.sh`, Claude Code's installer with the version), and both user units set
`DISABLE_AUTOUPDATER=1`, which the sessions they start inherit.

The `claude` binary is shared with the owner's own sessions, which still update it unless
`deploy/managed-settings.json` is installed (its `env` turns updates off for everyone). When it changes anyway,
the runner says so and refuses: `make -C deploy health` shows "Claude Code 2.1.301 is installed, but Marcel is
tested with 2.1.296 …" and exits 2 (not 1: nothing is broken, see the exit codes above), and every spawn,
resume, fork and cloud start answers `422 claude_version`. Running sessions carry on. Fix it with
`deploy/install-claude.sh` or `make -C deploy install`, which put the pinned version back.

Installing a version does not move Claude's running supervisor (`marcel-claude-daemon.service`) onto it: it keeps
the version it started with until it restarts, and restarting it stops every session. `install-claude.sh` says so
when it installed something; restart it when no work is running (the hub starts the brain again).

To move to a new version:

1. In a PR, change `runner/claude-version`.
2. `make -C runner check`, then on the NUC the live tests (`make -C runner test-live`, "Live checks").
3. Merge, then `make -C deploy install`.

## Backups

Every night at 03:30 UTC `marcel-backup.timer` runs `deploy/backup.sh` (by hand: `make -C deploy backup`). It writes
`~/marcel/backups/<date>/`, owner-only, and keeps the newest 14 dates:

| File | What |
|---|---|
| `marcel.db` | The hub's database, through SQLite's online backup (consistent while the hub runs), integrity-checked |
| `memory.bundle` | The memory repo's history (every branch), as one git bundle |
| `memory.tar.gz` | The memory files as they are, including what the brain wrote and nothing has committed yet |
| `artifacts.tar.gz` | The artifact versions the database points to (the Items tab) |
| `hub-secret`, `adopt-token.sha256`, `usage-guard.json`, `digests.json`, … | Every other file in `~/marcel/data`, except `triage.log` |

A failed run also leaves `~/marcel/state/backup.result` (`{"outcome": "failed", "reason", "at"}`), which the hub turns
into one message and push to the owner ("The nightly backup failed: …"), then renames to `backup.result.told`. A good
run writes nothing. A backup that never ran at all (the timer off, the NUC down at 03:30) leaves no file: `make -C
deploy health` warns when the newest backup is over 36 h old.

Not in it: `deploy/.env.local` (keep your own copy of the secrets), the brain folders (re-rendered by
`make -C deploy install` and the runner) and worker clones. `MARCEL_BACKUPS` and `MARCEL_BACKUP_DAYS` change where
and how long.

**When a backup fails** the unit fails and nothing else changes; the day's earlier copy stays. `make -C deploy
health` prints a `backup` line: `WARNING` when there is no backup or the newest is over 36 hours old (it never changes
the exit code). Look at `journalctl --user -u marcel-backup -n 50` and `systemctl --user list-timers marcel-backup`.

### Restore

Every session stops for the restore, so nothing writes to the data folder while it is replaced. Tasks started after
the backup are not in the restored database; their worker folders stay where they were. The brain starts a fresh
session afterwards.

```bash
B=~/marcel/backups/<date>
~/marcel/src/deploy/compose.sh stop                          # the hub
systemctl --user stop marcel-runner marcel-claude-daemon     # the runner and every session
systemctl --user stop marcel-redeploy.path marcel-backup.timer   # no redeploy or backup halfway
mv ~/marcel/data ~/marcel/data.before-restore                # keep what was there
mkdir ~/marcel/data && cd ~/marcel/data
find $B -maxdepth 1 -type f ! -name '*.tar.gz' ! -name memory.bundle -exec cp -p {} . \;
tar -xzf $B/memory.tar.gz && tar -xzf $B/artifacts.tar.gz
git --git-dir=$HOME/marcel/data/memory.git init -q -b main  # this and the next two: only if $B has memory.bundle
git --git-dir=$HOME/marcel/data/memory.git fetch -q --update-head-ok $B/memory.bundle 'refs/heads/*:refs/heads/*'
git --git-dir=$HOME/marcel/data/memory.git --work-tree=$HOME/marcel/data/memory reset -q   # history back; files as backed up
make -C ~/marcel/src/deploy install                          # starts everything stopped above again
```

Drilled on 2026-10-14 with these steps into a scratch folder: the files, the memory (with the brain's uncommitted
notes), its history and all 55 artifact versions matched the live ones, and a throwaway hub on another port answered
`/api/health`, showed the owner's agent and its conversation, and served the board's current and older versions. The
live path (stopping the units, `make install`) was not drilled.

## Triage

| What you see | Look at | Usually |
|---|---|---|
| App says the hub is unreachable | `make -C deploy health`; `systemctl status cloudflared-marcel` | Hub container down: `make -C deploy deploy`. Tunnel down: `sudo systemctl restart cloudflared-marcel` |
| `hub /api/health` says the runner is not answering | `journalctl --user -u marcel-runner -n 50` | The runner refuses to start without Claude's supervisor: `systemctl --user start marcel-claude-daemon` |
| Runner log: supervisor "inside the runner's cgroup" | `claude daemon status` | A supervisor started outside its unit. Stop the session work, then `systemctl --user restart marcel-claude-daemon` |
| Marcel says "Claude Code was updated to X; N sessions ended" | `make -C deploy health` (exits 2) | Another `claude` updated the shared binary and the supervisor restarted. Run `deploy/install-claude.sh`, then retry the failed quests from the app. Install `deploy/managed-settings.json` so updates stay off for every `claude` on the NUC |
| `hub /internal/health`: brain "No brain session yet" | `docker exec marcel-hub marcel-hub agent list`; `make -C deploy logs` | No agent yet: `marcel-hub setup` (see Install). `MARCEL_BRAIN_CWD` unset, or the brain workspace is not trusted: run `make -C deploy install` |
| `hub /internal/health`: brain "not connected" or "sent nothing for over 45 s" | `curl -s --unix-socket ~/marcel/run/runner.sock http://runner/sessions`; `make -C deploy logs` | The brain's `claude` process died or hangs. The hub starts a new one when the runner shows it gone; a hung one: ask for a fresh start from the app (Settings) |
| "Marcel is unreachable. A deploy wrote a different secret." | `~/marcel/data/hub-secret` vs `~/.claude/plugins/data/marcel-marcel-local/hub-secret` | Run `make -C deploy install`, which copies it again, then `claude respawn --all`: after a wrong secret the channel never retries |
| Nothing starts after a reboot | `loginctl show-user $USER -p Linger` | Linger is off: `sudo loginctl enable-linger $USER` |
| Port 7420 already in use | `ss -ltnp | grep 7420` | Old Marcel's `marcel.service`: `sudo systemctl disable --now marcel.service` |

## When a part is down

- **Hub down:** the app cannot connect; sessions keep running, and their channel reconnects with backoff
  (at most 30 s apart) until the hub is back.
- **Runner down:** the hub stays up and its health says the runner is not answering; no new task starts.
  Running sessions keep running under Claude's supervisor.
- **Supervisor down:** every background session has stopped; the runner refuses to start until it is back.
- **Tunnel down:** the app cannot reach the hub away from home; everything on the NUC carries on.

## Live checks

Run by hand on the NUC; they start real `claude` sessions on the owner's plan (a few cents of
`haiku`). Each needs Claude's supervisor up (`marcel-claude-daemon.service`), the owner logged in,
and `make -C deploy install` done (node 22 in `~/marcel/node`, the trusted `workspaces/scratch`
with the plugin).

```bash
make -C runner test-live    # spawn to done, tail, stop, the cap, reply in manual mode after spawn, fork, resume
```

`MARCEL_LIVE_CWD` (default `~/marcel/workspaces/scratch`), `MARCEL_LIVE_MODEL` (default `haiku`) and
`MARCEL_LIVE_NODE_BIN` (default `~/marcel/node/bin`) change what they use. They start their own runner
on a temporary socket with a cap of 1, so the deployed runner and the brain are not touched.

The channel's live check runs against the **deployed** hub and runner (not a runner of its own):

```bash
cd plugins/marcel/channel && npx vitest run --config tests/live/vitest.config.ts
```

It needs Docker access to `marcel-hub` (it pairs a device and removes it at the end), the owner's adopt token in
`~/.marcel/adopt-token` (`marcel-hub adopt-token`, made once; the test never rotates it) and the hub secret. On the phone
it shows: an adopted quest and a short worker quest (archived at the end), one Write approval card it answers itself,
and Marcel's brain replaced once (it stops the brain on purpose to check a message waits for the new one).

## Still open on this NUC

Checks the stories left for the first install. Each is recorded on the build board when done.

- Drills (S-14.1): a reboot brings the hub, runner and brain back with no manual steps; both units
  started cold give a healthy runner within 30 s; the supervisor is still active after 10 idle minutes;
  through the tunnel `/api/health` is ok, `/api/tasks` is 401 and `/channel` is 404
  (`curl --path-as-is` for `/api/../channel` too).
- Branch protection on `shbunder/marcel` `main`: only the owner merges, no direct or force pushes
  (S-11.1, ADR-005). Until it is on, a session whose git login can push could land unapproved code.
- Harry from a session: `cd plugins/marcel/tools && uv run pytest -m live tests/test_harry_live.py` (S-15.1).
- First-run checks (board log `lead-nuc-checks`): transcript folder naming (S-03.4), the trust dialog and
  `~/.claude.json` keys on a real clone (S-03.5), one real cloud spawn and send (S-03.6), and
  `make -C plugins/marcel/tools test-live` recorded in `project/lessons/brain-skills-eval.md` (S-05.5).
