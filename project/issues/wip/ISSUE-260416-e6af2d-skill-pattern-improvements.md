# ISSUE-e6af2d: Skill Pattern Improvements (pydantic-ai-skills Lessons)

**Status:** WIP
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

- [ ] Add `read_skill_resource(skill, resource)` action to `marcel` tool (`tools/marcel/skills.py`)
  - [ ] Update `skills/loader.py` to expose named resources per skill (SETUP.md, components.yaml, feeds.yaml, any extra .md/.yaml files)
  - [ ] Update `SKILL.md` format docs / `docs/skills.md` to document the new action
- [ ] Introduce `SkillConfig` dataclass in `skills/registry.py`; update all call sites that consume registry dicts
- [ ] Add skill name validation in `integrations/__init__.py` (`register()`) and `skills/registry.py` (`_load()`); add tests
- [ ] Hook auto-reload into per-request startup (staleness check against mtime of `~/.marcel/skills/`); add test covering hot-drop scenario
- [ ] `make check` passes

## Relationships
_None._

## Comments
### 2026-04-16 - Claude
Improvements 5 and 6 kept in the description for visibility. They should not be implemented without a concrete driver.

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
