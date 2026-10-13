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
sudo loginctl enable-linger $USER             # user units start at boot and survive logout
sudo systemctl disable --now marcel.service   # old Marcel, if present: it takes port 7420 at boot
```

`/etc/claude-code/managed-settings.json` (root, ADR-001) must allow the channel. The list replaces
Claude Code's default allowlist, so it names every channel plugin that may run:

```json
{
  "channelsEnabled": true,
  "allowedChannelPlugins": [
    {"plugin": "marcel", "marketplace": "marcel-local"},
    {"plugin": "telegram", "marketplace": "claude-plugins-official"},
    {"plugin": "imessage", "marketplace": "claude-plugins-official"}
  ]
}
```

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

It stops with one sentence when something is missing: Docker, `claude`, Python 3.12, `.env.local` or
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

After changing it: `make -C deploy deploy` for the hub. For Harry's token, `make -C deploy install`, then
restart Claude's supervisor when no work is running (`systemctl --user restart marcel-claude-daemon`, which
stops every session; the hub starts the brain again): sessions get the supervisor's environment.

## Day to day

```bash
make -C deploy health    # units, runner, hub, and the whole path (hub → runner → a live brain). Exit 1 when any is not ok
make -C deploy logs      # hub, runner and supervisor logs, followed
make -C deploy deploy    # rebuild and restart the hub only; sessions keep running
```

## Pairing a phone

```bash
docker exec -it marcel-hub marcel-hub pair
```

It prints a QR code and an 8-character code that work once, for 10 minutes. Scan it with the app.

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
`claude respawn --all` rather than restarting the supervisor. Stopping the supervisor any way
(including `claude daemon stop --keep-workers`) stops every session under this unit: systemd ends
the unit's whole process group.

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
