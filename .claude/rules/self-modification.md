# Rule — self-modification restart path

Marcel can rewrite its own code. The restart that deploys that code is the safety-critical boundary. Only **one** mechanism is allowed.

## The one legal restart

```python
from marcel_core.watchdog.flags import request_restart
import subprocess
sha = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
request_restart(sha)
```

This writes the `restart_requested` flag file. A host-side systemd path unit (`marcel-redeploy.path`) watches it and triggers `redeploy.sh`, which rebuilds the Docker image, restarts the container, health-checks, and **rolls back on failure**. See [docs/self-modification.md](../../docs/self-modification.md) for the full mechanism.

In dev mode (`make serve`), the restart watcher in `main.py` detects the same flag and exec-replaces the process in-place — same contract, different implementation.

## Never

- `sudo systemctl restart marcel`
- `docker restart marcel`
- `os.execv(...)` from inside the container (except the dev-mode watcher in `main.py`, which is the sole exception)
- `subprocess.run(['/path/to/redeploy.sh', ...])` — bypasses the flag and the rollback
- Any new code path that invokes a restart without going through `request_restart()`

## Why

The flag-file mechanism gives automatic rollback on health-check failure. Direct restarts skip the rollback and can leave Marcel unreachable — which, for a family assistant running on a home server, means "the kids can't ask about dinner tonight" until the zoo keeper SSHes in.

## Enforcement

- [.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md) flags any diff that adds a bypass path.
- [.claude/agents/security-auditor.md](../agents/security-auditor.md) treats bypass as **Critical** — the flag file's contents are a git SHA that `redeploy.sh` checks out, so any code path where user-controllable input reaches that file is remote code execution on the host.
