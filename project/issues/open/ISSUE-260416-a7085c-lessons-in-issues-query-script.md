# ISSUE-a7085c: Embed lessons learned in issue files, remove rotation mechanism, add query script

**Status:** Open
**Created:** 2026-04-16
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, workflow, tooling

## Capture

**Original request:** Embed lessons learned in issue files, remove rotation mechanism, add query script

**Follow-up Q&A:**
- Should existing entries in lessons-learned.md and lessons-learned-archive.md be migrated back to their issue files? → Yes, approximate migration is fine, doesn't have to be 100% correct.
- Should the query script filter by keyword and sort by date/relevance? → Yes.

**Resolved intent:** The current `/finish-issue` Step 10 asks Claude to manually count entries, cut the oldest from `lessons-learned.md`, and paste it into `lessons-learned-archive.md` — a fragile text surgery that repeatedly leaves orphaned `---` separators and requires a separate fixup commit after every merge. The fix is to embed lessons learned directly in the issue file (in the `✅ close` commit), remove the global files entirely, and add a `scripts/query_lessons.py` helper that greps closed issue files for matching lessons filtered by keyword and sorted by date descending.

## Description

**Problem:** Step 10 of `/finish-issue` is the most error-prone step in the workflow:
1. Claude has to count entries, find the oldest, cut it out, paste it into a separate archive file — all as text surgery that consistently leaves orphaned `---` dividers.
2. It produces a separate `🩹 fixup` commit after the merge, adding cognitive overhead after the "real" work is done.
3. The active `lessons-learned.md` is always-loaded (241 lines per session) even when irrelevant.

**Solution:**
- Add a `## Lessons Learned` section to the issue template and the close commit's allowed contents.
- Remove the rotation step from `/finish-issue`: Claude writes lessons directly into the issue file before the `✅ close` commit.
- Migrate existing entries from `lessons-learned.md` and `lessons-learned-archive.md` back to their respective closed issue files (approximate — good enough).
- Delete (or deprecate) both legacy files.
- Add `scripts/query_lessons.py` to replace `grep` across two files: keyword search + date-sorted output across all closed issue files.

## Tasks

- [ ] Add `## Lessons Learned` section to `project/issues/TEMPLATE.md` (with the three subsections: What worked well / What to do differently / Patterns to reuse)
- [ ] Update `closing-commit-purity.md` rule to list `## Lessons Learned` as an allowed item in the `✅ close` commit
- [ ] Update `.claude/skills/finish-issue/SKILL.md` — replace Step 10 (rotation) with: write lessons into the issue file as part of the close commit; remove the fixup commit step
- [ ] Write `scripts/migrate_lessons.py` — parses both legacy files, maps each entry to its closed issue file by hash/number, appends a `## Lessons Learned` section (skips if already present)
- [ ] Run the migration script and verify output across a sample of issue files
- [ ] Write `scripts/query_lessons.py` — keyword search across `project/issues/closed/*.md` Lessons Learned sections, sorted by issue date descending; also searches legacy files for backward compat
- [ ] Update `project/FEATURE_WORKFLOW.md` Step 1 — replace grep command with `python scripts/query_lessons.py <keyword>`
- [ ] Add deprecation header to `project/lessons-learned.md` and `project/lessons-learned-archive.md` (or delete them if migration is clean)
- [ ] Update `docs/claude-code-setup.md` — remove rotation policy, document new query script
- [ ] Update `project/issues/CLAUDE.md` — fix rationalization table entry that references "Step 9 of /finish-issue"

## Relationships

None.

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
