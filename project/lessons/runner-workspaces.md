# Runner workspaces, resume and fork

- **Trust and `plugin install` were never run by a test against real Claude Code.** The fake shim
  fakes neither, so `runner/tests/test_workspaces.py` has its own small `claude` that shows the trust
  dialog and writes `.claude/settings.local.json`. On the NUC, check once that `claude plugin install
  marcel@marcel-local --scope local` writes only `.claude/settings.local.json` (the runner excludes
  that path in the clone's `.git/info/exclude` and fails if `git status` is not clean afterwards).
- **`~/.claude/daemon/roster.json` has no recorded format.** The resume wait looks for the session's
  full or short id anywhere in its text. If the real file holds ids of other sessions that contain
  the short id, the wait only runs to its 10 s limit; the copy check still catches a copy.
- **The fake gives a fork its source's name** (`backgrounded · <new> · <source name>`). The runner
  never reads the name from that line, but a test that finds a fork by name must use the source's.
