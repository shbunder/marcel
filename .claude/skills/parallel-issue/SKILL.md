---
name: parallel-issue
description: Create a new issue AND a git worktree so another Claude Code session can work on it without disturbing the current checkout. Use when the user wants to run two agents on Marcel simultaneously. For single-agent work, prefer `/new-issue` — it's lighter.
---

Create a new issue in a parallel worktree for: $ARGUMENTS

This skill is the parallel-agent variant of `/new-issue`. It does everything `/new-issue` does, PLUS creates a git worktree in a sibling directory so a separate Claude Code session can work on the issue without stomping the main checkout. Use this when you want to spin up a second agent.

## Why worktrees, not branches alone

Hash-based IDs prevent counter collisions and feature branches isolate commit history, but two Claude Code sessions inside the same checkout still share one `HEAD`. `git checkout` in session A yanks the files out from under session B. A git worktree is a separate directory on disk that shares the `.git` history store but has its own `HEAD` — true isolation between concurrent agents.

Full conventions: [project/issues/CLAUDE.md](../../../project/issues/CLAUDE.md) and [project/issues/GIT_CONVENTIONS.md](../../../project/issues/GIT_CONVENTIONS.md).

## Steps

### 1. Ensure clean main in the primary checkout

```bash
git checkout main
git pull --ff-only
git status  # must be clean
```

If the working tree is dirty or you're on a feature branch, stop and tell the user to finish or stash existing work first.

### 2. Generate a collision-free hash

```bash
while true; do
  HASH=$(python3 -c 'import secrets; print(secrets.token_hex(3))')
  if [ -z "$(ls project/issues/*/ISSUE-*-${HASH}-*.md 2>/dev/null)" ]; then
    break
  fi
done
```

### 3. Derive date and slug

```bash
DATE=$(date +%y%m%d)
```

Slug: short kebab-case from the request (3–5 words, no stop words). Filename: `project/issues/open/ISSUE-${DATE}-${HASH}-${SLUG}.md`.

### 4. Write the issue file

Use the template in [project/issues/TEMPLATE.md](../../../project/issues/TEMPLATE.md). Fill in ID (`ISSUE-${HASH}`), Status (Open), Created (today), Capture (verbatim), Resolved intent, Description, Tasks. Leave Relationships empty unless you can infer dependencies.

### 5. Commit the issue file on main

```bash
git add "project/issues/open/ISSUE-${DATE}-${HASH}-${SLUG}.md"
git commit -m "📝 [ISSUE-${HASH}] created: ${SLUG} — one-line description"
```

Stage only the issue file.

### 6. Create the worktree with the feature branch

```bash
REPO_NAME=$(basename "$(git rev-parse --show-toplevel)")
WORKTREE_PATH="../${REPO_NAME}-issue-${HASH}"
git worktree add "${WORKTREE_PATH}" -b "issue/${HASH}-${SLUG}"
```

The worktree is a sibling directory of the main checkout. The new feature branch is created and checked out there. The main checkout stays on `main` — other agents are undisturbed.

### 7. Report back with startup instructions

Tell the user:
- The hash ID (`ISSUE-${HASH}`)
- The branch name (`issue/${HASH}-${SLUG}`)
- The worktree absolute path
- **How to open a fresh Claude Code session in the worktree.** For example:
  - VSCode: `File → Open Folder → ${WORKTREE_PATH}`, then launch the Claude Code extension there
  - Terminal: `cd ${WORKTREE_PATH} && claude`
- The task list for that session to confirm scope
- A reminder that `/finish-issue` in the worktree will merge and clean up automatically

## Caveats the user needs to know

- **Python venv.** The worktree gets a fresh checkout — if the project uses a local `.venv`, the worktree won't have one until you run `make install` (or symlink `.venv` from the main checkout, which works but is non-obvious). `make check` will fail until Python deps are available.
- **Port collisions.** `make serve` binds a port. Two worktrees running the dev server simultaneously will fight over the same port unless you pass a different one per worktree.
- **Disk cost.** Each worktree is a full checkout of the source tree. Cheap on history (shared `.git`) but not free on working-tree files.
- **Cleanup is automatic at close time** — `/finish-issue` from inside the worktree detects it and runs `git worktree remove` from the main repo after merging. If you abandon a worktree without closing, clean it up manually with `git worktree remove ${WORKTREE_PATH}` from the main checkout.
