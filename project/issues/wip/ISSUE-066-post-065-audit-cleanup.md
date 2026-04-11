# ISSUE-066: Post-065 Audit Cleanup

**Status:** WIP
**Created:** 2026-04-11
**Assignee:** Marcel (self)
**Priority:** Medium
**Labels:** refactor, docs

## Capture
**Original request:** Post-065 audit cleanup — architecture tightening + documentation gaps

**Follow-up Q&A:**
- User confirmed the marcel() god-action should stay (fewer tools for the LLM is the goal), but the implementation should be modularized into sub-modules.
- User confirmed the agent/ module is misnamed and needs revision.
- User confirmed MarcelDeps accumulating behavior flags is an issue.

**Resolved intent:** Clean up architectural rough edges and documentation gaps identified during the post-ISSUE-058 deep audit. Three code changes: dissolve the `agent/` module (move `marcelmd.py` to `harness/`, `memory_extract.py` to `memory/`), extract `MarcelDeps` behavior flags into a `TurnState` dataclass, and split `marcel()` tool action implementations into sub-modules. Six documentation fixes: create missing `docs/index.md`, add timezone field to jobs docs, create news integration page, document memory extraction in storage docs, add A2UI component API endpoints to architecture docs.

## Description

The deep audit covering issues 058–065 found the codebase in good shape overall — architecture is sound, tests are genuine scenario-based tests, no dead code, philosophy upheld. However, several areas need tightening:

**Architecture:**
1. The `agent/` module contains `marcelmd.py` (a context builder) and `memory_extract.py` (a background task) — neither is core agent logic. Move them to where they belong.
2. `MarcelDeps` has accumulated stateful behavior flags (`read_skills`, `notified`) that don't belong in a dependency container. Extract them into a separate `TurnState` class.
3. The `marcel()` tool action implementations are all in one 342-line file. The dispatch pattern is correct (keep one tool for the LLM), but the implementations should be split into sub-modules for maintainability.

**Documentation:**
4. `docs/index.md` is referenced in mkdocs.yml but doesn't exist — site won't render.
5. `docs/jobs.md` TriggerSpec table omits the `timezone` field added in ISSUE-064.
6. No `docs/integration-news.md` — banking has a dedicated page, news should too.
7. Memory extraction pipeline (Haiku post-turn extraction, `save_memory` action, consolidation) is underexplained in docs.
8. A2UI component API endpoints (`/api/components`, `/api/components/{name}`) not documented in architecture.md.

## Tasks
- [ ] ISSUE-066-a: Dissolve `agent/` module — move `marcelmd.py` → `harness/marcelmd.py`, `memory_extract.py` → `memory/extract.py`, update all imports
- [ ] ISSUE-066-b: Extract `MarcelDeps` behavior flags (`read_skills`, `notified`) into a `TurnState` dataclass composed into deps
- [ ] ISSUE-066-c: Split `tools/marcel.py` action implementations into sub-modules (`tools/marcel/` package) while keeping the single `marcel()` entry point
- [ ] ISSUE-066-d: Create `docs/index.md` — overview of Marcel with quick links
- [ ] ISSUE-066-e: Add `timezone` field to `docs/jobs.md` TriggerSpec table
- [ ] ISSUE-066-f: Create `docs/integration-news.md` following the banking template
- [ ] ISSUE-066-g: Add memory extraction subsection to `docs/storage.md`
- [ ] ISSUE-066-h: Add A2UI component API endpoints to `docs/architecture.md`

## Relationships
- Related to: [[ISSUE-057-post-050-deep-audit]] (previous audit)
- Follows: [[ISSUE-058-memory-learning-feedback]] through [[ISSUE-065-news-sync-integration]]

## Comments

## Implementation Log
