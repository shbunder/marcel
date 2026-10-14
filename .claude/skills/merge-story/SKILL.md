---
name: merge-story
description: Merge a story PR the owner had to review (ADR-012) into main and mark it Done on the build board, then report what became ready. Use when the owner approved a story PR in Review with green checks and a "merge" verdict. Stories that need no owner review land through /finish-story instead.
disable-model-invocation: true
argument-hint: <story id or PR number>
---

Merge: $ARGUMENTS   (a story id or PR number)

1. `gh pr view <pr> --json state,mergeable,statusCheckRollup,headRefName,body`: checks green, no conflicts,
   branch `story/<id>`, the verifier's verdict is "merge". Export the board; every `depends:` is Done.
2. `gh pr merge <pr> --merge --delete-branch` (a merge commit, never squash or rebase).
3. Board: `get` `stories/<id>`, `update` with `if_version`: `{status: "Done", updated_at: "<now>"}`;
   `log` row `{kind: "merge", text: "merged <PR url>"}`. No board commit in git.
4. Remove the worktree if it is on this machine: `git worktree remove ../marcel-<id>`.
5. Export again; `python3 scripts/board.py ready --runs-on <machine>` for cloud, NUC and Mac. Tell the owner
   what was merged and what is now ready, per machine.

Never merge a story whose `touches:` overlap an earlier, unmerged story in the same wave.
