# Git Conventions for Issues

Every issue produces a clean, predictable sequence of commits across one `main` commit and one feature branch. This is mandatory — no shortcuts, no combining steps that should be separate.

## Lifecycle at a glance

```
main:           📝 create ─────────────────────────── merge ── main
                         \                           /
branch:                   🔧 impl ── 🔧 impl ── ✅ close
```

- **Create** happens on `main` as a standalone `📝` commit that contains only the issue file in `open/`. No code changes.
- **Branch** is created immediately after `📝` (by the `/new-issue` skill) as `issue/{hash}-{slug}`. All implementation work happens here.
- **Implement** commits on the branch combine the `open/ → wip/` move (first impl commit only) with source code changes, prefixed `🔧 [ISSUE-{hash}] impl: ...`.
- **Close** is a standalone commit on the branch that moves the issue file from `wip/ → closed/` and updates `Status: Closed`. No code changes.
- **Merge** uses `git merge --no-ff` from `main` to preserve the branch topology. Main goes from `open/` → (via merge) → `closed/` with no `wip/` state.

## Commit message format

```
<emoji> [ISSUE-{hash}] <action>: <description>
```

| Emoji | Meaning | Where | Standalone? |
|-------|---------|-------|-------------|
| 📝 | Issue created | main | Yes — only the issue file |
| 🔧 | Implementation work | feature branch | Yes — may be multiple commits; first one also moves file to `wip/` |
| ✅ | Issue closed | feature branch | Yes — only the issue file move + status |
| 🩹 | Post-close fixup | main (after merge) | Yes — trivial correction, no issue move |

Reserve `🐛`, `🚀`, `📚` for **issue labels** inside the issue file, not for commit messages.

## Staging rules

- **`📝 create`:** only stage `./project/issues/open/ISSUE-*.md`. Never `git add .` or `git add -A`.
- **`🔧 impl`:** stage the issue file (for the open→wip move on the first impl commit) AND the relevant source files. Be explicit — name the files, don't use `git add .`.
- **`✅ close`:** only stage `./project/issues/` (for the wip→closed move). Docs updates and version bumps go in a separate `🔧 impl` commit BEFORE the close commit, not combined with it. This is a change from the legacy convention — under the new flow, the closing commit is purely the status marker.
- **`🩹 fixup`:** stage only the files that need correcting. Do not reopen or move the issue file.

## Multi-commit implementations

When a feature requires multiple implementation commits:

- The **first** commit moves the issue to `wip/` AND includes initial code: `🔧 [ISSUE-{hash}] impl: <first chunk>`
- **Subsequent** commits continue the work: `🔧 [ISSUE-{hash}] impl: <next chunk>`
- All implementation commits use `🔧` — no other emojis during implementation.

## Merging back to main

```bash
git checkout main
git pull --ff-only
git merge --no-ff "issue/{hash}-{slug}" -m "merge issue/{hash}-{slug}"
git push origin main
git branch -d "issue/{hash}-{slug}"
```

The `--no-ff` flag preserves the branch shape in `git log --graph`, so the issue's commit sequence stays visible as a distinct topic. Do NOT fast-forward merge issue branches.

## Post-close fixups

Sometimes you catch a small mistake after closing an issue — a typo, a missed file, a convention not applied to a related template. Use a fixup commit on `main` instead of reopening the issue or creating a new one.

```
🩹 [ISSUE-{hash}] fixup: <what was corrected>
```

**When to use `🩹 fixup`:**
- The fix is trivial (a few lines, no design decisions)
- It corrects work done under the same issue (same scope)
- The issue is already closed

**When to create a new issue instead:**
- The fix is substantial (new logic, new files, needs testing)
- It's new scope that wasn't part of the original issue
- It would take more than a few minutes

**Fixup staging:** stage only the files that need correcting. Do NOT reopen or move the issue file.

## Linking issues

Use `[[ISSUE-{hash}-title]]` — **never include directory paths**. Links are plain text; they don't auto-update when files move. When updating relationships after a close:

1. Check for references: `grep -r "ISSUE-{hash}" ./project/issues/`
2. Update relationship context in affected issues (e.g., a "blocked by" that is now closed)
3. Add a system note comment to any issue whose relationship status changed

Semantic relationship labels: `Depends on`, `Blocks`, `Implements`, `Related to`, `Parent`, `Duplicate of`.

## Legacy `ISSUE-NNN` scheme

Issues created before ISSUE-079 use the legacy sequential counter (`ISSUE-042-fix-login-bug.md`). They are NOT migrated to the new hash scheme — ~50 code comments reference them. Treat them as read-only history. Only new issues use `ISSUE-{YYMMDD}-{hash}-{slug}.md`.

## Useful queries

```bash
# Active issue branches (replaces `ls wip/*.md`)
git branch --list 'issue/*'

# Status overview
for dir in open closed; do echo "$dir: $(ls ./project/issues/$dir/*.md 2>/dev/null | wc -l)"; done

# High-priority open issues
grep -rl "Priority.*High" ./project/issues/open/

# WIP subtasks on current branch
grep -r "\[⚒\]" ./project/issues/wip/

# Find references to an issue
grep -r "ISSUE-a1b2c3" ./project/issues/

# Recent implementation work
git log --oneline --grep="🔧" --since="2 days ago"
```
