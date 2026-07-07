# Execution sandbox

Marcel runs agent shell commands (`bash` and [`code_exec`](co-work.md)) inside a
**bubblewrap workspace-write sandbox** (ADR-260628-0fc1e2) — the OS-level
containment that makes "Marcel writes and runs its own code over my data"
safe. It is the second half of F2, layered under the
[command policy](extensions.md#the-lifecycle-event-bus):

- the **policy** decides *which* commands run / ask / are denied;
- the **sandbox** contains *what a command that does run can touch*.

## What it confines

A sandboxed command gets:

- a broad **read-only** view of the whole filesystem;
- **writes confined** to the session workspace (the effective cwd);
- the **self-modification boundary** bind-mounted **read-only inside** the
  writable workspace — `.git`, the data dir (`~/.marcel`, which holds the
  `restart_requested.{env}` flag), and the tracked self-mod files
  (`CLAUDE.md`, `src/marcel_core/auth`, `config.py`, `.env*`). Sandboxed code
  cannot rewrite the code that governs it or inject a restart;
- **network** per `marcel_sandbox_network` (kept on for admin `bash`; the
  untrusted `code_exec` path runs with it off).

Implemented with [`sandbox.py`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/harness/sandbox.py)
via `bwrap` — no daemon, one process per run.

## The unprivileged-user-namespace requirement

Bubblewrap needs **unprivileged user namespaces**. Several environments
disable them even when `kernel.unprivileged_userns_clone` reads `1`:

- **Ubuntu 23.10+** restrict them via AppArmor
  (`kernel.apparmor_restrict_unprivileged_userns=1` by default).
- **Docker** blocks the `CLONE_NEWUSER` path unless the container is allowed
  to create user namespaces.

So the sandbox is **active only where userns is available**. Marcel probes at
runtime (`sandbox_available()`): if the sandbox cannot start, the command
runs **unsandboxed** and a warning is logged — the command policy still
applies, but there is no OS containment. **Until the sandbox is active, treat
`bash` as unconfined.**

### Enabling it in the deployment

`bubblewrap` ships in the image (the Dockerfile installs it). To make the
sandbox *active*, enable unprivileged userns for the container:

1. **Host (Ubuntu 23.10+):** allow unprivileged userns —
   ```bash
   sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
   # persist: echo 'kernel.apparmor_restrict_unprivileged_userns=0' | sudo tee /etc/sysctl.d/60-userns.conf
   ```
2. **Docker:** let the container create user namespaces — e.g. add to the
   prod compose service:
   ```yaml
   security_opt:
     - apparmor=unconfined   # or a profile that permits userns
   ```
   (A tighter alternative is a custom AppArmor/seccomp profile that allows
   only `clone`/`unshare` with `CLONE_NEWUSER`.)
3. **Alternative — setuid bwrap:** where unprivileged userns cannot be
   enabled, install `bwrap` setuid-root (`chmod u+s $(command -v bwrap)`); it
   then sets up the sandbox without userns. This widens `bwrap`'s own trust,
   so prefer options 1–2.

Confirm it took: the startup logs show no "sandbox could not start" warning,
and `sandbox_available()` returns `True`. The skipped confinement tests in
`tests/harness/test_sandbox.py` run once a working sandbox is present.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `MARCEL_SANDBOX_ENABLED` | `true` | Route `bash`/`code_exec` through the sandbox when available. `false` runs unsandboxed everywhere. |
| `MARCEL_SANDBOX_NETWORK` | `true` | Keep network in sandboxed `bash`. The [`code_exec`](co-work.md) path forces it off. |

## Status

Layer 1 of F2. The wrapper, fallback, and Dockerfile install are in place;
**activation is a deployment step** (enable userns as above) — verify it in
prod before relying on the containment.
