# Contract request: the runner says when Claude Code changes under Marcel

Against `contracts/runner-api.yaml` (`RunnerEvent`). Found live on the NUC: another `claude` process updated the
shared binary, Claude's supervisor restarted onto it (`cause=upgrade, live_workers=5` in its daemon log), five
running sessions ended, and the runner's pin (S-03.10) refused every start until the pinned version was back.

The hub now notices it by reading `GET /health` every minute (`hub/src/marcel_hub/claude_updates.py`), and infers
which sessions the restart ended from the sessions the runner no longer lists. That is a guess with a minute's
delay. The runner sees both facts first-hand.

## Proposed

Two new `RunnerEvent.type`s, each with `message` in plain words:

- `runner.claude_changed` `{from, to, expected}`: the installed `claude --version` changed (the runner checks it
  on each poll or each health call). `expected` is the pin, so the hub can tell "updated away" from "back".
- `runner.supervisor_restarted` `{cause, live_workers, session_ids}`: Claude's supervisor started again (its pid
  changed between two polls); `cause` and `live_workers` from the supervisor's own log line when readable
  (`~/.claude/daemon.log`: `shutting down (cause=upgrade, …, live_workers=5)`; project/lessons/runner-built-ins.md),
  `session_ids` the sessions that were live before and are gone after.

The hub then marks exactly those sessions' tasks and words the notice from `cause`, and drops the guess.
