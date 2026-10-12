---
name: story
description: Claim a story on the build board and start it — picks the next ready story for this machine (or the one named), marks it In Progress, creates its worktree and branch, and loads it. Use at the start of any implementation work.
---

Start: $ARGUMENTS   (a story id, or empty for "the next ready one")

1. **Export the board** (see `.claude/rules/board.md`). Keep the result text: it lists each row's version.
2. **Pick.** Machine: macOS → `Mac`; the NUC (Linux with `~/marcel/`) → `NUC`; anything else → `cloud`.
   - Named: `python3 scripts/board.py start <id>`. Refused → tell the owner why, in plain words, and stop.
   - Empty: first line of `python3 scripts/board.py ready --runs-on <machine>`, then `start` it.
     Nothing ready → say what is in flight and stop.
   - Never start a `Mac` or `NUC` story on another machine.
3. **Claim** with ArtifactData `update` on `stories/<id>`, `if_version` from step 1:
   `{status: "In Progress", owner: "<hostname -s>", branch: "story/<id>", updated_at: "<now, ISO UTC>"}`.
   Pin failed → someone else took it; go back to 1. Then `set` a `log` row (`kind: "status"`,
   `text: "Backlog → In Progress on <host>"`).
4. **Branch:** `git fetch origin && git worktree add ../marcel-<id> -b story/<id> origin/main`
   (cloud: `git switch -c story/<id> origin/main`). Work there from now on.
5. **Load:** `project/stories/<id>.md`, its "read first" files, and `project/lessons/` for its lane. Follow
   the implementer loop in `plan/04-agent-playbook.md`: failing tests from "done when" first.

Tell the owner in one line: which story, why it was the pick, the branch.
