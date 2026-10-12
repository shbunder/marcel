---
name: finish-story
description: Finish the story on the current story/S-… branch — runs the lane gate, has a fresh verifier check it, commits by name, pushes, opens the PR and moves the board card to review. Use when an implementer believes the story's "done when" list is met.
---

Finish: $ARGUMENTS   (empty = the story in the current branch name `story/<id>`)

## 1. Gate

Run the gate for every lane the diff touches (see the playbook): `make -C hub check`, `make -C runner check`,
`npm run check` in `plugins/marcel/channel`, `make ios-check`, `python3 -m unittest discover .claude/board`.
Red → fix it first. Do not continue on red.

## 2. Verify with fresh eyes

Delegate to the `pre-close-verifier` agent with the story id and `git diff origin/main...HEAD`. Fix every
**BLOCK** finding (as more commits). Mention **WARN** findings in the PR body.

## 3. Commit and push

```bash
git status
git add <each file by name>            # never git add . / -A / -u / commit -a
git commit -m "[<id>] impl: <what changed, in plain words>"
git push -u origin story/<id>
```

## 4. Pull request

`gh pr create --base main --title "[<id>] <story title>"` with a body that has:
- what it does for the user, in two or three plain lines;
- the "done when" list, each item ticked with the test or command that proves it;
- WARN findings, any lesson added, any ADR added;
- last line: `Board: <id> → review` (the fallback when a session cannot reach the board).

## 5. Board

Read `stories/<id>` (`get`), then `update` with its `if_version`:
`{status: "review", pr: "<PR url>", updated_at: "<now>"}`, and add a `log` row
`{kind: "status", text: "doing → review · <PR url>"}`.

If the board is out of reach (no `ArtifactData`), say so — the PR's last line carries the change.

Tell the owner: the PR link, whether the gate was green, and anything the verifier flagged.
