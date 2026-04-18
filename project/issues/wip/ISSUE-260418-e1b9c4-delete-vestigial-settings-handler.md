# ISSUE-e1b9c4: Delete vestigial settings integration handler

**Status:** WIP
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** cleanup, refactor

## Capture

**Original request:** "Migrate the `settings` integration out of the kernel into a marcel-zoo habitat — second sub-issue under ISSUE-2ccc10. Smallest migration target (no credentials, single file, no scheduled jobs). Validates: switching imports to the new `marcel_core.plugin.models` surface, moving SKILL.md + SETUP.md to a zoo skill habitat, removing the source-tree copy, and reworking tests to keep core-side coverage on dispatch+loader rather than the migrated integration."

**Audit finding (rescoped 2026-04-18):** Before starting the migration, the audit revealed that `settings` is wired up two ways in the kernel — and only one of them is live:

1. **Marcel utility tool** at `src/marcel_core/tools/marcel/settings.py`, dispatched in `src/marcel_core/tools/marcel/dispatcher.py:89-94`. Invoked via `marcel(action="list_models")` etc. This is what `src/marcel_core/defaults/skills/settings/SKILL.md` teaches the agent. Real and used.
2. **Integration handler** at `src/marcel_core/skills/integrations/settings.py`. Registers `settings.list_models` / `settings.get_model` / `settings.set_model` via `@register`. **No SKILL.md anywhere instructs the agent to call these via `integration(id="settings.list_models")`.** Dead code.

A repo-wide grep for `integration(id="settings` and `"settings.` returned zero hits in production code; only `tests/core/test_settings.py` references the dead handler (it imports the functions directly to test them, bypassing the dispatcher entirely).

Migrating path 2 to a zoo habitat would move dead code into a different repo. Path 1 cannot move to zoo without a new "zoo contributes marcel-tool actions" extension point that does not exist (and isn't in scope here).

**Resolved intent:** Replace the planned migration with a cleanup. Delete the dead integration handler and the dead-code tests. Keep the marcel utility tool, its SKILL.md, and the storage layer untouched — those are the real settings surface. Update ISSUE-2ccc10 to drop `settings` from the migration list (the umbrella now covers banking + icloud + news, three migrations not four). The bigger "kernel-runtime knobs as zoo-contributed marcel-tool actions" question is real but separate; capture it as a follow-up only if needed later.

## Description

**Delete:**

- `src/marcel_core/skills/integrations/settings.py` — the entire file. Three `@register` decorators that no caller ever reaches.
- The integration-handler test block in `tests/core/test_settings.py` (the `test_list_models_*`, `test_get_model_*`, `test_set_model_*` functions). The storage tests above and the channel-tier tests below remain untouched.

**Keep:**

- `src/marcel_core/tools/marcel/settings.py` — the real marcel-tool action implementations.
- `src/marcel_core/tools/marcel/dispatcher.py` — wires `marcel(action="list_models")` etc.
- `src/marcel_core/defaults/skills/settings/SKILL.md` — teaches the agent to use the marcel-tool actions; still ships with the kernel.
- `src/marcel_core/storage/settings.py` — kernel storage layer for per-channel model + tier preferences.
- `tests/core/test_settings.py` storage and channel-tier sections.

**Update:**

- `project/issues/open/ISSUE-260418-2ccc10-migrate-real-integrations-to-habitats.md` — drop `settings` from the migration list. Note in its description that the audit revealed settings was vestigial and was deleted under ISSUE-e1b9c4.

**Verify:**

- `make check` still green at the 90% coverage gate.
- No grep hits for `marcel_core.skills.integrations.settings` outside of `project/issues/closed/` (historical).
- `marcel(action="list_models")` and friends still work end-to-end (smoke check via the marcel-tool path).

## Tasks

- [✓] Audit `src/marcel_core/skills/integrations/settings.py` against the plugin surface — identified the file as dead code (no caller, dispatched only via direct test imports).
- [ ] Delete `src/marcel_core/skills/integrations/settings.py`.
- [ ] Remove the integration-handler test section from `tests/core/test_settings.py` (storage + channel-tier sections stay).
- [ ] Update `project/issues/open/ISSUE-260418-2ccc10-...md` — settings drops out of the migration list with a one-line audit note.
- [ ] Grep the repo for `marcel_core.skills.integrations.settings` and confirm only historical references remain (`project/issues/closed/`, this issue file).
- [ ] Run `make check` — 90% coverage gate still green after the deletion.

## Relationships

- Depends on: ISSUE-c48967 (plugin surface — landed; this issue would have consumed it but instead deletes the handler entirely)
- Part of: ISSUE-2ccc10 (umbrella tracker — count drops from 4 to 3 as a result of this issue)
- Pattern reference: ISSUE-6ad5c7 (docker POC) — N/A here, settings is a deletion not a migration

## Implementation Log

### 2026-04-18 — Rescope audit

Started this issue intending to migrate `src/marcel_core/skills/integrations/settings.py` to `~/projects/marcel-zoo/integrations/settings/`. The Task 1 audit (find every kernel import, plan the import switch) immediately turned up the duplication problem:

- `tools/marcel/settings.py` provides `list_models` / `get_model` / `set_model` as marcel-tool actions, dispatched by `tools/marcel/dispatcher.py:89-94`.
- `defaults/skills/settings/SKILL.md` teaches the agent to call `marcel(action="list_models")` — i.e. it routes through the marcel-tool, not the integration handler.
- `skills/integrations/settings.py` registers `settings.list_models` / `settings.get_model` / `settings.set_model` as integration handlers — but `grep -rn 'integration(id="settings\|"settings\.' src/ docs/ project/ ~/.marcel/skills/` returns nothing in production code. Only `tests/core/test_settings.py` reaches into the module, and it imports the handler functions directly rather than going through the dispatcher.

Decision (with user): **A — replace the migration with a cleanup**. Delete the dead integration handler and its tests. The marcel-tool path stays; settings drops out of the ISSUE-2ccc10 migration list. The follow-up question of "should kernel-runtime knobs ever be zoo-contributable?" is real but separate.

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
