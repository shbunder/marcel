# Runner cloud sessions

- **`claude --cloud -- "<prompt>"` and the `subprocess.Popen` pty are only proven against the fake
  claude.** SP3 ran `claude --cloud "<prompt>"` (no `--`) under `pty.fork`. The fake accepts both
  forms, real Claude Code may not. Before F07 relies on cloud tasks, run one real create
  (`POST /cloud/sessions`) and one real send on the NUC. If the create fails, compare:
  - the error's `detail` with `runner/tests/fake_claude/fixtures/cloud/create-pty-output.txt`
    (the `Created cloud session: … View: … Resume with: …` line);
  - by hand, `claude --cloud "x"` against `claude --cloud -- "x"` on a terminal: does `--` make
    claude read `--cloud` as "no id" and the prompt as the prompt?
  - with a session that hangs: whether claude needs a window size on the pty (Popen gives none;
    `pty.fork` inherits the caller's).
  If the send fails, compare its output with `fixtures/cloud/followup-p-json.out`.
- **End of output is not end of process, and not the other way round.** EOF on the pipe can come a
  moment before `claude` exits, and a helper `claude` leaves behind can hold the pty open after it
  exits. `cloud.py` waits for the exit after EOF, stops reading once claude has exited and gone
  quiet, and always kills the whole process group.
- **A `repo` cloud create starting on the right branch is still unproven (S-03.9).** No real
  `claude --cloud` has been run from the `.cloud` worktree; the tests use a fake that only records
  the folder, its branch, its commit and `@{u}`. The runner gives claude a worktree of the clone on a
  local branch named like `base`, tracking `origin/<base>`, with `origin` set to the https URL. On
  the NUC, once:
  1. `POST /cloud/sessions` with `repo: {url: "https://github.com/shbunder/marcel", base: "<a branch
     other than main>"}` and a prompt that asks the session to print `git branch --show-current` and
     `git log -1 --format=%H`.
  2. At the session's claude.ai page: is the repo `shbunder/marcel`, and do the branch and commit
     match `origin/<base>`? Note the result here.
  3. If claude.ai started on the default branch instead, check by hand in a worktree whether
     `claude --cloud` reads the branch from `HEAD`, from the upstream, or needs the branch pushed.
  4. Whether the trust dialog appears in the worktree (SP2 says worktrees inherit trust from the
     clone). If it does, the create hangs to its 60 s limit and answers `claude_timeout`; the
     pty output in `detail` would show the dialog.
  5. Do the repo's own `.claude/settings.json` hooks run on the NUC when `claude --cloud` starts in
     the `.cloud` checkout? (The runner disables git hooks, not Claude Code hooks.) If they do, a
     repo can run code on the NUC through a cloud create; note it here and decide before F07.
- **A cloud create holds the repo's lock, and that can outlast the hub's 120 s `repo` spawn
  timeout.** A task spawn of the same repo waits while a create runs: usually a fetch and up to
  60 s of `claude --cloud`, but on a repo's first use a clone, the trust dialog (40 s) and the plugin
  install come first, and each git step may take up to 600 s. The hub then gives up on a spawn the
  runner still finishes. S-06.7 / S-07.1: allow for it (a longer timeout for `repo` spawns, or treat
  a timed-out spawn as possibly started and look for it in the roster).
