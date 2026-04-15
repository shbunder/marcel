---
name: finish-issue
description: Close a WIP issue — update task statuses from the diff, log implementation, verify no shortcuts, commit the close, merge the feature branch back to main. Use when implementation work on an issue branch is complete. Do NOT use to abandon an issue mid-work.
---

Finish work on issue: $ARGUMENTS

Full conventions live in [project/issues/CLAUDE.md](../../../project/issues/CLAUDE.md) and [project/issues/GIT_CONVENTIONS.md](../../../project/issues/GIT_CONVENTIONS.md). This skill is the procedural wrapper — do NOT duplicate workflow content here, reference those files.

## Steps

### 1. Locate the issue

Search for the issue file matching `$ARGUMENTS` (accepts short hash like `a1b2c3` or `ISSUE-a1b2c3`, or the full filename).

- You should be on the feature branch `issue/{hash}-{slug}` — if not, `git checkout` it first
- The file should be in `project/issues/wip/` on this branch
- Read the issue file in full

If the file is in `open/` (no work started) or `closed/` (already closed), stop and tell the user.

### 2. Commit any uncommitted source-code work

Run `git status`. If there are staged or unstaged changes to source files (anything outside `project/issues/`), commit them now:

```bash
git add <relevant source files>
git commit -m "🔧 [ISSUE-{hash}] impl: <brief description of what was done>"
```

Per [.claude/rules/git-staging.md](../../rules/git-staging.md), stage by name — never `git add -A`.

### 3. Determine what was actually done

```bash
git diff main...HEAD -- . ':(exclude)project/issues/'
```

Read the changed files to understand what was implemented. Cross-reference with the task list in the issue.

### 4. Update task and subtask statuses

Go through every `- [ ]` and `- [⚒]` item in the issue. For each one:
- Mark `[✓]` if the corresponding work is present in the diff
- Mark `[⚒]` if it was started but is incomplete
- Leave `[ ]` if there is no evidence it was touched

If any subtask statuses changed, include those updates in the closing commit (step 8) — do not create separate commits for subtask checkbox changes.

### 5. Append an implementation log entry

Add a log entry at the bottom of the issue file under `## Implementation Log` using the format in [project/issues/TEMPLATE.md](../../../project/issues/TEMPLATE.md).

### 6. Delegate verification to the pre-close-verifier subagent

The main conversation is biased toward the code it just wrote. Use a fresh context for verification:

```
Agent(
  subagent_type="pre-close-verifier",
  description="Pre-close verification ISSUE-{hash}",
  prompt="Verify issue branch issue/{hash}-{slug} before close. "
         "Issue file: project/issues/wip/ISSUE-{YYMMDD}-{hash}-{slug}.md. "
         "Focus areas: <optional hint, e.g. 'pay special attention to the hook script'>. "
         "Return the structured verdict."
)
```

The subagent reads the issue file, runs `git diff main...HEAD -- . ':(exclude)project/issues/'`, hunts for shortcuts/scope drift/stragglers, and returns a verdict with line references.

**If the verdict is REQUEST CHANGES:** fix every Critical and Important finding, then either re-run the verifier or (for trivial fixes) inspect your own changes and confirm the findings are addressed. Do not close until you have an APPROVE verdict or an explicit user override.

**Record the verdict** in the Implementation Log as a **Reflection** subsection:

```markdown
**Reflection** (via pre-close-verifier):
- Verdict: APPROVE | REQUEST CHANGES → addressed
- Coverage: X/Y requirements addressed
- Shortcuts found: <list or "none">
- Scope drift: <list or "none">
- Stragglers: <list or "none">
```

**When you cannot delegate** (e.g., the Agent tool is unavailable or the subagent returns an error), fall back to running the same checks inline — coverage, shortcut hunt, scope drift, straggler grep — using the pre-close-verifier SKILL.md ([.claude/agents/pre-close-verifier.md](../../agents/pre-close-verifier.md)) as your checklist.

### 7. Pre-close verification

Before creating the close commit:

- `grep -r "<key term>" .marcel/skills/ .claude/skills/ docs/ project/` for key terms from the changes (convention names, emoji, format strings) to find files that reference old patterns.
- Verify all tasks and subtasks in the issue show `[✓]`.
- If you find missed files, commit them as a final `🔧 [ISSUE-{hash}] impl:` commit before the close.
- **Docs and version bumps ship in the LAST `🔧 impl:` commit, not in `✅ close`.** The close commit is a pure status marker.

### 8. Close on the feature branch

```bash
git mv project/issues/wip/ISSUE-{YYMMDD}-{hash}-{slug}.md project/issues/closed/ISSUE-{YYMMDD}-{hash}-{slug}.md
# Update Status: Closed inside the file
git add "project/issues/closed/ISSUE-{YYMMDD}-{hash}-{slug}.md"
git commit -m "✅ [ISSUE-{hash}] closed: <one-line summary of what was completed>"
```

### 9. Merge back to main

**First, detect whether you're in a worktree.** The primary checkout is the first entry in `git worktree list --porcelain`; any other entry is a worktree. If you started via `/parallel-issue`, you are in a worktree.

```bash
MAIN_REPO=$(git worktree list --porcelain | awk '/^worktree / {print $2; exit}')
HERE=$(git rev-parse --show-toplevel)
```

**Case A — standard (no worktree).** You're in the primary checkout, on the feature branch:

```bash
git checkout main
git pull --ff-only
git merge --no-ff "issue/{hash}-{slug}" -m "merge issue/{hash}-{slug}"
git branch -d "issue/{hash}-{slug}"
```

**Case B — worktree (`HERE` is NOT the same as `MAIN_REPO`).** You can't merge into a branch that is checked out elsewhere, so switch to the main checkout first, merge, then remove the worktree:

```bash
cd "$MAIN_REPO"
git checkout main
git pull --ff-only
git merge --no-ff "issue/{hash}-{slug}" -m "merge issue/{hash}-{slug}"
git worktree remove "$HERE"
git branch -d "issue/{hash}-{slug}"
```

`git worktree remove` refuses if the worktree has uncommitted changes — that's a safety feature; commit or stash first. After removal, the sibling directory is gone and the feature branch can be deleted.

`--no-ff` preserves the branch shape in `git log --graph` in both cases.

### 10. Capture lessons learned (with rotation)

`project/lessons-learned.md` is capped at **10 active entries**. Adding a new one means moving the oldest to the archive in the same commit.

1. **Count existing entries:** `grep -c '^## ISSUE-' project/lessons-learned.md`
2. **Append the new entry** at the top of the entries section (after the `---` that ends the preamble), using the format below
3. **If the count was already 10**, cut the oldest entry from `project/lessons-learned.md` and paste it at the top of the entries section of `project/lessons-learned-archive.md`. The archive is unbounded.

New entry format:

```markdown
---

## ISSUE-{hash}: Title (YYYY-MM-DD)

### What worked well
- <patterns worth repeating>

### What to do differently
- <mistakes or friction encountered>

### Patterns to reuse
- <code patterns, design decisions worth remembering>
```

Focus on things that surprised you, caused rework, or would save time next time. Keep each bullet to 1-2 sentences.

Commit on main as a small follow-up. Include both files if you rotated:

```bash
git add project/lessons-learned.md project/lessons-learned-archive.md
git commit -m "🩹 [ISSUE-{hash}] fixup: capture lessons learned"
```

### 11. Report back

Tell the user:
- Which tasks were marked done vs incomplete
- Any tasks left open and why
- Reflection findings (shortcuts found, scope drift)
- Lessons captured
- The merge commit hash on main
