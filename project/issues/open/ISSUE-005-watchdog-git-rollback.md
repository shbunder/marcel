# ISSUE-005: Watchdog + git rollback

**Status:** Open
**Created:** 2026-03-26
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, phase-1

## Capture
**Original request:** Marcel should be able to restart itself after self-modification. If the restart fails, there should be a mechanism to roll back the git commit and spin up from the last good commit.

**Resolved intent:** Build a watchdog process that manages `uvicorn` as a subprocess, monitors the `/health` endpoint after restarts, and automatically reverts the last git commit + restarts if the health check fails. The watchdog is explicitly off-limits for Marcel's self-modification (it must never rewrite the safety net that saves it).

## Description

### Process hierarchy on the NUC

```
systemd
  └── marcel-watchdog   (this issue)
        └── uvicorn (marcel_core.main:app)
```

`make serve` in development starts the watchdog directly (not via systemd).

### Watchdog behaviour

**Normal startup:**
1. Start uvicorn subprocess on port 8000
2. Poll `GET /health` every 2s for up to 30s
3. If 200 OK: log "Marcel is up" and enter monitor loop
4. If timeout: log fatal error and exit (do NOT rollback on first boot — no prior commit to roll back to)

**After a self-modification restart** (triggered by the agent calling an internal `restart_for_update()` function):
1. Agent has already committed changes via git
2. Agent calls `restart_for_update(commit_sha_before_change)` — watchdog receives this via a local Unix socket or a flag file
3. Watchdog sends SIGTERM to uvicorn, waits up to 10s for graceful shutdown
4. Starts new uvicorn process
5. Polls `/health` every 2s for up to 30s
6. **If health check passes:** notify agent "Restart successful" (write to flag file)
7. **If health check fails:**
   - `git revert HEAD --no-edit`
   - `git commit -m "revert: auto-rollback after failed restart"`
   - Start uvicorn again (from reverted code)
   - Poll health again (30s)
   - Write "Rollback complete" to flag file for the agent to read on next startup

### Communication between agent and watchdog

Use a flag file at `data/watchdog/`:
- `data/watchdog/restart_requested` — written by agent to trigger restart (contains pre-change commit SHA)
- `data/watchdog/restart_result` — written by watchdog after restart: `"ok"` or `"rolled_back"`

The watchdog polls for `restart_requested` every second when in monitor mode. This avoids IPC complexity.

### Module layout

```
src/marcel_core/watchdog/
  __init__.py
  main.py       # entrypoint: starts uvicorn, enters monitor loop
  health.py     # polls /health endpoint
  rollback.py   # git revert + recommit
  flags.py      # reads/writes flag files in data/watchdog/
```

The watchdog has NO imports from the rest of `marcel_core` — it is intentionally isolated so it cannot be accidentally broken by agent self-modification.

### `make serve` (development)

```makefile
serve:
	python -m marcel_core.watchdog.main
```

### Systemd unit (for NUC production, documented in ops/)

```ini
[Unit]
Description=Marcel Watchdog
After=network.target

[Service]
WorkingDirectory=/path/to/marcel
ExecStart=/path/to/.venv/bin/python -m marcel_core.watchdog.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Tasks
- [ ] `watchdog/flags.py`: read/write flag files in `data/watchdog/`
- [ ] `watchdog/health.py`: `poll_health(timeout_s, interval_s) -> bool`
- [ ] `watchdog/rollback.py`: `do_rollback() -> None` — git revert + commit
- [ ] `watchdog/main.py`: start uvicorn subprocess, monitor loop, restart flow
- [ ] `agent/runner.py`: expose `request_restart(pre_change_sha: str)` that writes the flag file and polls for result
- [ ] Update `Makefile`: `make serve` starts watchdog
- [ ] Tests: health poll succeeds/times out; rollback runs correct git commands (mock subprocess)
- [ ] Docs: `docs/self-modification.md` — watchdog flow, flag files, systemd setup

## Relationships
- Depends on: [[ISSUE-001-marcel-core-server-scaffold]]

## Implementation Log
