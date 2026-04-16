# ISSUE-e6af2d: Skill Pattern Improvements (pydantic-ai-skills Lessons)

**Status:** Closed
**Created:** 2026-04-16
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature, refactor, skills

## Capture
**Original request:** Skill pattern improvements inspired by pydantic-ai-skills: (1) read_skill_resource action on marcel tool, (2) SkillConfig typed dataclass, (3) skill name validation, (4) auto-reload on request startup. Items 5 (first-class shell scripts with timeout/shebang/arg-to-flag) and 6 (multi-source registry composition) are deferred for future investigation.

**Follow-up Q&A:** None.

**Resolved intent:** A cross-repo analysis of Marcel's skill pattern against the pydantic-ai-skills framework surfaced four concrete improvements worth making now. None of them change the architecture or touch Marcel's distinctive strengths (multi-user isolation, SETUP.md fallback, compact system-prompt index). They improve robustness and maintainability: a typed config layer so the registry stops passing bare dicts around; explicit name validation so misconfigured skills fail at load time rather than mysteriously at call time; auto-reload of skill docs so a dropped-in SKILL.md is picked up without a server restart; and a `read_skill_resource` action on the `marcel` tool that formalizes the existing convention (feeds.yaml, components.yaml, SETUP.md) into a uniform fetch interface. Two larger ideas (first-class shell scripts, multi-source registry composition) are left in the description for future consideration.

## Description

After comparing Marcel's skill system with [pydantic-ai-skills](https://github.com/pydantic/pydantic-ai-skills), four improvements were selected for implementation and two deferred:

### In scope

**1. `read_skill_resource(skill, resource)` action on the `marcel` tool**
Skills already expose named sub-documents (feeds.yaml, components.yaml, SETUP.md) but there is no uniform way for the agent to fetch them. The agent must know each resource's file-system convention. A `read_skill_resource` action on the `marcel` tool would let any skill expose named resources through a single interface: `marcel(action="read_skill_resource", skill="news", resource="feeds")`.

**2. `SkillConfig` typed dataclass in the registry**
`skills/registry.py` builds and returns `dict[str, dict]` for skill configs. Replacing the inner dict with a typed `SkillConfig` dataclass improves IDE support, removes silent key-typo bugs, and makes the registry's surface self-documenting.

**3. Skill name validation in `register()` and `_load()`**
Marcel's `family.action` dotted naming convention is implicit. Invalid names (wrong format, missing dot, reserved words) are only discovered at call time. Adding validation in `integrations/__init__.py` (`register()`) and `skills/registry.py` (`_load()`) surfaces misconfiguration at startup.

**4. Auto-reload of skill docs on request startup**
Marcel has `registry.reload()` but it is never called automatically. A SKILL.md file dropped into `~/.marcel/skills/` is not picked up until the server restarts. Hooking `reload()` into the per-request or per-agent-run path (behind a staleness check) eliminates this friction.

### Deferred — investigate later

**5. First-class shell skills** — `LocalSkillScriptExecutor` pattern from pydantic-ai-skills (subprocess execution with timeout, shebang detection, arg-to-flag conversion). Marcel's Python handlers cover complex logic adequately today; promoting shell skills requires a concrete use case first.

**6. Multi-source registry composition** — `GitSkillsRegistry`, `FilteredRegistry`, `PrefixedRegistry`, `CombinedRegistry`. Only relevant if Marcel adds a shared/family skill library. No driver for this now.

## Tasks

- [✓] Add `read_skill_resource(skill, resource)` action to `marcel` tool (`tools/marcel/skills.py`)
  - [✓] Update `skills/loader.py` to expose named resources per skill (SETUP.md, components.yaml, feeds.yaml, any extra .md/.yaml files)
  - [✓] Update `SKILL.md` format docs / `docs/skills.md` to document the new action
- [✓] Introduce `SkillConfig` dataclass in `skills/registry.py`; update all call sites that consume registry dicts
- [✓] Add skill name validation in `integrations/__init__.py` (`register()`) and `skills/registry.py` (`_load()`); add tests
- [✓] Hook auto-reload into per-request startup (staleness check against mtime of `~/.marcel/skills/`); add test covering hot-drop scenario
- [✓] `make check` passes

## Relationships
_None._

## Comments
### 2026-04-16 - Claude
Improvements 5 and 6 kept in the description for visibility. They should not be implemented without a concrete driver.

## Implementation Log

### 2026-04-16 10:45 - LLM Implementation
**Action**: Implemented all four skill pattern improvements in a single impl commit
**Files Modified**:
- `src/marcel_core/skills/registry.py` — added `SkillConfig` typed dataclass (`type`, `handler`, `url`, `method`, `auth`, `params`, `response_transform`, `command`); `get_skill()` now returns `SkillConfig`; `_load()` validates names against `SKILL_NAME_PATTERN` and skips invalid entries with a warning; mtime-based auto-reload invalidates cache when `skills.json` changes on disk
- `src/marcel_core/skills/integrations/__init__.py` — `register()` validates names at decoration time, raises `ValueError` for anything not matching `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`
- `src/marcel_core/skills/executor.py` — `run()`, `_run_shell()`, `_run_python()` accept `SkillConfig` instead of `dict`; attribute access throughout
- `src/marcel_core/skills/loader.py` — added `get_skill_resource(skill_name, resource_name)` (exact + stem-based case-insensitive matching, excludes SKILL.md), `list_skill_resources(skill_name)`, `_find_skill_dir(skill_name)` helper
- `src/marcel_core/tools/marcel/skills.py` — added `read_skill_resource()` action; `read_skill()` now appends an available-resources footer when the skill dir has extra files
- `src/marcel_core/tools/marcel/dispatcher.py` — added `resource=` param; wired `read_skill_resource` case; updated error message list
- `tests/core/test_skills.py` — updated all executor/registry tests to use `SkillConfig`; added `test_invalid_name_in_skills_json_is_skipped`, `test_auto_reload_when_skills_json_changes`, `test_invalid_name_raises_on_register`, `test_no_dot_name_raises_on_register`, `test_valid_names_with_underscores_and_digits`
- `tests/core/test_skill_loader.py` — added `TestGetSkillResource` and `TestListSkillResources` classes (12 new tests)
- `docs/skills.md` — documented `read_skill_resource` action and updated `marcel` tool contract table
**Commands Run**: `uv run pytest tests/ -x -q` (1371 passed), `uv run ruff check`, `uv run pyright src/`
**Result**: All checks green. Auto-reload is mtime-based on `skills.json`; SKILL.md files are already loaded fresh each request (no cache in loader.py).
**Next**: Close

**Reflection** (via pre-close-verifier):
- Verdict: REQUEST CHANGES → addressed
- Coverage: 5/5 requirements addressed
- Shortcuts found: none (bare `except Exception` in `_find_skill_dir` is consistent with existing project style in `_check_requirements`)
- Scope drift: none — items 5 and 6 correctly left unimplemented
- Stragglers: 3 found (docs/skills.md summary bullet, tools/marcel/__init__.py docstring, docs/architecture.md comment) — all fixed in follow-up impl commit e05f1c3

## Lessons Learned

### What worked well
- Starting from a cross-repo analysis (plan mode) before writing any code produced a tight scope. No mid-impl pivots.
- The SkillConfig dataclass forced every executor test to be updated, which immediately caught that all test call sites were using raw dicts — good forcing function.
- The mtime-based auto-reload approach is zero-cost when files don't change (one `stat()` call) and naturally handles the hot-drop scenario without any extra wiring at the request layer.

### What to do differently
- Grep for straggler docs *before* the impl commit, not after. Three files were missed (docs/skills.md summary bullet, tools/marcel/__init__.py docstring, docs/architecture.md comment) and required a second fixup commit. Running `grep -r "read_skill" docs/ src/` before closing would have caught them.
- The issue description said "auto-reload on request startup" but the implementation hooks into `_load()` (called by `get_skill()`) rather than `stream_turn`. The intent was the same but the description was imprecise. Future issues should describe *where* the hook lives, not just *when* it fires.

### Patterns to reuse
- **`SkillConfig.from_dict()` factory pattern**: when replacing a bare-dict API with a typed dataclass, a `from_dict()` classmethod lets you convert at the registry boundary and keep all other code typed. Tests can construct instances directly with keyword args.
- **mtime cache invalidation**: tracking `_cache_mtime = file.stat().st_mtime` alongside `_cache` is a reliable two-liner for any module-level disk cache that should auto-reload on file change. Pairs naturally with an existing `reload()` function.
