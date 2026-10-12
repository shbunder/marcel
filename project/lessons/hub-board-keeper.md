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
