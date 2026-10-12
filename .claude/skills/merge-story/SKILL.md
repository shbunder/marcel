---
name: merge-story
description: Lead only — merge a reviewed story PR into main in wave order and mark it done on the build board. Use when a story is in review, its checks are green and the owner (or lead) has approved it.
---

Merge: $ARGUMENTS   (a story id or a PR number)

1. **Check it is mergeable.** `gh pr view <pr> --json state,mergeable,statusCheckRollup,headRefName`. Checks green,
   no conflicts, branch `story/<id>`. Export the board (as in `/story` step 1) and confirm every story in its
   `depends:` is `done`.
2. **Merge** with a merge commit so the story stays one unit in history:
   `gh pr merge <pr> --merge --delete-branch` (never squash, never rebase).
3. **Board.** `get` `stories/<id>`, then `update` with `if_version`:
   `{status: "done", updated_at: "<now>"}`; add `log` `{kind: "merge", text: "merged <PR url> → main"}`.
4. **Clean up** the worktree if it is on this machine: `git worktree remove ../marcel-<id>`.
5. **What opened up.** Export the board again and run `board.py ready` for each machine.
   Tell the owner in plain words what got merged and which stories are now ready, per machine.

Never merge your own story without the owner's or a verifier's sign-off. Never merge out of wave order when the
later story touches files an earlier, unmerged one also touches.
