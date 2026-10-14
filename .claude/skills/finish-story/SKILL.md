---
name: finish-story
description: Finish the story on the current story/S-… branch — lane gate, story-verifier, commit by name, then land it on main (or open a PR when the owner must review), board card to Done or Review. Use when the implementer believes every "done when" item is met.
---

Finish: $ARGUMENTS   (empty = the id in the branch name `story/<id>`)

1. **Gate** every lane the diff touches (`make -C <lane> check`, `npm run check`, `make ios-check`,
   `make scripts`). Red → fix first.
2. **Verify:** delegate to the `story-verifier` agent with the id and branch. Fix everything under
   "fix first", as more commits.
   If the diff touches auth, pairing, the runner, the plugin's tools, `deploy/` or secrets, also run
   the `security-auditor` agent on the branch and fix what it marks Critical or High.
3. **Commit:** `git status`, `git add <each file by name>`, `git commit -m "[<id>] impl: <what changed>"`,
   `git show --stat HEAD`.
4. **Land or PR** (ADR-012). Open a PR only when the change touches the self-change path (`deploy/`,
   `hub/src/marcel_hub/selfmod.py`, `hub/src/marcel_hub/watchdog/`, restart / redeploy / rollback), rests on a
   trade-off the owner has not decided, or changes a look or wording the story asks the owner to sign off.
   - **Land** (the default), with a verdict of "merge":
     ```bash
     git fetch origin && git switch --detach origin/main
     git merge --no-ff story/<id> -m "Merge story/<id>: <title>"
     make -C <each lane the story touches> check     # main may have moved since you branched
     git push origin HEAD:main                       # rejected → start this step again
     gh run watch "$(gh run list --branch main --limit 1 --json databaseId -q '.[0].databaseId')" --exit-status
     ```
     A red `check` on `main` is yours: fix it before anything else.
   - **PR:** `git push -u origin story/<id>`, `gh pr create --base main --title "[<id>] <title>"`. Body: what
     it does for the user (two or three plain lines), why the owner must look, the verifier's table, any
     lesson or ADR; last line `Board: <id> → Review`. The owner (or the lead, on their word) runs `/merge-story`.
5. **Board:** `get` `stories/<id>`, then `update` with its `if_version`: landed →
   `{status: "Done", updated_at: "<now>"}` and a `log` row `{kind: "merge", text: "landed <sha> on main"}`;
   PR → `{status: "Review", pr: "<url>", updated_at: "<now>"}` and a `log` row (`kind: "status"`).
6. **Clean up** a landed story: leave the worktree (`cd` to the main checkout) and
   `git worktree remove ../marcel-<id> && git branch -d story/<id>`.

Tell the owner in plain words: landed (with the CI result) or the PR link and why it needs them, and what
the verifier flagged.
