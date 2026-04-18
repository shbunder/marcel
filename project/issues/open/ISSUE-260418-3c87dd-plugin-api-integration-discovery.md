# ISSUE-3c87dd: Define `marcel_core.plugin` API + widen integration discovery to data root

**Status:** Open
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "I want to make Marcel more modular, a first step is to separate specific configurations from the source code. I want to move code related to integrations to .marcel/integrations (...) The goal later is to put this into a separate git repository that 'installs' to the correct file."

**Resolved intent:** This is step 1 of the marcel-zoo extraction — the "userspace" repo that will eventually hold all modular components (integrations, skills, channels, jobs, agents) as habitats installed under `~/.marcel/`. Before moving any code out, the kernel needs a stable plugin API surface so zoo code has something contractual to import from, and the integration discovery mechanism needs to widen from "walk this one package" to "also walk `<data_root>/integrations/`". No behavior change for existing integrations; this purely opens the door.

## Description

Today, integration handlers live at [src/marcel_core/skills/integrations/](../../src/marcel_core/skills/integrations/) and are auto-discovered by [skills/integrations/__init__.py:82-96](../../src/marcel_core/skills/integrations/__init__.py) via `pkgutil.iter_modules(__path__)`. External zoo code cannot plug in without being pip-installed into the same package namespace.

Two changes unlock everything downstream:

1. **`marcel_core.plugin` surface.** A new package that re-exports exactly what integrations are allowed to import. Starts minimal — `register`, logger, a stable typing surface. Grows per-component-type in later issues. Zoo habitats that reach past this surface are using a private API and own their own breakage.
2. **Widened discovery.** `discover()` additionally walks `<data_root>/integrations/` (resolved via `settings.data_dir`), loading each subdirectory as a module via `importlib.util.spec_from_file_location`. The same `@register` contract applies.

Directory-name / handler-namespace convention is enforced: an integration at `<data_root>/integrations/banking/` may only register `banking.*` handlers. Collisions across directories are rejected at load time (already the case per [integrations/__init__.py:60-64](../../src/marcel_core/skills/integrations/__init__.py)).

No integrations move in this issue — that's ISSUE-6ad5c7. This is purely the plumbing that makes the move possible.

## Tasks

- [ ] Create `src/marcel_core/plugin/__init__.py` re-exporting the integration surface: `register`, `IntegrationHandler` type, module logger helper. Nothing else yet.
- [ ] Add `src/marcel_core/plugin/__init__.py` docstring describing plugin-API stability contract: "anything not re-exported here is internal and may break between Marcel versions."
- [ ] Extend `discover()` in [skills/integrations/__init__.py](../../src/marcel_core/skills/integrations/__init__.py) to also walk `<data_root>/integrations/`. Use `importlib.util.spec_from_file_location` against each subdirectory's `__init__.py` so habitat packages can have multi-file code.
- [ ] Enforce directory-name / handler-namespace match. If `<data_root>/integrations/foo/` registers `bar.baz`, fail loudly at discovery with a clear error (not silent skip — this is user-facing misconfiguration).
- [ ] Ensure error isolation: one broken external integration must not crash discovery for the rest. Log-and-skip with exception detail.
- [ ] Unit test: fake integration at `<tmp>/integrations/demo/__init__.py` with `@register("demo.ping")` loads, is callable via `get_handler`, and is listed in `list_python_skills()`.
- [ ] Unit test: integration dir `foo/` registering `bar.*` fails discovery with a helpful error.
- [ ] Unit test: integration with an import error is skipped, logged, and does not break discovery of siblings.
- [ ] Docs: new page `docs/plugins.md` describing the `marcel_core.plugin` surface and the `<data_root>/integrations/` convention. Register in `mkdocs.yml` nav.
- [ ] Update `docs/skills.md` to mention the data-root discovery path alongside the source-tree path.

## Relationships

- Blocks: ISSUE-6ad5c7 (habitat split + docker POC — needs this discovery path)
- Blocks: ISSUE-7d6b3f (channel plugin), ISSUE-a7d69a (job habitat) — both will follow the same plugin-surface pattern

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
