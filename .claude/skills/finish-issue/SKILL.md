---
description: Close a WIP issue — update task statuses from git diff, log implementation, commit and move to closed/
---

Finish work on issue: $ARGUMENTS

## Steps

### 1. Locate the issue

Search for the issue file matching `$ARGUMENTS` (accepts issue number, e.g. `42`, `ISSUE-042`, or the full filename).

- Look first in `project/issues/wip/`
- If found elsewhere (open, closed), stop and tell me — only WIP issues can be finished
- Read the issue file in full

### 2. Commit any uncommitted work on the current branch

Run `git status`. If there are staged or unstaged changes to source files (anything outside `project/issues/`), commit them now:
```
git add <relevant source files>
git commit -m "🔧 [ISSUE-{NNN}] impl: <brief description of what was done>"
```
Do not use `git add -A` — be selective about what you stage.

### 3. Determine what was actually done

Run `git diff main...HEAD -- . ':(exclude)project/issues/'` to see all code changes on this branch.

Read the changed files to understand what was implemented. Cross-reference with the task list in the issue.

### 4. Update task and subtask statuses

Go through every `- [ ]` and `- [⚒]` item in the issue. For each one:
- Mark `[✓]` if the corresponding work is present in the diff
- Mark `[⚒]` if it was started but is incomplete
- Leave `[ ]` if there is no evidence it was touched

If any subtask statuses changed, include those updates in the closing commit (step 6) — do not create separate commits for subtask checkbox changes.

### 5. Append an implementation log entry

Add a log entry at the bottom of the issue file under `## Implementation Log`:

```markdown
### {today's date} - LLM Implementation
**Action**: <summary of what was built>
**Files Modified**:
- `path/to/file.py` — what changed
**Result**: <outcome, e.g. "X tests passing">
```

### 6. Pre-close verification

Before closing, check for stragglers:
- Run `grep -r "<key term>" .claude/skills/ docs/ project/` for key terms from the changes (convention names, emoji, format strings) to find files that reference old patterns.
- Verify all tasks and subtasks in the issue show `[✓]`.
- If you find missed files, commit them as a final `🔧 [ISSUE-{NNN}] impl:` commit before closing.

### 7. Move to closed

- Update `**Status:** Closed`
- Move the file: `project/issues/wip/ISSUE-{NNN}-{slug}.md` → `project/issues/closed/ISSUE-{NNN}-{slug}.md`

Commit the move:
```
git add ./project/issues/
git commit -m "✅ [ISSUE-{NNN}] closed: <one-line summary of what was completed>"
```

### 8. Report back

Tell me:
- Which tasks were marked done vs incomplete
- Any tasks left open and why
- The final commit hash for the closure commit
