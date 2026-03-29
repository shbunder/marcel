# Issue Management

Issues are tracked as markdown files in this directory, versioned with git. No external tools or databases — just files, text, and consistent conventions.

## Directory Structure

```
./project/issues/
  open/    # New, unstarted issues
  wip/     # Work in progress
  closed/  # Completed or cancelled
```

## File Naming

`ISSUE-{number}-{brief-title}.md` — e.g., `ISSUE-042-fix-login-bug.md`

- Numbers zero-padded to 3 digits
- Kebab-case titles
- Find next number: `find ./project/issues -name 'ISSUE-*.md' | grep -oE 'ISSUE-[0-9]+' | sort -t- -k2 -n | tail -1`

## Issue Template

```markdown
# ISSUE-042: Fix Login Bug

**Status:** Open | WIP | Closed
**Created:** YYYY-MM-DD
**Assignee:** Name | Unassigned
**Priority:** High | Medium | Low
**Labels:** bug, feature, docs, ...

## Capture
**Original request:** [verbatim quote from the user]

**Follow-up Q&A:** [questions asked and answers received, if any]

**Resolved intent:** [one paragraph in your own words — what this actually is and why]

## Description
[What, why, and any relevant context — can reference the capture above]

## Tasks
- [ ] Task description
- [✓] Completed task

## Subtasks
- [ ] ISSUE-042-a: Research phase
- [⚒] ISSUE-042-b: Implementation
- [✓] ISSUE-042-c: Tests

## Relationships
- Depends on: [[ISSUE-039-auth-refactor]]
- Blocks: [[ISSUE-044-user-profile]]
- Implements: [[ISSUE-021-security-requirements]]

## Comments
### YYYY-MM-DD - Author
Comment text...

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
```

**Subtask naming:** append a letter suffix — `ISSUE-042-a`, `ISSUE-042-b`, `ISSUE-042-c`, ... Subtasks are inline checklist items within the parent file only, not separate files. Do not use `[[double brackets]]` when referencing subtasks — subtasks have no files of their own, so there is nothing to link to.

**Subtask states:** `[ ]` not started → `[⚒]` in progress → `[✓]` complete

## Issue Lifecycle

1. **Create** in `open/` with `Status: Open`
2. **Start work** → move to `wip/`, set `Status: WIP`; break into subtasks if complex
3. **Progress** → update task/subtask checkboxes; append to Implementation Log for any code changes
4. **Complete** → move to `closed/`, set `Status: Closed`; notify any issues that were blocked by this one

Before closing, verify:
- All tasks and subtasks show `[✓]`
- Dependent issues are unblocked and notified
- Implementation Log reflects all work done

## Git Conventions

Every issue produces a clean, predictable sequence of commits. This is **mandatory** — no shortcuts, no combining steps that should be separate.

### The commit workflow

Each issue follows exactly this commit sequence:

| Step | Commit message | What it contains | Standalone? |
|------|---------------|------------------|-------------|
| 1. Create | `📝 [ISSUE-XXX] created: one-line description` | Issue file in `open/` | Yes — separate commit |
| 2. Implement | `🔧 [ISSUE-XXX] impl: what was done` | Issue file moved to `wip/` (status → WIP) + all source code changes | Yes — may be multiple commits |
| 3. Close | `✅ [ISSUE-XXX] closed: summary` | Issue file moved to `closed/` (status → Closed) + docs + version bump | Yes — separate commit |

**Rules:**

- **Step 1 is always a standalone commit.** It contains only the issue file. No code, no other files. This is the "we decided to do this" marker.
- **Step 2 combines the WIP move with implementation.** The first implementation commit moves the issue file from `open/` to `wip/` and sets `Status: WIP`. This avoids an empty "I started" commit. Stage both the issue file and source code together.
- **Step 3 is always a standalone commit.** It moves the issue file from `wip/` to `closed/`, sets `Status: Closed`, and includes documentation updates and version bumps. It must **not** contain code changes — all code should already be committed in step 2.

### Multi-commit implementations

When a feature requires multiple implementation commits:

- The **first** commit moves the issue to `wip/` and includes initial code: `🔧 [ISSUE-XXX] impl: description of first chunk`
- **Subsequent** commits continue the work: `🔧 [ISSUE-XXX] impl: description of next chunk`
- All implementation commits use `🔧` — no other emojis during implementation.

### Staging rules

- **Step 1 (create) and step 3 (close):** only stage `./project/issues/` (plus `docs/` and version files for step 3). Never `git add .` or `git add -A`.
- **Step 2 (implement):** stage both `./project/issues/` and the relevant source files. Be explicit — name the files, don't use `git add .`.

### Commit format

```
<emoji> [ISSUE-XXX] <action>: <description>
```

### Emoji reference

| Emoji | Meaning | Used in step |
|-------|---------|-------------|
| 📝 | Issue created | 1 |
| 🔧 | Implementation work | 2 |
| ✅ | Issue closed | 3 |

Reserve `🐛`, `🚀`, `📚` for **issue labels** inside the issue file (bug, feature, docs) — not for commit messages. This keeps `git log --oneline` clean and scannable:

```
📝 [ISSUE-042] created: fix login authentication bug
🔧 [ISSUE-042] impl: add OAuth handler, update routes
🔧 [ISSUE-042] impl: add refresh token logic
✅ [ISSUE-042] closed: all tasks complete, docs updated
```

## Linking Issues

Always use `[[ISSUE-XXX-title]]` — **never include directory paths**. Links are plain text; they don't auto-update when files move. When moving an issue:

1. Check for references: `grep -r "ISSUE-042" ./project/issues/`
2. Update relationship context in affected issues (e.g., a "blocked by" that is now closed)
3. Add a system note comment to any issue whose relationship status changed

Use semantic relationship labels: `Depends on`, `Blocks`, `Implements`, `Related to`, `Parent`, `Duplicate of`.

## Implementation Log

When performing actual development work (code changes, test runs, debugging), **always append a log entry**:

```markdown
### YYYY-MM-DD HH:MM - LLM Implementation
**Action**: Implemented OAuth2 login flow
**Files Modified**:
- `src/auth/oauth.py` - Created OAuth handler
- `src/routes/auth.py` - Added login endpoints
**Commands Run**: `make test`
**Result**: Success — all tests passing
**Next**: Implement refresh token logic
```

Use the Comments section for decisions, blockers, and discussion. Use the Implementation Log for technical work. The distinction matters for audit and review.

## Useful Queries

```bash
# Status overview
for dir in open wip closed; do echo "$dir: $(ls ./project/issues/$dir/*.md 2>/dev/null | wc -l)"; done

# High-priority open/wip issues
grep -rl "Priority.*High" ./project/issues/{open,wip}/

# All WIP subtasks
grep -r "\[⚒\]" ./project/issues/wip/

# Check incomplete items before closing
grep -E "\[ \]|\[⚒\]" ./project/issues/wip/ISSUE-042-*.md

# Find references to an issue (before moving it)
grep -r "ISSUE-042" ./project/issues/

# Recent implementation work
git log --oneline --grep="🔧" --since="2 days ago"
```
