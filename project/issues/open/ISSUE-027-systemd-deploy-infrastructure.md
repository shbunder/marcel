# ISSUE-027: Systemd-based deploy infrastructure

**Status:** Open
**Created:** 2026-04-02
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, infrastructure

## Capture
**Original request:** Marcel's self-restart mechanism is broken — `redeploy.sh` runs inside the container and calls `docker compose up -d` which kills the process running it. The user wants a reliable self-restart that is also easy for anyone cloning the repo to set up.

**Follow-up Q&A:**
- Q: What's the most architecturally sound approach? A: Host-side systemd path unit — the container shouldn't manage its own lifecycle.
- Q: Marcel still needs to manage other containers (Plex, etc.). A: Keep the Docker socket for container management; only move self-restart to systemd.
- Q: Can we make this dummy-proof for people cloning the repo? A: Yes — `make setup` as a single entry point.

**Resolved intent:** Replace the broken in-container self-restart with a host-side systemd path unit that watches the restart flag file. Provide a single `make setup` command that installs everything (systemd units + Docker container) so anyone cloning the repo can get Marcel running with one command after filling in `.env`.

## Description

### Current problem

`redeploy.sh` is called from inside the container via `subprocess.Popen`. It runs `docker compose up -d` which recreates the container, killing the script mid-execution. The rollback logic can never run. The health check can never complete.

### Design

**Self-restart** moves to the host via systemd:

```
Marcel writes ~/.marcel/watchdog/restart_requested
  → systemd path unit detects file
  → systemd service runs redeploy.sh on the host
  → redeploy.sh rebuilds/restarts container + health check + rollback
```

**Container management** (Plex, etc.) stays via Docker socket — unchanged.

**Setup** becomes a single command:

```bash
git clone ... && cd marcel
cp .env.example .env   # fill in keys
make setup              # installs systemd units + builds + starts container
```

### Systemd units (user-level, no sudo)

- `marcel.service` — runs `docker compose up -d --build` and health checks
- `marcel-redeploy.service` — runs `redeploy.sh` (triggered by path unit)
- `marcel-redeploy.path` — watches `~/.marcel/watchdog/restart_requested`

All units use `systemctl --user` — no root access needed (user must be in docker group).

## Tasks
- [ ] Create `deploy/` directory with systemd unit templates
- [ ] Create `deploy/setup.sh` — prerequisite checks, template rendering, unit installation
- [ ] Simplify `_restart_watcher` in `main.py` — just write the flag, no subprocess/Docker detection
- [ ] Update `Dockerfile` — remove `docker-compose-plugin` (keep `docker-ce-cli` for managing other containers)
- [ ] Update `docker-compose.yml` — keep Docker socket (needed for Plex etc.)
- [ ] Update `redeploy.sh` for host-side execution (remove `--no-build` path, always full rebuild)
- [ ] Update `Makefile` — add `setup` and `teardown` targets
- [ ] Update `install.sh` to integrate with new setup
- [ ] Update `project/CLAUDE.md` — new restart instructions for Marcel agent
- [ ] Update `docs/self-modification.md` — new architecture
- [ ] Run tests and lint
- [ ] Close issue with version bump

## Relationships
- Related to: [[ISSUE-024-agent-reimplementation-memory-architecture]] (fixup that exposed the broken restart)

## Implementation Log
