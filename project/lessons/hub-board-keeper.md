# Hub: the board keeper in tests

The hub rebuilds project boards in the background (`boards.BoardKeeper`, a loop on the app's event
loop, once a second). A rebuild publishes `artifact.created` / `artifact.updated` into the app event
log, so any test that counts app events would become flaky the moment a board happened to be due.

- `hub/tests/conftest.py` sets `boards.TICK_S` to an hour for every test, so the loop never fires on
  its own. Board tests call `keeper.flush()` (or `keeper.build(project_id)`) themselves.
- Give the keeper a fake clock that **starts at `time.monotonic()`** (see `tests/test_boards.py`
  `Clock`). The app marks every board at start with the real clock; a fake clock starting at 0 or
  1000 would leave those marks due far in the future.
- An agent created after the app started gets its inbox and board row in the same write, but no
  board content until the first change in the inbox (or the first read of the board, which builds
  it on the spot). Tests that read a board straight away should `build` it first.
- Every artifact now has an `artifact_version` row and every project a `board` artifact. A test
  that counts all artifacts must leave boards out (`Artifact.kind != ArtifactKind.board`).

## The avatar state in tests (S-04.8)

`agent_state.AgentStates` follows the same event log and sends `agent.state` when an agent's state
changes. Two things that used to make one event now make two:

- a message the owner writes to the brain (main conversation or side thread) also sends
  `agent.state` `thinking`;
- a task moving into or out of `working` / `needs_you` / `done` also sends `agent.state`.

Tests that count the events of a message take the avatar off the log
(`app.state.events.observers.remove(app.state.agent_states.observe)`, see `tests/test_ws.py`
`quiet_app`); tests that count task events filter `agent.state` out. `conftest.py` holds its
once-a-second tick back, like the board keeper's; drive it with a fake clock and `tick()`.
