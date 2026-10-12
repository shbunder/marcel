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
