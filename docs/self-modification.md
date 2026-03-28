# Self-Modification Safety

Marcel can rewrite its own code.  The watchdog is the safety net that makes
this safe: it manages the `uvicorn` subprocess, detects startup failures, and
automatically rolls back a bad commit before the agent even wakes up on the
next request.

---

## Process hierarchy

```
systemd (production) / make serve (development)
  └── marcel-watchdog   (marcel_core.watchdog.main)
        └── uvicorn     (marcel_core.main:app)
```

The watchdog is a plain Python process.  It has **no imports from the rest of
`marcel_core`** — it is intentionally isolated so it cannot be broken by agent
self-modification.

---

## Watchdog flow

### 1. Normal startup

1. Start a `uvicorn` subprocess on `$MARCEL_PORT` (default `8000`).
2. Poll `GET http://localhost:{port}/health` every `$MARCEL_POLL_INTERVAL`
   seconds (default `2`) for up to `$MARCEL_HEALTH_TIMEOUT` seconds
   (default `30`).
3. If `200 OK` is received: log *"Marcel is up"* and enter the monitor loop.
4. If the deadline expires without a `200`: log a fatal error and exit.
   **No rollback is attempted on first boot** — there is no prior good commit
   to roll back to.

### 2. Monitor loop

The watchdog sleeps for `POLL_INTERVAL` seconds on each iteration and checks
two things:

- **Restart request flag** — has the agent asked for a restart?
- **Unexpected exit** — did `uvicorn` die on its own?

### 3. Restart on request (self-modification path)

When Marcel modifies its own code it:

1. Commits all changes via git.
2. Writes the **pre-change SHA** to the `restart_requested` flag file.

The watchdog detects the flag on the next poll and:

1. Clears `restart_requested`.
2. Sends `SIGTERM` to `uvicorn`; waits up to 10 s for a clean exit
   (falls back to `SIGKILL`).
3. Starts a new `uvicorn` subprocess.
4. Polls `/health` for up to `HEALTH_TIMEOUT` seconds.

**If health check passes** → writes `"ok"` to `restart_result`.

**If health check fails**:

1. Stops the unhealthy process.
2. Runs `git revert HEAD --no-edit` in the repo root — this creates a new
   revert commit (no separate `git commit` is needed).
3. Starts `uvicorn` again from the reverted code.
4. Polls `/health` again.
5. Writes `"rolled_back"` to `restart_result` on success, or
   `"rollback_failed"` and exits with code `1` if the rolled-back version
   also fails to start.

### 4. Unexpected exit recovery

If `uvicorn` exits without a restart request, the watchdog immediately starts a
fresh process and polls health.  If the restart fails, the watchdog logs an
error and exits — at which point systemd will restart the watchdog itself
(`Restart=always`).

---

## Flag files

Flag files live at `data/watchdog/` relative to the repository root.

| File | Writer | Reader | Contents |
|------|--------|--------|----------|
| `restart_requested` | agent | watchdog | pre-change git SHA (plain text) |
| `restart_result` | watchdog | agent | `"ok"`, `"rolled_back"`, or `"rollback_failed"` |

All writes use an **atomic write-to-temp-then-rename** pattern so neither side
ever reads a partially-written file.

The agent API for triggering a restart lives in
`src/marcel_core/agent/runner.py` → `request_restart(pre_change_sha)`.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MARCEL_PORT` | `8000` | Port passed to `uvicorn` |
| `MARCEL_HEALTH_TIMEOUT` | `30` | Seconds to wait for `/health` |
| `MARCEL_POLL_INTERVAL` | `2` | Seconds between health polls |

---

## Development usage

```bash
make serve          # starts the watchdog (which starts uvicorn)
```

The `Makefile` target runs:

```
python -m marcel_core.watchdog.main
```

---

## Production — systemd unit (NUC)

```ini
[Unit]
Description=Marcel Watchdog
After=network.target

[Service]
WorkingDirectory=/path/to/marcel
ExecStart=/path/to/.venv/bin/python -m marcel_core.watchdog.main
Restart=always
RestartSec=5
Environment=MARCEL_PORT=8000

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable marcel-watchdog
sudo systemctl start  marcel-watchdog
sudo journalctl -u marcel-watchdog -f   # follow logs
```

---

## Off-limits for self-modification

`src/marcel_core/watchdog/main.py` **must never be modified by Marcel's
self-modification system**.  It is the process that decides whether a
self-modification was safe, and modifying it mid-flight would remove the safety
guarantee.  If a change to the watchdog itself is required, it must be done by
a human developer with a manual restart.

The restriction is documented in CLAUDE.md under *Self-Modification Safety*.
