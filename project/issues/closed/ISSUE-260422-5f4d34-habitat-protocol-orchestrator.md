# ISSUE-5f4d34: Habitat Protocol + unified discovery orchestrator (Phase 1.5 of 3c1534)

**Status:** Closed
**Created:** 2026-04-22
**Assignee:** Unassigned
**Priority:** Low
**Labels:** refactor, plugin-system, cleanup

## Capture

**Follow-up to [[ISSUE-3c1534]] Phase 1.** The kernel-side rename shipped (Phases 1.1-1.6) but the `Habitat` Protocol + unified orchestrator was deferred because it's an abstraction layer that doesn't affect the rename's critical path. This issue captures the deferred work.

User had authorised this as decision #4 in the refactor plan: *"yes [common Habitat Protocol]"*.

**Resolved intent:** Introduce a `Habitat` Protocol in `src/marcel_core/plugin/habitat.py` with five concrete implementations (`ToolkitHabitat`, `SkillHabitat`, `SubagentHabitat`, `ChannelHabitat`, `JobHabitat`). Add a `discover_all_habitats(zoo_dir)` function in `src/marcel_core/plugin/orchestrator.py` that loops over all five kinds with uniform logging, validation, and error containment. Wire the orchestrator into `lifespan()` (replaces the current per-kind discovery calls). Update `_log_zoo_summary` in `main.py` to read from the orchestrator's discovered list instead of re-walking the filesystem.

## Description

### What's being deferred (from ISSUE-3c1534 Phase 1.5)

Per the ISSUE-3c1534 plan, Phase 1.5 was one of six Phase 1 sub-steps:

- Define `Habitat` Protocol in `src/marcel_core/plugin/habitat.py`
- Implement `ToolkitHabitat`, `SkillHabitat`, `SubagentHabitat`, `ChannelHabitat`, `JobHabitat` wrappers
- Implement `discover_all_habitats` in `src/marcel_core/plugin/orchestrator.py`
- Wire orchestrator into `lifespan()` (replaces current per-kind discovery calls)
- Update `_log_zoo_summary` in `main.py` to read from orchestrator

### Why deferred

The Protocol is an *organizational cleanup*, not a behaviour change. Today each habitat kind has its own discovery function (`toolkit.discover`, `channels.discover`, etc.) and each works correctly. The Protocol unifies the surface for orchestration and logging, but the per-kind loaders are not going away — they become the implementations behind the Protocol's `.discover_all()` classmethod.

Shipping Phase 1 without this abstraction meant one fewer abstraction layer to coordinate against the vocabulary renames. The abstraction becomes easier to land now that names are stable.

### Scope

1. `src/marcel_core/plugin/habitat.py` — Protocol definition (~40 lines) matching the sketch in ISSUE-3c1534.
2. Wrapper classes in their respective modules, each implementing the Protocol:
   - `ToolkitHabitat` — in `src/marcel_core/toolkit/habitat.py` or inline in `__init__.py`
   - `SkillHabitat` — in `src/marcel_core/skills/loader.py`
   - `SubagentHabitat` — in `src/marcel_core/agents/loader.py`
   - `ChannelHabitat` — in `src/marcel_core/plugin/channels.py`
   - `JobHabitat` — in `src/marcel_core/plugin/jobs.py`
3. `src/marcel_core/plugin/orchestrator.py` — `discover_all_habitats(zoo_dir)` function
4. Lifespan rewrite in `main.py` — replace the current sequence of `discover_channels()` + `discover_integrations()` + scheduler jobs-load with a single orchestrator call
5. `_log_zoo_summary` — read from orchestrator's discovered list (no double filesystem walk)
6. `tests/core/test_habitat_protocol.py` — compliance tests per kind + ordering tests
7. Regression: the existing `test_lifespan_runs_discover_before_scheduler_start` from ISSUE-efbaaa must still pass (orchestrator preserves the "toolkit + channels before scheduler.start" ordering)

### Risks

Low. The Protocol is additive — existing per-kind loaders continue to work, the Protocol wraps them. Lifespan rewrite is the riskiest bit; the ISSUE-efbaaa ordering test gates merge.

## Implementation Approach

### Shape — additive Protocol over existing loaders

The five kinds have different signatures today: two `discover()` functions with side effects (toolkit, channels), two pure "load all" helpers that return lists (`load_agents()`, `discover_templates()`), and one per-user filter (`load_skills(user_slug)`). The Protocol unifies the *orchestration surface* — one `discover_all(zoo_dir)` entry point per kind — while each wrapper internally dispatches to the existing loader. Nothing about the per-kind logic moves.

### Files to add / modify

- `src/marcel_core/plugin/habitat.py` — new. Defines `Habitat` Protocol (`kind: str`, `name: str`, `source: str`) + five concrete wrappers (`ToolkitHabitat`, `SkillHabitat`, `SubagentHabitat`, `ChannelHabitat`, `JobHabitat`) as `@dataclass(frozen=True)` types with a `@classmethod discover_all(cls, zoo_dir) -> list[Self]`. Wrapper bodies delegate to existing loaders; each absorbs its kind's shape into the uniform surface.
- `src/marcel_core/plugin/orchestrator.py` — new. `discover_all_habitats(zoo_dir) -> dict[str, list[Habitat]]` calls each wrapper's `discover_all` in a fixed order (`toolkit`, `channels`, `jobs`, `agents`, `skills`), wraps each call in a try/except so a broken kind cannot poison the others, and logs one info line per kind with the count.
- `src/marcel_core/main.py` — `lifespan()` replaces the explicit `discover_integrations()` call (line 146) with `discover_all_habitats(settings.zoo_dir)`. Call ordering invariant (orchestrator finishes **before** `scheduler.start()`) is preserved — `_ensure_habitat_jobs()` → `_metadata` dependency doesn't change.
- `tests/core/test_habitat_protocol.py` — new. Per-kind compliance tests (Protocol members present, `discover_all` returns the right shape), orchestrator ordering test, and a "broken kind fails isolated" test.

### Existing code the wrappers wrap — unchanged

- Toolkit — `marcel_core.toolkit.discover` at `src/marcel_core/toolkit/__init__.py:218`; post-call state in `_metadata` dict. `ToolkitHabitat.discover_all` calls `discover()` then reads `_metadata.items()`.
- Channels — `marcel_core.plugin.channels.discover` at `src/marcel_core/plugin/channels.py:190`; post-call state via `list_channels()` + `get_channel(name)` at `:166`. Discovery is already idempotent (sys.modules check), so calling it again from the orchestrator when module-load already called it is safe.
- Skills — `marcel_core.skills.loader.load_skills(user_slug)` at `src/marcel_core/skills/loader.py:365` is per-user; discovery needs to be user-independent, so `SkillHabitat.discover_all` walks `<zoo>/skills/` directly (same logic as `_log_zoo_summary` uses) and returns one Habitat per on-disk directory. Per-user requirement filtering stays a separate concern and is unchanged.
- Subagents — `marcel_core.agents.loader.load_agents()` at `src/marcel_core/agents/loader.py:187`. Already returns `list[AgentDoc]`; `SubagentHabitat.discover_all` maps over the returned list.
- Jobs — `marcel_core.plugin.jobs.discover_templates()` at `src/marcel_core/plugin/jobs.py:62`. Returns `dict[str, dict[str, Any]]`; `JobHabitat.discover_all` flattens the dict into a list.

### `_log_zoo_summary` — keep as-is, NOT switching to the orchestrator

The issue body's line *"Update `_log_zoo_summary` to read from the orchestrator's discovered list"* conflicts with the function's explicit design (see its docstring at `src/marcel_core/main.py:91-98` + [[ISSUE-792e8e]]): counts are **on-disk directory counts**, not post-discovery registrations, so the first-boot diagnostic stays truthful even when a habitat fails to load. Reading from the orchestrator would mask that failure mode. Flagged here as a deliberate deviation; captured in Lessons Learned. Task list below reflects the deviation (one task dropped).

### Risks + mitigations

- **Lifespan ordering regression** — the existing `test_lifespan_runs_discover_before_scheduler_start` at `tests/core/test_main_lifespan.py:21` patches `marcel_core.toolkit.discover`. Because the orchestrator calls through to the real `toolkit.discover`, the patch still intercepts and the order assertion still holds. Verified by running that test with the new code.
- **Channel module-load order** — `discover_channels()` is called at `main.py:182` (module-load time, before `lifespan`) so the router-mount loop at `:189` can see registered plugins. Lifespan's orchestrator will also discover channels — this is a second call, absorbed by `discover()`'s sys.modules idempotency check. Both lifecycles remain correct.

### Verification steps

- New unit tests: `uv run pytest tests/core/test_habitat_protocol.py -v` — all green.
- Regression gate: `uv run pytest tests/core/test_main_lifespan.py::test_lifespan_runs_discover_before_scheduler_start -v` — must still pass.
- Full suite: `make check` — green at ≥90 % coverage (1411+ baseline).
- Manual: `make serve` boots to `main: zoo at … —  channels=1 …` without any new warnings.

### Non-scope

- Converting `_log_zoo_summary` to read from orchestrator (see deviation above).
- Cleaning up the module-level `discover_channels()` call at `main.py:182` — moving it would require refactoring the router-mount loop (lifespan runs after router mount). Out of this issue's scope.
- Refactoring the per-kind loaders themselves — the Protocol is additive; kind internals stay.

## Tasks

- [✓] Define `Habitat` Protocol in `src/marcel_core/plugin/habitat.py`
- [✓] Implement `ToolkitHabitat`, `ChannelHabitat`, `SkillHabitat`, `SubagentHabitat`, `JobHabitat` wrappers
- [✓] Implement `discover_all_habitats` in `src/marcel_core/plugin/orchestrator.py`
- [✓] Replace `lifespan()`'s `discover_integrations()` call with the orchestrator
- [✓] Add `tests/core/test_habitat_protocol.py` (per-kind compliance + ordering + isolated-failure)
- [✓] Verify `test_lifespan_runs_discover_before_scheduler_start` still passes
- [✓] `make check` green
- [ ] `/finish-issue` → merged close commit on main

## Relationships

- Follows: [[ISSUE-3c1534]] (five-habitat taxonomy — Phase 1 shipped)
- Independent of: [[ISSUE-ea6d47]] (Phase 2 jobs dispatch_type — shipped), [[ISSUE-d7eeb1]] (Phase 3 zoo rename — shipped), [[ISSUE-71e905]] (Phase 4 docs)

## Implementation Log
<!-- issue-task:log-append -->

### 2026-04-23 14:43 - LLM Implementation
**Action**: Habitat Protocol + discover_all_habitats orchestrator landed. Five frozen-dataclass wrappers (ToolkitHabitat, ChannelHabitat, SkillHabitat, SubagentHabitat, JobHabitat) over existing loaders; fixed-order orchestrator with per-kind failure isolation. Lifespan swaps toolkit.discover() for discover_all_habitats(). _log_zoo_summary deliberately unchanged — its ISSUE-792e8e first-boot diagnostic needs on-disk counts, not post-discovery registrations. 17 new tests + existing ordering regression (test_lifespan_runs_discover_before_scheduler_start) still green. make check: 1428 tests, 90.51% coverage.
**Files Modified**:
- `src/marcel_core/plugin/habitat.py`
- `src/marcel_core/plugin/orchestrator.py`
- `src/marcel_core/main.py`
- `tests/core/test_habitat_protocol.py`
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned

### What worked well
- **Lazy imports inside `discover_all`.** `ToolkitHabitat.discover_all` does `from marcel_core.toolkit import _metadata, discover` inside the classmethod body rather than at module load. This is what makes `patch('marcel_core.toolkit.discover')` keep working in the `test_lifespan_runs_discover_before_scheduler_start` regression gate — the patch replaces the name in `marcel_core.toolkit` before the local-import binding resolves at call time. Pattern worth reusing for any future wrapper over a patch-targeted symbol.
- **Filesystem walk over `load_skills(user_slug)` for `SkillHabitat`.** Forcing a user slug into startup discovery would have been the wrong coupling. Keeping skill discovery user-independent and leaving per-user requirement filtering in `load_skills` preserved the existing contract and avoided a cross-cutting refactor this issue isn't about.
- **Documenting the `_log_zoo_summary` deviation up front.** The issue body asked for one thing; the function's design intent (ISSUE-792e8e: on-disk counts for first-boot diagnostics) conflicted with it. Calling that out in the Implementation Approach before writing any code turned a "did you miss this?" finding into a "considered and declined" decision — the pre-close-verifier recognised it as documented, not drift.

### What to do differently
- **Per-kind failure isolation's `except Exception` is broad.** The orchestrator catches *anything* a wrapper raises and substitutes `[]`. That's correct for the "broken habitat shouldn't take down siblings" goal but it also swallows programming errors. `log.exception(...)` emits a stack trace, but a dev reading kernel logs might miss it during a noisy startup. Worth a follow-up: surface orchestrator failures in `_log_zoo_summary`'s output so a broken kind shows up visibly next to the healthy counts.
- **The `Habitat` Protocol is minimal — maybe too minimal.** `kind`, `name`, `source` is enough for the orchestrator and for logging. Real consumers (a future admin UI, or Phase 5's alias-removal gate) will want more: handler lists for toolkits, capability bits for channels, frontmatter for skills/subagents. Those live on the concrete wrappers as extra fields, not on the Protocol. First caller to need "uniform access to provides/capabilities across kinds" will need to extend the Protocol or introduce a second layer — plan accordingly.

### Patterns to reuse
- **Additive abstraction — wrapper delegates, native loader unchanged.** Five kinds, five wrappers, zero changes to the existing loaders. The refactor is trivially reversible and the blast radius is two new files + one lifespan-function change.
- **Orchestrator tests monkeypatch `marcel_core.plugin.orchestrator.KindHabitat.discover_all`**, not the kind's native loader. The orchestrator resolves class references through its own namespace via the `_KINDS` tuple, so stubbing at the orchestrator layer is what exercises the dispatch path. Stubbing the native loader would miss the orchestrator entirely.

### Reflection (via pre-close-verifier)

- **Verdict:** APPROVE — 7/7 requirements addressed.
- **Additive abstraction verified:** zero changes under `src/marcel_core/toolkit/`, `src/marcel_core/agents/`, `src/marcel_core/skills/`, `plugin/channels.py`, `plugin/jobs.py`. Native loaders untouched; wrappers delegate only.
- **Lifespan ordering gate passes** — the lazy-import-inside-`discover_all` pattern is the reason the existing mock-based test still intercepts.
- **Shortcuts found:** none. The only `except Exception` is the orchestrator's per-kind isolation, which uses `log.exception(...)` + returns `[]` — that's the feature, not a shortcut.
- **Scope drift:** none. `_log_zoo_summary` deviation is documented, not silent.
- **Stragglers:** none. Grep across `docs/`, `.claude/`, `~/.marcel/`, `CLAUDE.md` turned up zero references to the old or new names — docs land in ISSUE-71e905 (Phase 4 rewrite).
