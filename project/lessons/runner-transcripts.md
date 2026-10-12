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
  0; the hub's stored offset is then past the new ones.
