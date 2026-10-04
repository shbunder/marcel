# Rule — the one legal restart path

Marcel redeploys itself only through the flag file:

1. A self-change is a PR on `shbunder/marcel` that the owner approves in the app.
2. On merge, the hub calls `request_redeploy(sha)`, which writes `~/marcel/state/redeploy.flag`.
3. The host's `marcel-redeploy.path` unit runs `deploy/redeploy.sh`.
4. The health watchdog checks `/health` and runs `git revert` on the merge if it fails.

## Never
- `docker restart`, `docker compose restart`, `systemctl restart` or `os.execv` from Marcel's code
- Calling `deploy/redeploy.sh` directly from code: that skips the health check and the rollback
- Merging a self-change without an approval record

## Why
Marcel runs on a home server. A restart that skips the rollback can leave it unreachable until
someone SSHes in.
