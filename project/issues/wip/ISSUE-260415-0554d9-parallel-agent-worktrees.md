# ISSUE-0554d9: Parallel-agent git worktrees — opt-in parallel skill

**Status:** WIP
**Created:** 2026-04-15
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** tooling, dx

## Capture

**Original request:** "what will happend if 2 claude-code sessions / agents are locally working on Marcel? will they not keep switching the branch on eachother?"

**Follow-up Q&A:**
- Observation: the ISSUE-079 redesign solved the commit-history collision (hash IDs + per-issue branches) but did NOT solve the working-directory collision — two Claude Code sessions in the same checkout share one `HEAD`.
- Proposed fix: git worktrees, where each concurrent agent works in a separate directory on disk that shares the same `.git` history store.
- Decision: go with Option 2 — keep `/new-issue` as the simple single-checkout flow; add a dedicated `/parallel-issue` skill for the "I want to spin up another agent" case. Trim the current docs so they don't overclaim parallel-safety.

**Resolved intent:** Add a new opt-in `/parallel-issue` skill that creates the issue AND a git worktree in a sibling directory, so a second Claude Code session can work on it without disturbing the main checkout. Update `/finish-issue` to detect when it's running inside a worktree and clean up after merge. Document the worktree pattern in `GIT_CONVENTIONS.md`. Trim any overclaims from existing docs so they distinguish "counter-collision safe" (hash IDs) from "working-directory safe" (worktrees).

## Description

ISSUE-079 shipped branch-per-issue workflow with self-generated hash IDs. That solves the *counter collision* problem (two agents picking the same ISSUE-NNN) but leaves the *working-directory collision* problem open: `git checkout issue/abc` in one session yanks the files out from under any other session pointed at the same `.git` directory.

Git worktrees are the idiomatic fix. This issue adds a `/parallel-issue` skill that wraps `git worktree add` and documents when to use it.

## Tasks

- [✓] Create `.claude/skills/parallel-issue/SKILL.md` — generates hash ID, `📝` commits the issue on main, creates a sibling worktree with the feature branch, reports the worktree path and a startup command
- [✓] Update `.claude/skills/finish-issue/SKILL.md` — detect worktree context, merge from the main repo path, remove the worktree, delete the branch
- [✓] Update `project/issues/GIT_CONVENTIONS.md` — add "Parallel agents" section with the worktree recipe and the port-collision warning for `make serve`
- [✓] Trim `project/issues/CLAUDE.md` — clarify that hash IDs prevent counter collisions but worktrees are needed for true working-directory isolation
- [✓] End-to-end verification: create an issue via `/parallel-issue` (dry-run the commands), confirm worktree cleanup command works

## Relationships

- Depends on: [[ISSUE-079-claude-code-setup-redesign]] — this is the follow-up that addresses the working-directory gap left by 079

## Comments

## Implementation Log

### 2026-04-15 - LLM Implementation
**Action**: Added opt-in `/parallel-issue` skill that creates a git worktree in a sibling directory for the feature branch. Updated `/finish-issue` to detect worktree context and clean up after merge. Documented the worktree pattern and the counter-collision vs working-directory-collision distinction.

**Files Modified**:
- `.claude/skills/parallel-issue/SKILL.md` (new) — mirrors `/new-issue` for steps 1–5, adds `git worktree add ../${REPO_NAME}-issue-${HASH} -b issue/${HASH}-${SLUG}` at step 6, reports worktree path and startup command at step 7. Caveats: Python venv, port collisions, disk cost, abandoned worktrees.
- `.claude/skills/finish-issue/SKILL.md` — step 9 now has two cases: (A) standard single-checkout merge, (B) worktree detected via `git rev-parse --show-toplevel` vs `git worktree list --porcelain | awk '/^worktree / {print $2; exit}'`, which `cd`s to the main repo before merging and runs `git worktree remove "$HERE"` after the merge.
- `.claude/skills/new-issue/SKILL.md` — cross-link to `/parallel-issue` for parallel-agent use cases.
- `project/issues/GIT_CONVENTIONS.md` — new "Parallel agents — git worktrees" section with motivation, manual recipe, close-from-worktree flow, caveats, and "when NOT to use" note.
- `project/issues/CLAUDE.md` — clarified that the hash prevents *counter* collisions; pointer to worktrees for true working-directory isolation. Added `git worktree list` to the active-work surface alongside `git branch --list 'issue/*'`.

**Result**: Parallel Claude Code sessions can now work on different issues in the same repo without clobbering each other's `HEAD`. Commands verified via a scratch worktree test (`git worktree add /tmp/marcel-scratch-worktree HEAD`, `git worktree remove`) — both succeeded cleanly.

**Reflection**:
- Coverage: 5/5 tasks addressed.
- Shortcuts found: none.
- Scope drift: none. The skill stays tightly scoped to "create issue + worktree"; session start of the second agent is left to the user (VSCode Open Folder or `cd && claude`) rather than hiding automation behind a wrapper — matches the "human-readable over clever" core principle.
- Verification: scratch worktree test passed; detection command returns the correct path when run from the primary checkout.
- This is the first issue under the new `ISSUE-{YYMMDD}-{hash}-{slug}.md` scheme and the first real end-to-end run of the branch-per-issue flow. Worked cleanly.
