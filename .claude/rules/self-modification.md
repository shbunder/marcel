# Rule — self-modification goes through a PR and the redeploy flag

Marcel v3 can change its own code (F11). The path is fixed:

1. The change is an ordinary task that ends with a **PR** against `shbunder/marcel`.
2. A human (the owner) approves and merges it.
3. The hub's `request_redeploy` writes `~/marcel/state/redeploy.flag` (the merged SHA).
4. The host's `marcel-redeploy.path` unit runs the redeploy script, which pulls, rebuilds only what changed,
   checks `/health`, and **rolls back** to the previous SHA if the check fails.

That is the only legal restart path. See `plan/features/F07-F11-platform.md` § F11.

## Never

- `docker restart …`, `docker compose restart …`, `systemctl restart …` from Marcel's code or a worker session.
- `os.execv(...)`, killing the runner, or calling the redeploy script directly.
- Any code path that treats the flag's SHA as something to execute (`git checkout $SHA` from user input).
- A self-change merged without a PR, or a PR merged by Marcel itself.

## Why

The flag file is the only path with an automatic rollback. A direct restart that fails leaves Marcel down on a home
server until the owner gets to a laptop — "the kids can't ask about dinner tonight".

## Enforcement

`security-auditor` treats any bypass as **Critical**. The F11 stories carry a test that deletes the rollback and
expects a red build (see [inert-controls](./inert-controls.md)).
