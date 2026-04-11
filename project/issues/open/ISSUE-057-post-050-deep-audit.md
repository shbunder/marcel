# ISSUE-057: Post-050 Deep Audit — Dead Code, Docs, Dependencies

**Status:** Open
**Created:** 2026-04-11
**Assignee:** Claude
**Priority:** Medium
**Labels:** docs, cleanup, quality

## Capture
**Original request:** "there have been a lot of impactfull changes to marcel since issue-050. Can you run a deep audit of Marcel since these changes: check if architectually everything is sound, check if everything is still consistent, check if test focus on testing functionalities through scenarios (and are not just patches trying to increase coverage), check if there is no dead code, and that the code library is still structured naturally. The code should be easy to navigate, check if the code-base is still fullfilling the phylosfy of Marcel, lastly ensure that all documenation is in order, it should be very clear for somebody seeing this repo for the first time how Marcel works, and how they should set it up."

**Follow-up Q&A:** User confirmed to work on all findings, including the pydantic-ai dependency issue.

**Resolved intent:** Comprehensive audit of the Marcel codebase after the rapid ISSUE-050 through ISSUE-056 development sprint. The goal is to ensure architectural soundness, remove dead code left behind by migrations (compactor, old session model), fix documentation that fell out of date with the continuous conversation model and artifact system, clean up redundant dependencies, consolidate granular tests, and close stale WIP issues — leaving the codebase navigable and well-documented for newcomers.

## Description
Five parallel deep-dive agents audited architecture, test quality, dead code, documentation, and philosophy alignment. Architecture was found to be sound. The main findings were:

1. **Dead code**: The `compactor.py` module and ~15 functions in `history.py` were entirely dead after ISSUE-051 replaced the session model with continuous conversations.
2. **Stale docs**: `NUC_QUICKSTART.md`, `MIGRATION_PLAN.md` referenced old migration phases. `architecture.md` still described the old SessionManager. `telegram.md` had wrong inactivity threshold and stale import paths. `storage.md` didn't document the new segment-based conversation model. Artifacts (ISSUE-050) had no documentation at all.
3. **Redundant dependency**: Both `pydantic-ai` and `pydantic-ai-slim[anthropic,openai]` in deps — only the slim variant is needed. Removing it revealed a missing explicit `PyJWT` dependency used by banking.
4. **Stale WIP**: ISSUE-031 (migrate to pydantic-ai) was fully superseded by ISSUE-049.
5. **Test granularity**: Escape character tests in `test_telegram.py` were unnecessarily split into 7 individual tests.

## Tasks
- [✓] Delete dead `compactor.py` module and its tests
- [✓] Remove dead functions from `history.py` (15 functions only used by compactor or nothing)
- [✓] Update `test_history.py` to remove tests for deleted functions
- [✓] Close ISSUE-031 (superseded by ISSUE-049)
- [✓] Remove redundant `pydantic-ai` dep, keep only `pydantic-ai-slim[anthropic,openai]`
- [✓] Add explicit `PyJWT` dependency (was transitive via pydantic-ai full)
- [✓] Rewrite `docs/architecture.md` for continuous conversation model, artifacts, observability
- [✓] Delete stale `NUC_QUICKSTART.md` and `MIGRATION_PLAN.md`
- [✓] Fix `docs/channels/telegram.md` — inactivity threshold (6h→1h), import paths, flow diagram
- [✓] Update `docs/storage.md` — add segment-based conversation format, update data layout
- [✓] Create `docs/artifacts.md` and register in `mkdocs.yml`
- [✓] Document `rss_fetch` tool in `news/SKILL.md`
- [✓] Document tracing config vars in `SETUP.md`
- [✓] Consolidate granular escape tests in `test_telegram.py` (7→3 tests)
- [✓] Verify `make check` passes (672 tests, 0 pyright errors)

## Relationships
- Related to: [[ISSUE-049-migrate-v2-harness]], [[ISSUE-050-artifact-mini-app]], [[ISSUE-051-continuous-conversation-model]], [[ISSUE-052-rich-content-delivery]], [[ISSUE-053-centralize-config-data-root]], [[ISSUE-054-phoenix-llm-tracing]], [[ISSUE-055-system-prompt-optimization]], [[ISSUE-056-rss-browser-tools-news-scraper]]

## Implementation Log
### 2026-04-11 - LLM Implementation
**Action**: Full audit and cleanup
**Files Modified**:
- `src/marcel_core/memory/compactor.py` — deleted (dead module)
- `src/marcel_core/memory/history.py` — removed 15 dead functions, updated docstring
- `tests/memory/test_compactor.py` — deleted
- `tests/memory/test_history.py` — removed tests for deleted functions
- `tests/core/test_telegram.py` — consolidated escape tests (7→3)
- `pyproject.toml` — removed `pydantic-ai`, added `PyJWT`
- `project/issues/wip/ISSUE-031-*` → `project/issues/closed/` (superseded)
- `docs/architecture.md` — complete rewrite
- `docs/storage.md` — added continuous conversation section, updated layout
- `docs/artifacts.md` — created (new)
- `docs/channels/telegram.md` — fixed threshold, import paths, flow diagram
- `mkdocs.yml` — added artifacts page
- `SETUP.md` — added missing config vars
- `src/marcel_core/defaults/skills/news/SKILL.md` — documented rss_fetch
- `NUC_QUICKSTART.md` — deleted (stale)
- `MIGRATION_PLAN.md` — deleted (stale)
**Commands Run**: `make check`
**Result**: Success — 672 tests passing, 0 pyright errors, all formatting/linting clean
