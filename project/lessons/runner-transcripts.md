# Runner transcripts

- **The project folder name is only half recorded.** SP2 shows `/` → `-` (e.g.
  `-home-shbunder-projects-marcel-src-scratch-spikes-SP2-worker-control-sandbox-repo`). That every
  other non-alphanumeric character (`_`, `.`) also becomes `-` is an assumption shared by
  `sessions.project_dir` and the fake claude, so a test with the fake cannot catch it being wrong.
  Marcel's worktrees have `_` in them (`tsk_01J9ZQ6`). The tail therefore falls back to finding
  `<session-id>.jsonl` in any project folder (`transcript.transcript_path`). Check one real worker
  folder on the NUC and note the result here.
- **`ai-title` lines are not in SP2's table.** They show up as `raw` events (the copy-resume
  recording has one). Map or drop them once someone decides what the app should do with them.
- **Offsets after a rewrite go backwards.** A truncated or replaced transcript is streamed again from
  0; the hub's stored offset is then past the new ones. Since runner-api 0.1.5 the hub sees the
  first offset at or below where it resumed (or any offset below the one before) as "the file
  started over", resets its position and reads it again. Transcript events are stored without
  their offset, so it skips repeats by type, time and data, matched in order and one for one
  against the events stored before the restart (the re-read state is saved in
  `runner-offsets.json`). It cannot see a file replaced by a
  *longer* one while no tail was open (the resume offset is then inside the new file).
- **A tail found by the fallback search never moves to the expected folder.** Decided: leave it.
  A session id names exactly one transcript; Claude Code writes it in the folder for the cwd it was
  started in. The fallback only finds a file when the runner's guess of that folder name is wrong,
  and then the guessed folder never gets the file. Both existing would mean the same session
  written in two folders (perhaps a `--resume` from another cwd — not seen in SP2). Switching would
  then restart from offset 0 in a different file and mix two histories; staying on the file that
  has the history is the safer choice. If the NUC check above ever shows both files, revisit.
