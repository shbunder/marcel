# ISSUE-066: Post-065 Audit Cleanup

**Status:** Closed
**Created:** 2026-04-11
**Closed:** 2026-04-12
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
- [✓] ISSUE-066-a: Dissolve `agent/` module — move `marcelmd.py` → `harness/marcelmd.py`, `memory_extract.py` → `memory/extract.py`, update all imports
- [✓] ISSUE-066-b: Extract `MarcelDeps` behavior flags (`read_skills`, `notified`) into a `TurnState` dataclass composed into deps
- [✓] ISSUE-066-c: Split `tools/marcel.py` action implementations into sub-modules (`tools/marcel/` package) while keeping the single `marcel()` entry point
- [✓] ISSUE-066-d: Create `docs/index.md` — overview of Marcel with quick links
- [✓] ISSUE-066-e: Add `timezone` field to `docs/jobs.md` TriggerSpec table
- [✓] ISSUE-066-f: Create `docs/integration-news.md` following the banking template
- [✓] ISSUE-066-g: Add memory extraction subsection to `docs/storage.md`
- [✓] ISSUE-066-h: Add A2UI component API endpoints to `docs/architecture.md`

## Relationships
- Related to: [[ISSUE-057-post-050-deep-audit]] (previous audit)
- Follows: [[ISSUE-058-memory-learning-feedback]] through [[ISSUE-065-news-sync-integration]]

## Comments

## Implementation Log

### 2026-04-12 - LLM Implementation

**Action**: Architectural tightening and documentation cleanup from the post-065 deep audit.

**Files Modified (code):**
- `src/marcel_core/harness/marcelmd.py` — new file (moved from `agent/marcelmd.py`)
- `src/marcel_core/memory/extract.py` — new file (moved from `agent/memory_extract.py`)
- `src/marcel_core/memory/__init__.py` — re-export `extract_and_save_memories`
- `src/marcel_core/agent/` — deleted (dissolved into harness/ and memory/)
- `src/marcel_core/harness/context.py` — new `TurnState` dataclass; `MarcelDeps` now holds `turn: TurnState` instead of `read_skills` and `notified` flags directly
- `src/marcel_core/tools/integration.py` — `ctx.deps.read_skills` → `ctx.deps.turn.read_skills`
- `src/marcel_core/tools/marcel.py` — deleted
- `src/marcel_core/tools/marcel/__init__.py` — new package entry point exposing `marcel` and `send_notify`
- `src/marcel_core/tools/marcel/dispatcher.py` — new file containing just the `marcel()` entry point and match statement
- `src/marcel_core/tools/marcel/skills.py` — `read_skill` action
- `src/marcel_core/tools/marcel/memory.py` — `search_memory`, `save_memory` actions
- `src/marcel_core/tools/marcel/conversations.py` — `search_conversations`, `compact` actions
- `src/marcel_core/tools/marcel/notifications.py` — `notify` action + `send_notify` helper
- `src/marcel_core/tools/marcel/settings.py` — `list_models`, `get_model`, `set_model` actions
- `src/marcel_core/jobs/executor.py` — `deps.notified` → `deps.turn.notified`
- `src/marcel_core/api/chat.py` — import `extract_and_save_memories` from `marcel_core.memory`
- `src/marcel_core/channels/telegram/webhook.py` — import `extract_and_save_memories` from `marcel_core.memory`
- `tests/core/test_agent.py` — updated imports and `patch()` paths
- `tests/core/test_marcelmd.py` — updated imports
- `tests/tools/test_integration_tools.py` — `ctx.deps.read_skills` → `ctx.deps.turn.read_skills`

**Files Modified (docs):**
- `docs/index.md` — new landing page with quick links and core concepts
- `docs/integration-news.md` — new dedicated integration page for news.sync (follows banking template)
- `docs/jobs.md` — added `timezone` field to TriggerSpec table with IANA timezone explanation
- `docs/storage.md` — added "Memory extraction (background)" and "Memory consolidation" subsections
- `docs/architecture.md` — updated module layout (removed `agent/`, added `harness/marcelmd.py`, `memory/extract.py`, split `tools/marcel/` package); added `/api/components` endpoints; rewrote stale memory extraction description; expanded memory system types to include `feedback`
- `mkdocs.yml` — registered `integration-news.md` in Integrations nav

**Commands Run**: `uv run pytest tests/ -q` (1077 passed), `make check` (coverage 92.89%)

**Result**: Success — all 1077 tests passing at 92.89% coverage; no behavior changes; architecture is cleaner and docs are complete.

**Reflection**:
- **Coverage**: 8/8 tasks addressed. All three architectural fixes landed as planned, and all five documentation gaps are closed with content that matches the actual code state.
- **Shortcuts found**: None. The only `except Exception` in the diff was pre-existing in `notify()` (moved verbatim from the original marcel.py) and is narrow + logs specifically.
- **Scope drift**: Mild — also updated the stale memory extraction description in `architecture.md` (which described the pre-ISSUE-049 `claude_code`-subprocess approach). This was a correctness fix that belonged naturally in the same change since I was already touching the surrounding module layout. Otherwise stayed in scope.

## Lessons Learned

### What worked well
- Running 5 parallel Explore sub-agents (architecture, tests, dead code, philosophy, docs) from a single audit prompt gave a complete picture in one round. Each agent stayed focused because its brief was narrow and self-contained — no cross-contamination, no duplicated reads.
- Splitting a large god-tool (`tools/marcel.py`) into a package with the dispatcher in one file and each action group in its own module kept the single-tool-to-the-LLM contract intact while fixing the maintainability problem. The `__init__.py` re-exports mean all existing imports (`from marcel_core.tools.marcel import marcel`) continue to work untouched.
- Extracting `TurnState` as a composed field on `MarcelDeps` (not inheritance, not a separate context parameter) meant tools only changed one line each (`deps.notified` → `deps.turn.notified`) and pydantic-ai's `deps_type` contract was unaffected.
- Writing the issue with all 8 tasks declared up front, then working them top-to-bottom, kept the commit sequence clean: one `📝 created`, two `🔧 impl` (code + linter fixup), one `✅ closed` (docs + issue move).

### What to do differently
- The docs site was already broken before this issue (`docs/index.md` missing from mkdocs.yml for weeks). Earlier audits should have run `mkdocs build --strict` as a sanity check — missing nav files are the kind of bug that only surfaces when someone actually views the site.
- Two documentation pages (architecture.md's memory extraction section, jobs.md's TriggerSpec table) had been stale since ISSUE-049 and ISSUE-064 respectively. The feature development procedure says "Update all affected doc pages in the same change as the code" — neither issue's closing commit caught the downstream doc reference. A grep for the changed module/field name across `docs/` at close time would have caught both.
- The `agent/` folder was named in ISSUE-033 (`marcel-md-system`) when it only held `marcelmd.py`, then it accreted `memory_extract.py` in ISSUE-049 without anyone noticing the name no longer fit. Module names should be revisited whenever a second file is added — if the name doesn't describe both, it probably shouldn't be the home for either.

### Patterns to reuse
- **Parallel audit pattern**: for any "deep audit / review since X" request, launch 4–6 focused Explore sub-agents in a single batch (architecture, tests, dead code, philosophy, docs, and optionally security). Each agent gets a self-contained brief with category-specific questions. Results come back in a few minutes and compile into a comprehensive report without polluting the main conversation with tool-call noise.
- **Composed state pattern**: when a dependency container starts accumulating mutable flags (`read_skills`, `notified`, `counter`, etc.), extract them into a `TurnState` / `RunState` dataclass composed as a field on the deps. Keeps the dep container immutable identity/config and collects all per-run state in one obvious place. Tools touch `deps.turn.x` instead of `deps.x`.
- **Package with dispatcher pattern**: when a single-file tool's action implementations grow past ~300 lines, convert the file into a package: `tool/__init__.py` re-exports the public entry point, `tool/dispatcher.py` holds the match/switch, and each action group lives in its own sibling module. Import paths stay stable thanks to `__init__.py` re-exports.
- **Doc-close verification grep**: before any closing commit, run `grep -r "<renamed function>" docs/ | grep -v closed_issue` to catch docs referencing the old name. Stale docs are worse than missing docs.
