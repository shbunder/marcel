# ISSUE-d2a2ce: Setup and teardown scripts with systemd unit templates

**Status:** Closed
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
- [✓] Create `deploy/` directory with three systemd unit templates
- [✓] Create `scripts/setup.sh` with prerequisites check, template rendering, install, health wait, and `--check` flag
- [✓] Create `scripts/teardown.sh` with graceful stop, unit removal, and data preservation
- [✓] Verify `make setup`, `make setup-check`, and `make teardown` all resolve correctly
- [✓] Test `--check` flag reports missing prerequisites clearly

## Relationships
- Follows: [[ISSUE-027-systemd-deploy-infrastructure]] (this delivers what ISSUE-027 was supposed to)

## Implementation Log

### 2026-04-16 - LLM Implementation
**Action**: Created systemd unit templates and setup/teardown scripts
**Files Modified**:
- `deploy/marcel.service.tmpl` — Created: oneshot service unit for `docker compose up -d --build`
- `deploy/marcel-redeploy.path.tmpl` — Created: path unit watching `restart_requested` flag
- `deploy/marcel-redeploy.service.tmpl` — Created: oneshot service triggering `redeploy.sh`
- `scripts/setup.sh` — Created: OS check, prereq checks, template rendering, systemd install, health wait, `--check` flag
- `scripts/teardown.sh` — Created: graceful stop/disable, unit file removal, data preservation
- `scripts/redeploy.sh` — Created (prior step): rebuild-if-running script with `--force` flag
**Result**: All five scripts created and executable; `make setup`, `make setup-check`, `make teardown`, `make docker-restart` all resolve to existing scripts

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 5/5 tasks addressed
- Shortcuts found: none
- Scope drift: seed_jobs.py quote-style normalization (ruff, harmless)
- Stragglers: none — docs/self-modification.md already referenced correct paths

## Lessons Learned

### What worked well
- **Prerequisite-accumulator pattern.** `PREREQ_OK=true` at the top, each failing check sets it to `false`, single `die` at the end. Users see every missing prereq in one pass instead of fix-one-rerun-discover-another.
- **Single script with `--force` over a wrapper script.** `redeploy.sh --force` covers both "already running, just rebuild" and "cold first deploy" without a second script. The `--force` flag makes the unsafe-in-general case deliberately explicit.
- **OS detection via `uname -s` as the first guard.** Both `setup.sh` and `teardown.sh` exit 0 with a friendly alternatives message on non-Linux. No cryptic `systemctl: command not found` errors on macOS.

### What to do differently
- **Closed issues can lie.** ISSUE-027 was marked all-[✓] but `deploy/`, `scripts/setup.sh`, and `scripts/teardown.sh` never existed. The Makefile silently pointed at nothing for weeks. At close time: check that every file the Makefile (or any caller) references actually exists on disk — not just that it was listed in the implementation plan.
- **`scripts/` was in `.gitignore` the whole time** (also caught in ISSUE-caf8de). The gitignore line was wrong from day one; removing it was the right fix. When `git add` fails with "ignored", run `git check-ignore -v <path>` immediately rather than using `-f`.

### Patterns to reuse
- **`@@PLACEHOLDER@@` template pattern for text-based config files.** `sed -e "s|@@VAR@@|$value|g"` is sufficient for systemd unit templates where variables are plain paths. No templating engine needed; the double-at delimiter is unlikely to collide with real content.
- **Prerequisite checks that summarise, not bail.** Accumulate all failures before calling `die`. Apply this to any setup script that has more than one independent prereq — the "fix everything, then rerun" UX is strictly better than "fix one thing, rerun, discover the next."
