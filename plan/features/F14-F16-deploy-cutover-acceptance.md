# F14 — Deployment on the NUC and cutover  (waves 1 and 3)

### S-14.1  Compose, systemd, tunnel                          runs-on: NUC · size: M
depends: S-04.1, S-03.1, SP6   touches: deploy/ docs/operating.md
do:
1. `deploy/docker-compose.yml`:
   - hub on 127.0.0.1:7420;
   - `/data` volume;
   - runner socket mounted;
   - `restart: unless-stopped`.
2. systemd user units:
   - `marcel-runner.service`;
   - `marcel-redeploy.path` and `marcel-redeploy.service`;
   - `loginctl enable-linger`, so they run without a login.
3. Cloudflare: the `marcel-bot.com` ingress points to the hub. Only `/pair`, `/ws`, `/api/*`, `/report`, `/adopt` and
   `/health` are exposed.
4. `make deploy`, `make logs`, `make health`.
5. `docs/operating.md`: install, pairing, upgrades, backups, triage.
done when:
- [ ] A reboot drill on the NUC brings the hub, runner and brain back with no manual steps (the log is in the PR).
- [ ] `curl https://marcel-bot.com/health` returns ok; without a token, `/api/tasks` returns 401.

### S-14.2  Backups                                           runs-on: NUC · size: S
depends: S-14.1   touches: deploy/backup.sh deploy/systemd/
do: A nightly SQLite `.backup` plus a memory git bundle to `~/marcel/backups`, keeping 14 days. A restore is documented
and drilled once.
done when:
- [ ] The restore drill log is in the PR.

### S-14.3  Retire old Marcel                                 runs-on: NUC · size: S
depends: S-14.1, S-06.4 (new Marcel answers on the NUC)   touches: docs/operating.md
behaviours: (decision: retire now)
do:
1. Stop and disable the old `marcel` and `marcel-dev` containers, the `marcel(-dev)-redeploy.path` units and
   `cloudflared-marcel`.
2. Delete the Telegram webhook (`deleteWebhook`).
3. Archive `~/.marcel` to `~/marcel-v2-archive.tar.zst`.
4. Repoint the tunnel to the new hub. Harry's tunnel is untouched.
done when:
- [ ] `docker ps` shows no v2 containers.
- [ ] The Telegram webhook info is empty.
- [ ] Harry's `/health` is still ok.

# F15 — Harry as a connector  (wave 3)

### S-15.1  Add Harry MCP to the plugin                        runs-on: cloud · size: S
depends: S-05.3   touches: plugins/marcel/.mcp.json docs/connectors.md
do: Register Harry (`http://localhost:7430/mcp`, bearer from the environment) as an MCP server for the brain and NUC workers.
Cloud workers use the claude.ai connector if the owner adds one. Document how to add any other connector:
an MCP server in the plugin, or a claude.ai connector.
done when:
- [ ] A live test: the brain lists Harry's tools.

# F16 — End-to-end acceptance  (lead · wave 3)

### S-16.1  v1 acceptance run                                 runs-on: NUC + iPhone · size: M
depends: everything above
do: The owner runs the four "v1 is done" checks from `01-functional-spec.md` on a real iPhone, with the lead
watching. Each check gets a screen recording and the hub log excerpt.
1. Ask for a coding task (cloud) and a NUC task ("check disk usage of all docker volumes") at the same time. Then:
   - ask "status?";
   - follow up on the first ("also add a test").
2. Open the NUC thread live. Steer it, and approve an escalation from the lock-screen push.
3. Find the PR and the report in the Library. Edit `MEMORY.md` in the app, and check the next answer uses it.
4. Watch the giraffe's spots count the active threads. Receive the evening digest.
done when:
- [ ] All four are recorded and pass. Gaps become stories for v1.1.

### S-16.2  Do we need a decision model? (v1.1)               runs-on: NUC · size: S
depends: S-16.1 plus two weeks of real use
do: From the triage log and transcripts, count the brain turns that a typed decision could have
handled without Opus: status questions, follow-up → existing thread, and "is this worth a push".
Estimate the share of plan usage they consumed.
done when:
- [ ] A one-page `project/decisions/` note with the numbers.
- [ ] Only if they make up more than about 25 % of brain usage: a follow-up story to pilot Clef through Workers AI behind the
  `Triage` seam, which needs the owner's OK for a second paid API.
