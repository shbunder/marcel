# ISSUE-e1b9c4: Migrate `settings` integration to a marcel-zoo habitat

**Status:** Open
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "Migrate the `settings` integration out of the kernel into a marcel-zoo habitat — second sub-issue under ISSUE-2ccc10. Smallest migration target (no credentials, single file, no scheduled jobs). Validates: switching imports to the new `marcel_core.plugin.models` surface, moving SKILL.md + SETUP.md to a zoo skill habitat, removing the source-tree copy, and reworking tests to keep core-side coverage on dispatch+loader rather than the migrated integration."

**Resolved intent:** With the plugin surface landed (ISSUE-c48967), the `settings` integration is the obvious first migration: one file, no credentials, no client library, no scheduled job, no cache. Its only kernel touch-points are the four model-registry helpers — exactly what `marcel_core.plugin.models` already exposes. Moving it proves the end-to-end migration pipeline (zoo discovery → plugin-surface import → handler dispatch → skill doc loading → test rework) without any of the harder concerns (credentials, sync loops, third-party deps) that banking and news will surface. The kernel ends this issue with one fewer integration to ship, and the zoo gains its second integration habitat alongside docker.

## Description

**What moves where:**

| From (kernel) | To (marcel-zoo) |
|---|---|
| `src/marcel_core/skills/integrations/settings.py` | `<MARCEL_ZOO_DIR>/integrations/settings/__init__.py` |
| (new file) | `<MARCEL_ZOO_DIR>/integrations/settings/integration.yaml` |
| `src/marcel_core/defaults/skills/settings/SKILL.md` | `<MARCEL_ZOO_DIR>/skills/settings/SKILL.md` |
| (new file) | `<MARCEL_ZOO_DIR>/skills/settings/SETUP.md` |

**Import switch:** the moved handler changes its imports from `marcel_core.harness.agent` and `marcel_core.storage.settings` to `marcel_core.plugin.models`. The behaviour is byte-identical — `models.*` is `is`-identity to the kernel helpers — but the integration no longer reaches past the surface.

**SKILL.md frontmatter:** `depends_on: [settings]` so the skill resolves its (empty) requirements via the integration's `integration.yaml`. Today the file has no `depends_on` because there was nothing to depend on; the migration adds the link.

**SETUP.md wrinkle:** the integration-pairs rule mandates SETUP.md alongside every SKILL.md, but settings has zero `requires:` — there is literally nothing to set up. Two options:

- (a) Ship a one-paragraph SETUP.md that explains the integration is always available and points to the model-list command if the user wants to choose a model. Honest documentation of the "no-config" state.
- (b) Carve out a kernel-side rule: when both inline `requires:` and every depended-on integration's `requires:` are empty, the loader skips the SETUP.md check.

Pick (a) on the principle that the rule is a strong default and a one-paragraph file is cheaper than a kernel-side exception.

**Test rework:** `tests/core/test_settings.py` today covers BOTH the storage layer (`storage.settings.{load,save}_channel_model`, `_channel_tier`) AND the integration handlers (`settings.list_models`, `settings.get_model`, `settings.set_model`). Split into two:

- The storage tests stay in `tests/core/test_settings.py` — `storage.settings` is kernel.
- The integration handler tests move to `<MARCEL_ZOO_DIR>/integrations/settings/tests/test_handlers.py` — they test zoo code, they live with zoo code.
- A small core-side test in `tests/core/test_settings_loader.py` (or extending `tests/core/test_skill_loader.py`) verifies the loader picks up the moved handler when `MARCEL_ZOO_DIR` is configured. This is a fake-plugin or zoo-fixture test, not a real-integration test.

**Coverage delta:** removing `settings.py` and the integration-handler half of `test_settings.py` from the kernel will lower kernel coverage. The replacement loader test must cover the discovery+dispatch path so the 90% gate stays green. Verify with `make check` before close.

**Out of scope (defer to next sub-issues):** banking, icloud, news. The pyproject decision for marcel-zoo. The scheduled-job hook (only news needs it).

## Tasks

- [ ] Audit `src/marcel_core/skills/integrations/settings.py` against `marcel_core.plugin.models` — confirm every kernel touch is covered (no surprise imports beyond the four model helpers).
- [ ] Create `<MARCEL_ZOO_DIR>/integrations/settings/__init__.py` — port the three handlers (`list_models`, `get_model`, `set_model`) and switch all imports to `marcel_core.plugin`.
- [ ] Create `<MARCEL_ZOO_DIR>/integrations/settings/integration.yaml` — `name: settings`, `description`, `provides: [settings.list_models, settings.get_model, settings.set_model]`, `requires: {}`.
- [ ] Create `<MARCEL_ZOO_DIR>/skills/settings/SKILL.md` — copy from `src/marcel_core/defaults/skills/settings/SKILL.md`, add `depends_on: [settings]` frontmatter, refresh examples to use the live action names.
- [ ] Create `<MARCEL_ZOO_DIR>/skills/settings/SETUP.md` — minimal "always available" doc per option (a) above. Stays consistent with the integration-pairs rule.
- [ ] Move integration-handler tests from `tests/core/test_settings.py` to `<MARCEL_ZOO_DIR>/integrations/settings/tests/test_handlers.py`. Storage-layer tests stay in core.
- [ ] Add a core-side discovery+dispatch test that exercises the settings handler via the zoo-loaded path (use the same `_write_integration` pattern as `tests/core/test_plugin.py`) so coverage of the loader path remains explicit.
- [ ] Delete `src/marcel_core/skills/integrations/settings.py` and `src/marcel_core/defaults/skills/settings/`.
- [ ] Verify with `make check` — 90% coverage gate still green; the new loader test covers what the deleted file used to.
- [ ] Verify behaviour: with `MARCEL_ZOO_DIR=~/projects/marcel-zoo` set, `settings.list_models` / `get_model` / `set_model` still respond identically. With it unset, the integration is gone (proves the kernel ships nothing settings-related).
- [ ] Update `docs/plugins.md` and/or `docs/skills.md` if any wording about "first-party integrations" or example lists referenced settings — keep references accurate.
- [ ] Grep the repo for stale `marcel_core.skills.integrations.settings` references and clean them up.

## Relationships

- Depends on: ISSUE-c48967 (plugin surface — landed)
- Part of: ISSUE-2ccc10 (umbrella tracker for the four real-integration migrations)
- Pattern reference: ISSUE-6ad5c7 (docker POC — same migration shape, no plugin-surface usage)

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
