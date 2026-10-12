---
name: story
description: Claim a story from the build board and start it — picks the next ready story for this machine (or the one named), marks it doing, creates its worktree and branch, and loads the story. Use at the start of any implementation work on Marcel v3.
---

Start work on: $ARGUMENTS   (a story id like `S-04.9`, or empty for "the next ready one")

Board URL and field meanings: [.claude/rules/board.md](../../rules/board.md). Load `ArtifactData` with ToolSearch
`select:ArtifactData` if it is not loaded.

## 1. Export the board

`ArtifactData` `list`, collection `stories`, `query: {limit: 1000}`, `out_dir: .board/export`.
Keep the result text: it shows each row's **version**.

## 2. Pick the story

Which machine is this? macOS → `Mac`; the NUC (Linux with `~/marcel/state/`) → `NUC`; anything else → `cloud`.

```bash
python3 .claude/board/board.py ready --board .board/export --runs-on <machine>
python3 .claude/board/board.py check --board .board/export <S-id>   # when one was named
```

- Named story not ready → say why (the script's reasons, in plain words) and stop.
- No argument → take the first line of `ready`. Nothing ready → say what is in flight and stop.
- Never start a `Mac` or `NUC` story on another machine.

## 3. Claim it (pinned, so two sessions can't both win)

`ArtifactData` `update` on `stories/<id>` with `if_version` = the version from step 1:
`{status: "doing", owner: "<hostname -s>", branch: "story/<id>", updated_at: "<now, ISO UTC>"}`.
If the pin fails, someone else claimed it: go back to step 1.

Then `set` `log/<YYYYMMDDTHHMM>-<id>-claim`: `{at, story: id, kind: "status", text: "todo → doing on <host>", by: "claude"}`.

## 4. Branch

```bash
git fetch origin
git worktree add ../marcel-<id> -b story/<id> origin/main     # cloud sessions: git switch -c story/<id> origin/main
```

Work inside that worktree from here on.

## 5. Load the story

Read the story's section in its `source` file (`plan/features/…`), every file in its `read first:` line, and any
`project/lessons/*.md` whose name matches its lane. Then follow the implementer loop in
[plan/04-agent-playbook.md](../../../plan/04-agent-playbook.md): failing tests from "done when" first, then code,
staying inside `touches:`.

Tell the owner in one line: which story, why it was the pick, and the branch.
