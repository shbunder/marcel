---
name: finish-feature
description: Finish a Feature — verify every story is done, run make check, verify no shortcuts, merge the feature branch back to main, and flip the feature to Done on the marcel-admin board. Use when all implementation work on a feat/FEAT-… branch is complete. Do NOT use to abandon a feature mid-work.
---

Finish work on feature: $ARGUMENTS

The code lives in this repo on `feat/FEAT-…-slug`; the board lives in `marcel-admin`. Conventions: `$MARCEL_ADMIN_DIR/JIRA/CLAUDE.md`. This skill is the procedural wrapper — stage by name in both repos.

```bash
ADMIN="${MARCEL_ADMIN_DIR:-$HOME/projects/marcel-admin}"
JIRA="$ADMIN/JIRA/jira.py"
```

## Steps

### 1. Locate the feature + confirm the branch

Resolve `$ARGUMENTS` to a `FEAT-…` (accepts the hash, the id, or the slug). You should be on `feat/FEAT-…-slug` in the code repo — if not, `git checkout` it. Read the feature file (`$ADMIN/JIRA/features/FEAT-….md`) and its stories (`python3 "$JIRA" list stories --feature FEAT-…`).

### 2. Commit any uncommitted code work

```bash
git status
git add <relevant source + SDK-doc files>
git commit -m "[STORY-…] impl: <brief description>"
```

Stage by name. Code commits carry source + `marcel/docs/` changes only — never ticket/status edits (those go in `marcel-admin`).

### 3. Determine what was actually done

```bash
git diff main...HEAD
```

Read the changed files and cross-reference with each story's acceptance criteria and subtasks.

### 4. Update story progress on the board (in marcel-admin)

For each story, tick acceptance criteria and subtasks that the diff satisfies, append a progress note, and set status:

```bash
python3 "$JIRA" check STORY-… <n> --section acceptance     # tick acceptance criterion n
python3 "$JIRA" check STORY-… <n>                          # tick subtask n
python3 "$JIRA" note  STORY-… "<what landed, test/coverage summary>"
python3 "$JIRA" set   STORY-… status Done                  # only if its acceptance criteria are met + tested
```

A story is `Done` only when its acceptance criteria have passing tests. Leave it `In Progress` (and say so) if incomplete.

### 5. Run the testing gate in the code repo

```bash
make check    # format + lint + typecheck + tests with coverage — all must pass
```

A feature does not merge until `make check` is green. Fix failures (as further `[STORY-…]` commits) before continuing.

### 6. Delegate verification to the pre-close-verifier subagent

A fresh context is less biased than the one that wrote the code:

```
Agent(
  subagent_type="pre-close-verifier",
  description="Pre-merge verification FEAT-…",
  prompt="Verify feature branch feat/FEAT-…-slug before merge. "
         "Board files: $MARCEL_ADMIN_DIR/JIRA/features/FEAT-….md and its stories. "
         "Run `git diff main...HEAD` in the marcel code repo. "
         "Hunt for shortcuts, scope drift, and stragglers (files that reference changed "
         "conventions but weren't updated). Return the structured verdict."
)
```

Fix every Critical/Important finding (as `[STORY-…]` commits) before merging. Record the verdict as a **Reflection** note on the feature, and capture the verifier's **traceability matrix** (each acceptance criterion → the test that proves it) in that note — it is the durable record that the feature's requirements were actually met:

```bash
python3 "$JIRA" note FEAT-… "Reflection (pre-close-verifier): verdict …; traceability (criterion → test): …; shortcuts …; scope drift …; stragglers …"
```

When you cannot delegate, run the same checks inline using [.claude/agents/pre-close-verifier.md](../../agents/pre-close-verifier.md) as the checklist.

### 7. Straggler grep + Lessons

```bash
grep -rn "<key term>" "$ADMIN/WIKI" "$ADMIN/JIRA" .claude/ docs/ project/ ~/.marcel/ 2>/dev/null
```

for convention names / symbols you changed; update any stragglers (as code commits in marcel, or doc commits in marcel-admin, whichever repo they live in). Append a **Lessons** note to the feature (`python3 "$JIRA" note FEAT-… "Lessons: …"`).

### 8. Tick the feature's story checklist + acceptance

In `$ADMIN/JIRA/features/FEAT-….md`, tick the `## Stories` checkbox for each `Done` story and the `## Acceptance criteria` bullets the feature now meets (use `Edit`; the CLI does not toggle the feature's own lists).

### 9. Merge the feature branch back to main (code repo)

Detect a worktree first (if you started via `/parallel-feature`):

```bash
MAIN_REPO=$(git worktree list --porcelain | awk '/^worktree / {print $2; exit}')
HERE=$(git rev-parse --show-toplevel)
```

**Standard (no worktree):**
```bash
git checkout main && git pull --ff-only
git merge --no-ff "feat/FEAT-…-slug" -m "merge feat/FEAT-…-slug (FEAT-…)"
git branch -d "feat/FEAT-…-slug"
```

**Worktree (`HERE` != `MAIN_REPO`):**
```bash
cd "$MAIN_REPO" && git checkout main && git pull --ff-only
git merge --no-ff "feat/FEAT-…-slug" -m "merge feat/FEAT-…-slug (FEAT-…)"
git worktree remove "$HERE" && git branch -d "feat/FEAT-…-slug"
```

`--no-ff` preserves the branch shape. There is **no** separate code-side "close commit" — the close marker is the board status flip in the next step.

### 10. Flip the feature to Done on the board

```bash
python3 "$JIRA" set FEAT-… status Done
git -C "$ADMIN" add JIRA/features/FEAT-*.md JIRA/stories/STORY-*.md WIKI/requirements/FEAT-*.md log.md
git -C "$ADMIN" commit -m "FEAT-…: done — <one-line summary>"
```

This board commit is the audit marker that the feature is complete — pure ticket/status, no code.

### 11. Report back

Tell the user: which stories are Done vs left open (and why), the pre-close-verifier verdict, lessons captured, the merge commit hash on `main` (code repo), and the board status commit (marcel-admin).
