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

Two rules apply to every commit in this repo:

- **Stage by name, never broadly** — enforced by [.claude/rules/git-staging.md](../../.claude/rules/git-staging.md). No `git add .`, `git add -A`, or `git commit -a`.
- **`✅ close` commits are pure status markers** — enforced by [.claude/rules/closing-commit-purity.md](../../.claude/rules/closing-commit-purity.md). Docs updates and version bumps go in a **final `🔧 impl:` commit BEFORE the close**, never combined with it.

Per-emoji specifics:

- **`📝 create`:** only stage `./project/issues/open/ISSUE-*.md`.
- **`🔧 impl`:** stage the issue file (for the open→wip move on the first impl commit) and the relevant source files. Also the place where docs land — see [docs-in-impl](../../.claude/rules/docs-in-impl.md).
- **`✅ close`:** only stage the issue file move to `./project/issues/closed/` plus the in-file status update.
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

## Parallel agents — git worktrees

Hash-based IDs prevent counter collisions on issue numbers, and feature branches isolate commit history. Neither of those prevent *working-directory* collisions: two Claude Code sessions inside the same checkout share one `HEAD`, so `git checkout issue/abc` in session A yanks files out from under session B.

For genuinely parallel agents, use **git worktrees**. A worktree is a separate directory on disk that has its own `HEAD` but shares the `.git` history store with the primary checkout — true isolation without a full clone.

### Creating a worktree

The `/parallel-issue` skill does this automatically. The equivalent manual commands:

```bash
# From the primary checkout on clean main
git checkout main && git pull --ff-only
git add project/issues/open/ISSUE-{YYMMDD}-{hash}-{slug}.md
git commit -m "📝 [ISSUE-{hash}] created: ..."

REPO_NAME=$(basename "$(git rev-parse --show-toplevel)")
git worktree add "../${REPO_NAME}-issue-{hash}" -b "issue/{hash}-{slug}"
```

The sibling directory is now a fully functional checkout of the feature branch. Open a new Claude Code session with that directory as `cwd` — VSCode "Open Folder" or `cd && claude` in a terminal.

### Closing from a worktree

`/finish-issue` detects a worktree context (by comparing `git rev-parse --show-toplevel` against the first entry of `git worktree list --porcelain`) and runs the merge from the primary checkout before removing the worktree. The manual equivalent:

```bash
# From inside the worktree, after the ✅ close commit
MAIN_REPO=$(git worktree list --porcelain | awk '/^worktree / {print $2; exit}')
HERE=$(git rev-parse --show-toplevel)

cd "$MAIN_REPO"
git checkout main
git pull --ff-only
git merge --no-ff "issue/{hash}-{slug}" -m "merge issue/{hash}-{slug}"
git worktree remove "$HERE"
git branch -d "issue/{hash}-{slug}"
```

`git worktree remove` refuses if there are uncommitted changes — that is a safety feature, not a bug. Commit or stash first.

### Caveats

- **Python venv.** A worktree is a fresh checkout — it has no `.venv` until you run `make install` (or symlink `.venv` from the primary checkout). `make check` will fail until Python deps are available.
- **Port collisions.** `make serve` binds a port. Two worktrees running the dev server at once need different ports.
- **Disk cost.** Each worktree is a full source-tree checkout. The `.git` history is shared, but the working files are duplicated.
- **Abandoned worktrees.** If a session is closed without `/finish-issue`, the worktree stays on disk. `git worktree list` shows all active worktrees; `git worktree remove <path>` cleans one up manually.

When NOT to use worktrees: single-agent work on one issue at a time. Use `/new-issue` — the simple flow is lighter and avoids the venv/port caveats.

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
