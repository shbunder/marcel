# ISSUE-016: Clean Commit Workflow SOP

**Status:** Closed
**Created:** 2026-03-29
**Assignee:** Marcel
**Priority:** Medium
**Labels:** docs, process

## Capture
**Original request:** "do a thorough walkthrough of Marcel's SOP, if a new feature request comes in a ticket needs to be created first with an issue description, this ticket needs to be committed as opened. when we start work we move the ticket to wip and commit again. then we do the actual work, commit again, and lastly after everything is tested and working we close the ticket and commit again (opening, and moving tickets should be separate commits, moving to wip and doing the work can be in one ticket)"

**Follow-up Q&A:**
- Reviewed current git log and found lifecycle commits are inconsistently applied — most issues skip creation and closing commits entirely.
- Proposed standardizing on a 3-emoji pattern (`📝 created`, `🔧 implemented`, `✅ closed`) and resolving ambiguities around multi-commit implementations, staging rules, and closing commit contents.
- User agreed with all suggestions.

**Resolved intent:** Update the issue management SOP so that every issue produces a clean, predictable sequence of commits in `git log`. The docs currently define lifecycle transitions but don't enforce them as separate commits, leading to a messy history. This change codifies the exact commit workflow: standalone create commit, combined WIP+implementation commit(s), standalone closing commit — and resolves the emoji conflicts, staging rules, and multi-commit edge cases.

## Description
The current `project/issues/CLAUDE.md` and `project/CLAUDE.md` describe an issue lifecycle (open → wip → closed) and a commit emoji table, but the rules are loose enough that in practice most issues skip lifecycle commits entirely. The git log is inconsistent — some issues have `📝` and `🛠️ ⇨ ✅` commits, most don't.

This issue tightens the SOP to make the commit workflow explicit and unambiguous:
1. Standardize the 4-step commit workflow with clear rules about what each commit contains
2. Resolve the emoji conflict (issue-type emojis vs lifecycle emojis)
3. Define rules for multi-commit implementations
4. Update the staging rule to allow combined issue+code commits for WIP+implementation
5. Specify what the closing commit contains (issue file move, docs, version bump — no code)

## Tasks
- [✓] ISSUE-016-a: Update `project/issues/CLAUDE.md` — rewrite Git Conventions section with explicit 4-step commit workflow
- [✓] ISSUE-016-b: Update `project/issues/CLAUDE.md` — simplify emoji table to the clean 3-emoji pattern, add multi-commit rules
- [✓] ISSUE-016-c: Update `project/issues/CLAUDE.md` — fix staging rule to allow combined issue+code commits for implementation
- [✓] ISSUE-016-d: Update `project/CLAUDE.md` — align Feature Development Procedure step 8 (Ship) with new commit workflow
- [✓] ISSUE-016-e: Verify consistency between both files after edits

## Relationships
- Related to: [[ISSUE-014-sop-telegram-issue-tracking]]

## Comments

## Implementation Log

### 2026-03-29 - LLM Implementation
**Action**: Rewrote Git Conventions section and aligned Feature Development Procedure
**Files Modified**:
- `project/issues/CLAUDE.md` — replaced Git Conventions section: new 3-step commit workflow table, rules, multi-commit implementation guidance, staging rules, simplified emoji reference (📝/🔧/✅ only)
- `project/CLAUDE.md` — updated Step 3 (Create an issue) to mandate standalone 📝 commit; rewrote Step 8 (Ship) to define closing commit contents (no code, only issue move + docs + version bump); updated Telegram-Initiated Changes to reference new workflow
**Result**: Both files are consistent. The commit workflow is now explicit and unambiguous.

## Lessons Learned

### What worked well
- The 3-emoji pattern (📝→🔧→✅) makes `git log --oneline` instantly scannable
- Separating the closing commit from code ensures code review happens on implementation commits

### What to do differently
- Should have established this convention from ISSUE-001 — retroactive cleanup is painful
- The "first impl commit moves to wip" rule avoids an empty "I started" commit — non-obvious but important

### Patterns to reuse
- Standalone decision commits (📝) create clear audit trail of "we decided to do this"
- Post-close fixup emoji (🩹) prevents reopening issues for trivial corrections
