# ISSUE-a7085c: Embed lessons learned in issue files, remove rotation mechanism, add query script

**Status:** Closed
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

- [✓] Add `## Lessons Learned` section to `project/issues/TEMPLATE.md` (with the three subsections: What worked well / What to do differently / Patterns to reuse)
- [✓] Update `closing-commit-purity.md` rule to list `## Lessons Learned` as an allowed item in the `✅ close` commit
- [✓] Update `.claude/skills/finish-issue/SKILL.md` — replace Step 10 (rotation) with: write lessons into the issue file as part of the close commit; remove the fixup commit step
- [✓] Write `scripts/migrate_lessons.py` — parses both legacy files, maps each entry to its closed issue file by hash/number, appends a `## Lessons Learned` section (skips if already present)
- [✓] Run the migration script and verify output across a sample of issue files
- [✓] Write `scripts/query_lessons.py` — keyword search across `project/issues/closed/*.md` Lessons Learned sections, sorted by issue date descending; also searches legacy files for backward compat
- [✓] Update `project/FEATURE_WORKFLOW.md` Step 1 — replace grep command with `python scripts/query_lessons.py <keyword>`
- [✓] Add deprecation header to `project/lessons-learned.md` and `project/lessons-learned-archive.md` (or delete them if migration is clean)
- [✓] Update `docs/claude-code-setup.md` — remove rotation policy, document new query script
- [✓] Update `project/issues/CLAUDE.md` — fix rationalization table entry that references "Step 9 of /finish-issue"

## Relationships

None.

## Implementation Log

### 2026-04-16 — LLM Implementation

**Action:** Replaced global lessons-learned rotation with per-issue sections and a query script.

**Files Modified:**
- `project/issues/TEMPLATE.md` — added `## Lessons Learned` section (3 subsections)
- `.claude/rules/closing-commit-purity.md` — added item 6 (Lessons Learned allowed in close commit)
- `.claude/skills/finish-issue/SKILL.md` — Step 8 now writes lessons before close commit; old Step 10 rotation block removed entirely
- `scripts/query_lessons.py` (new) — keyword search + date sort across all closed issue files; --top and --since filters; legacy file fallback with deduplication
- `scripts/migrate_lessons.py` (created and deleted) — one-shot migration tool; 30/30 entries matched and migrated to their respective closed issue files
- 30 `project/issues/closed/ISSUE-*.md` files — each received a `## Lessons Learned` section
- `project/lessons-learned.md` — deprecation header added
- `project/lessons-learned-archive.md` — deprecation header added
- `project/FEATURE_WORKFLOW.md` — Step 1 grep replaced with `python scripts/query_lessons.py`
- `docs/claude-code-setup.md` — rotation policy docs replaced with query script docs
- `project/issues/CLAUDE.md` — rationalization table step reference updated

**Commands Run:** `python scripts/migrate_lessons.py --dry-run` (30/30 matches verified), then `python scripts/migrate_lessons.py` (executed), smoke-tested query script with `scheduler` and `git staging` keywords.

**Result:** All 10 tasks complete. Migration was 100% — every hash and legacy issue number found its closed file. No always-loaded context cost going forward; lessons are per-issue and searched on demand.

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 9/9 requirements addressed (10th checkbox tracking gap fixed in close commit)
- Shortcuts found: two unused imports (`sys`, `field`) in `query_lessons.py` — fixed in final impl commit before close
- Scope drift: none
- Stragglers: `.claude/settings.local.json` has two stale allow-list entries for `lessons-learned*.md` — files still exist (deprecated), so no functional breakage; housekeeping candidate for a future pass

## Lessons Learned

### What worked well
- **Dry-run first.** The migration script's `--dry-run` flag showed 30/30 matches before a single file was touched. The zero-miss result came from testing hash-lookup logic against actual filenames before executing. Always add `--dry-run` to one-shot scripts.
- **Delete one-shot tools after use.** The user explicitly asked to delete `migrate_lessons.py` after the migration ran — keeping it would add noise with no future value. The migration is in git history if anyone needs to understand what happened.
- **Regex anchored to section headers beats line-count rotation.** The old approach needed Claude to count `## ISSUE-` occurrences and manually cut/paste entries. The new parser searches for `## Lessons Learned` sections — no counting, no text surgery, no orphaned `---` separators possible.

### What to do differently
- **Unused imports went unnoticed during writing.** `sys` and `dataclasses.field` were imported but never used in `query_lessons.py`. A quick `ruff check scripts/query_lessons.py` after writing would have caught them inline without a verifier round-trip.

### Patterns to reuse
- **OR-combined keyword scoring + date sort** is a low-complexity relevance model that works well for a corpus of 30–80 short documents. No embedding or vector search needed — hit count is a good enough proxy for "this entry is about the topic I'm working on."
- **Always-loaded context cost as the forcing function.** The 241-line always-loaded `lessons-learned.md` was the real driver. When a file is always-loaded, its size is a direct tax on every session. If a file grows unboundedly and most of its content is irrelevant per session, move it to grep-on-demand.
