# ISSUE-017: Fixup / Amendment SOP

**Status:** Closed
**Created:** 2026-03-29
**Assignee:** Marcel
**Priority:** Medium
**Labels:** docs, process

## Capture
**Original request:** "amendments seem to happen a lot, how would be best to tackle this? it looks like you re-opened an issue now in the commit history. Write a proper SOP for amendments and small changes"

**Follow-up Q&A:**
- Agreed on using `🩹` (adhesive bandage) emoji for post-close fixups to distinguish them from implementation commits (`🔧`).
- Agreed on two-tier rule: trivial same-scope corrections get a `🩹 fixup` commit; substantial or new-scope work gets a new issue.
- Agreed to add a pre-close verification step to catch these before they happen.

**Resolved intent:** Define a clear convention for handling post-close corrections so the git log stays clean and unambiguous. Currently, a `🔧 impl` commit after a `✅ closed` commit looks like a reopened issue. The fix is twofold: (1) add a pre-close verification checklist to catch missed items before closing, and (2) introduce a `🩹 fixup` commit type for genuine post-close corrections that are trivial and same-scope.

## Description
ISSUE-016 exposed the gap: after closing the issue, we discovered the skills hadn't been updated. The resulting `🔧` commit after `✅` was confusing in the log. This issue adds:

1. A `🩹` fixup commit convention — distinct emoji, clear semantics, no issue reopen
2. A pre-close verification step — check all files that reference changed conventions
3. Clear rules for when to use fixup vs. new issue

## Tasks
- [✓] ISSUE-017-a: Add `🩹` fixup emoji and rules to `project/issues/CLAUDE.md` Git Conventions section
- [✓] ISSUE-017-b: Add pre-close verification checklist to `project/issues/CLAUDE.md` Issue Lifecycle section
- [✓] ISSUE-017-c: Update `project/CLAUDE.md` Step 8 (Ship) to reference verification checklist
- [✓] ISSUE-017-d: Verify finish-issue skill aligns with new verification + fixup rules

## Relationships
- Related to: [[ISSUE-016-clean-commit-workflow-sop]]

## Comments

## Implementation Log

### 2026-03-29 - LLM Implementation
**Action**: Added fixup convention and pre-close verification to SOP
**Files Modified**:
- `project/issues/CLAUDE.md` — added 🩹 to emoji table, added "Post-close fixups" subsection with when-to-use/when-not-to rules, added verification bullet to pre-close checklist
- `project/CLAUDE.md` — added pre-close verification step and fixup reference to Step 8 (Ship)
- `.claude/skills/finish-issue/SKILL.md` — added step 6 (pre-close verification) with grep instructions, renumbered subsequent steps
**Result**: All four files (issues CLAUDE.md, project CLAUDE.md, finish-issue skill, new-issue skill) are consistent with the new workflow
