# ISSUE-5f4d34: Habitat Protocol + unified discovery orchestrator (Phase 1.5 of 3c1534)

**Status:** Open
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

## Tasks

- [ ] Define `Habitat` Protocol
- [ ] Implement 5 kind-specific wrapper classes
- [ ] Implement `discover_all_habitats` orchestrator
- [ ] Replace `lifespan()`'s per-kind discovery calls with one orchestrator invocation
- [ ] Update `_log_zoo_summary` to read from orchestrator
- [ ] Add `test_habitat_protocol.py` (per-kind compliance + ordering)
- [ ] Verify `test_lifespan_runs_discover_before_scheduler_start` still passes
- [ ] `make check` green
- [ ] `/finish-issue` → merged close commit on main

## Relationships

- Follows: [[ISSUE-3c1534]] (five-habitat taxonomy — Phase 1 shipped)
- Independent of: [[ISSUE-ea6d47]] (Phase 2 jobs trigger_type), [[ISSUE-d7eeb1]] (Phase 3 zoo rename), [[ISSUE-71e905]] (Phase 4 docs)
