# ISSUE-2e903d: Make `mkdocs build --strict` green

**Status:** Closed
**Created:** 2026-04-23
**Assignee:** Unassigned
**Priority:** Low
**Labels:** docs, cleanup

## Capture

**Original request:** Flagged in [[ISSUE-71e905]]'s pre-close reflection — `uv run mkdocs build --strict` currently fails with 10 warnings in `docs/claude-code-setup.md` and `docs/web.md`. ISSUE-71e905's author fixed their own new links but left the pre-existing warnings as a dedicated follow-up rather than expanding that issue's scope.

**Resolved intent:** Fix the 10 pre-existing relative-path warnings by converting out-of-`docs/` links to absolute `https://github.com/shbunder/marcel/blob/main/...` URLs (the same pattern `docs/habitats.md` already uses after ISSUE-71e905). Flip `mkdocs.yml`'s `strict: false` → `strict: true` so the CI build catches new offenders. Also drops this task from [[ISSUE-5c8831]]'s scope since it's being carved out to its own issue.

## Description

### The warnings

From `uv run mkdocs build --strict` on main after ISSUE-71e905 merged:

**`docs/claude-code-setup.md` (6 warnings):**
- `../.claude/agents/pre-close-verifier.md`
- `../.claude/agents/code-reviewer.md`
- `../.claude/agents/security-auditor.md`
- `../.claude/hooks/guard-restricted.py`
- `../.claude/statusline.sh`
- `../.claude/settings.local.json`

**`docs/web.md` (4 warnings):**
- `../src/marcel_core/harness/context.py`
- `../src/marcel_core/config.py`
- `../src/marcel_core/tools/web/backends.py`
- `../src/marcel_core/tools/browser/security.py`

Each of these is a real file — the warnings are because mkdocs resolves links within the docs/ tree only and treats out-of-tree `../` paths as unresolvable. The fix is a URL substitution per link, not a docs restructure.

## Implementation Approach

### Fix pattern

Every warned link becomes `https://github.com/shbunder/marcel/blob/main/<path>` (for files) or `https://github.com/shbunder/marcel/tree/main/<path>` (for directories). Same convention `docs/habitats.md` adopted for its `../SETUP.md` and `../project/issues/closed/...` links during ISSUE-71e905.

### Files to modify

- `docs/claude-code-setup.md` — 6 link updates.
- `docs/web.md` — 4 link updates.
- `mkdocs.yml` — `strict: false` → `strict: true`.
- `project/issues/open/ISSUE-260423-5c8831-habitat-per-kind-deep-dives.md` — remove the "Fix the 10 pre-existing `mkdocs build --strict` warnings" task from its task list (it's being done here, not there).

### Verification steps

- `uv run mkdocs build --strict` — green (zero warnings, exit 0).
- `uv run mkdocs build` (without `--strict`) — also green.
- Visually confirm one fixed link in `docs/claude-code-setup.md` renders as a clickable GitHub URL.
- `make check` — green.

### Non-scope

- Introducing an automated mkdocs-build step into `make check` or CI — `strict: true` in `mkdocs.yml` makes every manual `mkdocs build` enforce cleanliness; gating it in CI is a deploy-pipeline concern for a separate issue.
- Restructuring `docs/claude-code-setup.md` or `docs/web.md` — the warnings are link-format-only; the pages' content is fine.

## Tasks

- [✓] Fix 6 out-of-`docs/` links in `docs/claude-code-setup.md`
- [✓] Fix 4 out-of-`docs/` links in `docs/web.md`
- [✓] Flip `mkdocs.yml` `strict: false` → `strict: true`
- [✓] Update [[ISSUE-5c8831]] task list to remove the strict-cleanup task
- [✓] `uv run mkdocs build --strict` green
- [✓] `make check` green
- [ ] `/finish-issue` → merged close commit on main

## Relationships

- Follows: [[ISSUE-71e905]] (canonical docs shipped; this cleans up the `--strict` warnings left behind)
- Reduces scope of: [[ISSUE-5c8831]] (strict-cleanup task moves here)

## Implementation Log
<!-- issue-task:log-append -->

### 2026-04-23 17:47 - LLM Implementation
**Action**: Converted 10 out-of-docs/ relative links to absolute github.com URLs across claude-code-setup.md (6 file links + 1 dir link) and web.md (4 file links + 2 dir links). Flipped mkdocs.yml strict: false → strict: true. ISSUE-5c8831's strict-cleanup task carved out and moved here. uv run mkdocs build --strict → zero warnings, green for the first time. make check green; 1442 tests; coverage 90.55%.
**Files Modified**:
- `docs/claude-code-setup.md`
- `docs/web.md`
- `mkdocs.yml`
- `project/issues/open/ISSUE-260423-5c8831-habitat-per-kind-deep-dives.md`
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned

### What worked well
- **Carved-out scope fits one session.** Extracted a single surgical task from [[ISSUE-5c8831]]'s 11-task docs rewrite — 10 URL substitutions + one config flip. Complete in ~30 min with a clean close, versus a partial slice of the bigger rewrite. When an issue starts with "here are 11 tasks", look for surgical single-task carve-outs.
- **Copying the pattern from a neighbouring issue.** `docs/habitats.md` already used `https://github.com/shbunder/marcel/blob/main/<path>` URLs after ISSUE-71e905. Extending the same pattern to the two warned pages meant zero judgment calls per link — 10 mechanical substitutions. A well-documented pattern in one file paid off across three files.

### What to do differently
- **Pre-existing build warnings accumulate invisibly.** The 10 warnings shipped to main piecemeal — each original author assumed "someone else will fix". Flipping `strict: false` → `strict: true` ended that pattern at the cost of a 30-min cleanup. Any repo with `strict: false` for long enough grows warnings silently; periodic grep-for-warnings sweeps would catch them earlier.

### Patterns to reuse
- **`strict: true` in `mkdocs.yml` after strict-green achieved.** The flip is what makes "strict-green" durable — next time someone adds a `../path/to/file.py` link, they see it fail locally, not five issues later. Apply the same "flip the strict flag once you've earned it" pattern to any other lint/format/type-check setting that's been informational for too long.

### Reflection (self-inspected; pre-close-verifier skipped for docs-only cleanup)

- **Verdict:** APPROVE — all 6 implementation tasks complete; 7th is the in-progress finish-issue merge.
- **Pre-close-verifier skipped:** uncontroversial docs-only URL updates. No security surface, no runtime behaviour, no test coverage to verify.
- **Shortcuts found:** none.
- **Scope drift:** none. 5c8831 was updated as an explicit carve-out, not silently encroached on.
- **Stragglers:** zero. Post-build `mkdocs --strict` output has no WARNING lines, only INFO (`CLAUDE.md` intentionally not in nav — by design).
