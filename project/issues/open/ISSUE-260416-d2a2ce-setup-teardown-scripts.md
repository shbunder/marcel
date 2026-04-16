# ISSUE-d2a2ce: Setup and teardown scripts with systemd unit templates

**Status:** Open
**Created:** 2026-04-16
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, infrastructure

## Capture
**Original request:** Create scripts/setup.sh, scripts/teardown.sh, and deploy/ systemd unit templates. These were supposed to be delivered by ISSUE-027 but were never created. The Makefile already references them. Setup installs systemd user units (marcel.service, marcel-redeploy.path, marcel-redeploy.service), builds the Docker image, starts everything, and waits for the health check. Teardown reverses this. Both scripts should be OS-aware (Linux-only, graceful message on macOS).

**Follow-up Q&A:**
- Q: Should we track this as a new issue even though ISSUE-027 was already closed? A: Yes — the work was marked done but never implemented.

**Resolved intent:** ISSUE-027 was closed with all tasks marked ✓ but the actual files — `deploy/` unit templates, `scripts/setup.sh`, and `scripts/teardown.sh` — were never created. The Makefile has been pointing at missing scripts since then. This issue delivers the missing pieces: three systemd user-level unit templates (service, redeploy path, redeploy service), a `setup.sh` that checks prerequisites, renders templates, installs units, starts the stack, and waits for the health check to pass, and a `teardown.sh` that reverses all of that cleanly. Both scripts must be OS-aware so they fail gracefully on macOS with a clear message rather than a cryptic error.

## Description

### What's missing

The `deploy/` directory does not exist. Neither do `scripts/setup.sh` or `scripts/teardown.sh`. The Makefile's `setup`, `setup-check`, and `teardown` targets all fail silently with "No such file or directory".

### Design (from docs/self-modification.md)

Three systemd user-level unit templates in `deploy/`:

| Unit | Type | Purpose |
|------|------|---------|
| `marcel.service.tmpl` | oneshot (RemainAfterExit) | Runs `docker compose up -d --build` |
| `marcel-redeploy.path.tmpl` | path | Watches `~/.marcel/watchdog/restart_requested` |
| `marcel-redeploy.service.tmpl` | oneshot | Runs `redeploy.sh` when triggered |

`scripts/setup.sh`:
1. Detect OS — exit with a clear message on non-Linux systems
2. Check prerequisites: `docker`, `docker compose`, `systemctl --user`, user in `docker` group
3. Create `.env` from `.env.example` if missing
4. Render templates with correct absolute paths (repo root, home dir)
5. Install units to `~/.config/systemd/user/`
6. `systemctl --user daemon-reload`
7. Enable + start `marcel.service` and `marcel-redeploy.path`
8. Poll `GET http://localhost:7420/health` until healthy (60s timeout)
9. Support `--check` flag (dry-run: verify prerequisites only, no changes)

`scripts/teardown.sh`:
1. Stop and disable `marcel.service` and `marcel-redeploy.path`
2. Remove unit files from `~/.config/systemd/user/`
3. `systemctl --user daemon-reload`
4. Preserve `~/.marcel/` data (never delete user data)

## Tasks
- [ ] Create `deploy/` directory with three systemd unit templates
- [ ] Create `scripts/setup.sh` with prerequisites check, template rendering, install, health wait, and `--check` flag
- [ ] Create `scripts/teardown.sh` with graceful stop, unit removal, and data preservation
- [ ] Verify `make setup`, `make setup-check`, and `make teardown` all resolve correctly
- [ ] Test `--check` flag reports missing prerequisites clearly

## Relationships
- Follows: [[ISSUE-027-systemd-deploy-infrastructure]] (this delivers what ISSUE-027 was supposed to)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
