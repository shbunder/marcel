---
name: parallel-feature
description: Create a new Feature on the marcel-admin board AND a git worktree in the marcel code repo, so another Claude Code session can implement it without disturbing the current checkout. Use when running two agents on Marcel simultaneously. For single-agent work prefer /new-feature — it's lighter.
---

Create a new feature in a parallel worktree for: $ARGUMENTS

The parallel-agent variant of `/new-feature`. It does everything `/new-feature` does (allocate the feature + requirements + ADR(s) on the `marcel-admin` board), PLUS creates a git worktree in a sibling directory of the **code repo** so a separate session can implement it. Conventions: `$MARCEL_ADMIN_DIR/JIRA/CLAUDE.md`.

```bash
ADMIN="${MARCEL_ADMIN_DIR:-$HOME/projects/marcel-admin}"
JIRA="$ADMIN/JIRA/jira.py"
```

## Why worktrees, not branches alone

Date+hash IDs prevent ticket collisions and feature branches isolate commit history, but two Claude Code sessions inside the same code checkout still share one `HEAD` — `git checkout` in session A yanks files out from under session B. A git worktree is a separate directory sharing the `.git` store but with its own `HEAD` — true isolation. (The `marcel-admin` board is shared and stays on `main`; only the code checkout needs a worktree.)

## Steps

### 1. Ensure clean main in the primary code checkout

```bash
git checkout main && git pull --ff-only && git status   # must be clean
```

If dirty or on a feature branch, stop and tell the user to finish or stash first.

### 2. Allocate the feature + requirements on the board

```bash
python3 "$JIRA" new-feature "<Title>" --priority <High|Medium|Low>
```

Note the `FEAT-…` id and the `feat/FEAT-…-slug` branch name it prints.

### 3. Write the prose + ADR(s), commit on the board

Same as `/new-feature` steps 3–5: fill the requirements page and feature Summary/acceptance, create ADR(s) with `python3 "$JIRA" new-adr FEAT-… "…"`, then commit in marcel-admin:

```bash
git -C "$ADMIN" add JIRA/features/FEAT-….md WIKI/requirements/FEAT-….md WIKI/decisions/ADR-*.md log.md
git -C "$ADMIN" commit -m "FEAT-…: create <slug> — feature, requirements, ADR(s)"
```

### 4. Create the worktree with the feature branch (code repo)

```bash
REPO_NAME=$(basename "$(git rev-parse --show-toplevel)")
WORKTREE_PATH="../${REPO_NAME}-feat-${FEAT_HASH}"
git worktree add "${WORKTREE_PATH}" -b "feat/FEAT-…-slug"
```

The worktree is a sibling of the main code checkout; the main checkout stays on `main`, so other agents are undisturbed.

### 5. Report back with startup instructions

Tell the user:
- The `FEAT-…` id, the branch name, and the worktree absolute path
- **How to open a fresh Claude Code session in the worktree** — e.g. VSCode `File → Open Folder → ${WORKTREE_PATH}` then launch the extension, or `cd ${WORKTREE_PATH} && claude`
- That stories are added with `/new-story FEAT-… "…"` and the feature is closed with `/finish-feature` from inside the worktree (it detects the worktree, merges, and cleans up)

## Caveats the user needs to know

- **Python venv.** A fresh worktree has no `.venv` until `make install` (or symlink it from the main checkout); `make check` fails until Python deps exist.
- **Port collisions.** Two worktrees running `make serve` fight over the same port unless you pass a different one.
- **Disk cost.** Each worktree is a full source checkout (cheap on history, not on working-tree files).
- **The board is shared.** Both sessions write to the same `marcel-admin` repo — that's fine (different feature/story files), but commit board edits promptly to avoid confusion.
- **Cleanup is automatic at close** — `/finish-feature` from inside the worktree runs `git worktree remove` after merging. If abandoned, clean up with `git worktree remove ${WORKTREE_PATH}` from the main checkout.
