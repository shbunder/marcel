# Rule — self-modification restart path

Marcel can rewrite its own code. The restart that deploys that code is the safety-critical boundary. Only **one** mechanism is allowed.

## The one legal restart

```python
from marcel_core.watchdog.flags import request_restart
import subprocess
sha = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
request_restart(sha)
```

This writes the `restart_requested.{env}` flag file (where `{env}` is `dev` or `prod`, resolved from `MARCEL_ENV`). A host-side systemd path unit watches the matching flag — `marcel-redeploy.path` for prod, `marcel-dev-redeploy.path` for dev — and triggers `redeploy.sh --env {env}`, which clears the flag, rebuilds the Docker image, and recreates the container. In prod, a second layer — the in-container watchdog (PID 1) — then polls `/health` and **rolls back via `git revert HEAD` on failure**. Dev has no in-container watchdog by design (uvicorn is PID 1 for `--reload`), so dev self-mod has no automatic rollback. See [docs/self-modification.md](../../docs/self-modification.md) for the full mechanism.

Dev and prod share one code path and one mechanism — only the flag suffix and compose file differ. There is no dev-mode exception.

## Never

- `sudo systemctl restart marcel`
- `docker restart marcel`
- `os.execv(...)` from inside the container — there are no exceptions
- `subprocess.run(['/path/to/redeploy.sh', ...])` — bypasses the flag and the rollback
- Any new code path that invokes a restart without going through `request_restart()`

## Why

The flag-file mechanism gives automatic rollback on health-check failure. Direct restarts skip the rollback and can leave Marcel unreachable — which, for a family assistant running on a home server, means "the kids can't ask about dinner tonight" until the zoo keeper SSHes in.

## Enforcement

- [.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md) flags any diff that adds a bypass path.
- [.claude/agents/security-auditor.md](../agents/security-auditor.md) treats bypass as **Critical** — the flag file's contents are a pre-change git SHA written by `request_restart()` (currently used only for logging by the watchdog, but it sits on the restart boundary). Any future code path that treats that SHA as an execution parameter (e.g. `git checkout $SHA`) makes user-controllable input reaching `request_restart()` a remote-code-execution vector on the host. Gate every call site tightly.
