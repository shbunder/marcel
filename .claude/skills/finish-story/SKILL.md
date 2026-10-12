---
name: finish-story
description: Finish the story on the current story/S-… branch — lane gate, story-verifier, commit by name, push, PR, board card to Review. Use when the implementer believes every "done when" item is met.
---

Finish: $ARGUMENTS   (empty = the id in the branch name `story/<id>`)

1. **Gate** every lane the diff touches (`make -C <lane> check`, `npm run check`, `make ios-check`,
   `make scripts`). Red → fix first.
2. **Verify:** delegate to the `story-verifier` agent with the id and branch. Fix everything under
   "fix first", as more commits.
3. **Commit and push:** `git status`, `git add <each file by name>`,
   `git commit -m "[<id>] impl: <what changed>"`, `git show --stat HEAD`, `git push -u origin story/<id>`.
4. **PR:** `gh pr create --base main --title "[<id>] <title>"`. Body: what it does for the user (two or three
   plain lines); the verifier's table; any lesson or ADR added; last line `Board: <id> → Review`.
5. **Board:** `get` `stories/<id>`, then `update` with its `if_version`:
   `{status: "Review", pr: "<url>", updated_at: "<now>"}`; add a `log` row (`kind: "status"`).

Tell the owner: the PR link, whether the gate was green, what the verifier flagged.
