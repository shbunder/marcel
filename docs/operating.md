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
| `data/` | The hub's `/data`: the database, `hub-secret`, `memory/` (the brain links into it), `marcel.toml` |
| `state/` | The hub's `/state`: `redeploy.flag` |
| `run/` | The runner's socket, mounted into the hub at `/run/marcel` |
| `brain/` | The brain workspace, rendered from `brain/` |
| `plugins/` | The `marcel-local` marketplace: a built copy of `plugins/marcel/` |
| `workspaces/` | Worker clones and worktrees, and `scratch/` for tasks with no repository |
| `node/` | Node 22 for the plugin's channel server. The system node is not used |

## Install

Once per machine, as root:

```bash
sudo loginctl enable-linger $USER     # user units start at boot and survive logout
```

`/etc/claude-code/managed-settings.json` must allow the channel (ADR-001). It is installed on this NUC:
`channelsEnabled: true` and `allowedChannelPlugins` with `marcel@marcel-local`.

Then, as the owner:

```bash
git clone git@github.com:shbunder/marcel.git ~/marcel/src
cd ~/marcel/src
$EDITOR deploy/.env.local             # see "Secrets"
make -C deploy install
make -C deploy tunnel                 # then run the two sudo lines it prints
```

`make -C deploy install` is safe to run again. It installs node 22, builds the plugin, registers the
`marcel-local` marketplace, writes the hub secret, renders the brain workspace and trusts it,
installs and starts the units, builds and starts the hub, then prints the health check. A unit with
the same name from old Marcel is copied to `~/marcel-v2-units/` before it is replaced.

It stops with one sentence when something is missing: Docker, `claude`, Python 3.12, `.env.local` or
`MARCEL_OWNER_NAME`.

## Secrets: `deploy/.env.local`

Never committed (`.gitignore`). The hub reads all of it; sessions get only `MARCEL_HARRY_TOKEN`
(copied to `~/.config/marcel/session.env`), because every session inherits the runner's environment.

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

After changing it: `make -C deploy deploy` for the hub, `make -C deploy install` for Harry's token.

## Day to day

```bash
make -C deploy health    # units, runner, hub, brain. Exit 1 when the hub is not ok
make -C deploy logs      # hub, runner and supervisor logs, followed
make -C deploy deploy    # rebuild and restart the hub only; sessions keep running
```

## Pairing a phone

```bash
docker exec -it marcel-hub marcel-hub pair
```

It prints a QR code and an 8-letter code that work once, for 10 minutes. Scan it with the app.

## Upgrades

Self-changes the owner approves in the app go through the redeploy flag (`.claude/rules/self-modification.md`).
`marcel-redeploy.path` is enabled only once `deploy/redeploy.sh` exists (S-11.2). Until then an approved
self-change is merged but not deployed: upgrade by hand.

By hand:

```bash
cd ~/marcel/src && git pull --ff-only
make -C deploy install
```

That rebuilds the plugin and the hub and restarts the runner. It never restarts
`marcel-claude-daemon`, which would stop every session. After a Claude Code update, run
`claude respawn --all` rather than restarting the supervisor. For maintenance,
`claude daemon stop --keep-workers` stops the supervisor and leaves sessions running.

## Backups

Back up `~/marcel/data` (the database, memory and its git history next to it, `hub-secret`) and
`deploy/.env.local`. Stop the hub first for a consistent database copy:

```bash
~/marcel/src/deploy/compose.sh stop
tar -C ~/marcel -czf ~/marcel-data-$(date +%F).tar.gz data
~/marcel/src/deploy/compose.sh start
```

To restore, put `data/` back before `make -C deploy install`.

## Triage

| What you see | Look at | Usually |
|---|---|---|
| App says the hub is unreachable | `make -C deploy health`; `systemctl status cloudflared-marcel` | Hub container down: `make -C deploy deploy`. Tunnel down: `sudo systemctl restart cloudflared-marcel` |
| `hub /api/health` says the runner is not answering | `journalctl --user -u marcel-runner -n 50` | The runner refuses to start without Claude's supervisor: `systemctl --user start marcel-claude-daemon` |
| Runner log: supervisor "inside the runner's cgroup" | `claude daemon status` | A supervisor started outside its unit. Stop the session work, then `systemctl --user restart marcel-claude-daemon` |
| Brain: none running | `make -C deploy logs` | `MARCEL_BRAIN_CWD` unset, or the brain workspace is not trusted: run `make -C deploy install` |
| "Marcel is unreachable. A deploy wrote a different secret." | `~/marcel/data/hub-secret` vs `~/.claude/plugins/data/marcel-marcel-local/hub-secret` | Run `make -C deploy install`, which copies it again |
| Nothing starts after a reboot | `loginctl show-user $USER -p Linger` | Linger is off: `sudo loginctl enable-linger $USER` |
| Port 7420 already in use | `ss -ltnp | grep 7420` | Old Marcel's `marcel.service`: `sudo systemctl disable --now marcel.service` |

## When a part is down

- **Hub down:** the app cannot connect; sessions keep running, and their channel retries for two minutes.
- **Runner down:** the hub stays up and its health says the runner is not answering; no new task starts.
  Running sessions keep running under Claude's supervisor.
- **Supervisor down:** every background session has stopped; the runner refuses to start until it is back.
- **Tunnel down:** the app cannot reach the hub away from home; everything on the NUC carries on.
