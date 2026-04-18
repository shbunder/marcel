# ISSUE-e1b9c4: Delete vestigial settings integration handler

**Status:** Closed
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
- [✓] Delete `src/marcel_core/skills/integrations/settings.py`.
- [✓] Remove the integration-handler test section from `tests/core/test_settings.py` (storage + channel-tier sections stay).
- [✓] Update `project/issues/open/ISSUE-260418-2ccc10-...md` — settings drops out of the migration list with a one-line audit note.
- [✓] Grep the repo for `marcel_core.skills.integrations.settings` and confirm only historical references remain (`project/issues/closed/`, this issue file).
- [✓] Run `make check` — 90% coverage gate still green after the deletion.

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

### 2026-04-18 — Cleanup execution

Deleted `src/marcel_core/skills/integrations/settings.py` (71 lines, three `@register` decorators that no agent ever reached). Removed the eight integration-handler test functions from `tests/core/test_settings.py` (lines 72-153 in the pre-change file): `test_list_models_returns_all_models`, `test_get_model_returns_default_when_unset`, `test_get_model_returns_saved_preference`, `test_get_model_missing_channel_returns_error`, `test_set_model_saves_preference`, `test_set_model_rejects_unqualified_model`, `test_set_model_accepts_off_registry_qualified_model`, `test_set_model_missing_params_returns_error`. Storage tests (lines 24-69) and channel-tier tests (lines 156+) stayed untouched. Updated the test-file module docstring from "settings storage and integration handlers" to just "settings storage".

Updated `project/issues/open/ISSUE-260418-2ccc10-migrate-real-integrations-to-habitats.md` — title now reads "banking, icloud, news" (was "banking, icloud, news, settings"); resolved-intent paragraph drops settings from the migration list and gains a "Settings dropped (2026-04-18)" note pointing back to this issue; the plugin-surface table no longer mentions `marcel_core.plugin.models` as a required addition (it landed in ISSUE-c48967 already and the settings habitat that would have consumed it is no longer happening); the per-integration migration tasks lose the settings line and the deletion task drops `settings` from the directory list. The plugin-surface task and an audit task are both marked `[✓]`. Migration target count is now 3.

Verification: `make check` green at 91.96% coverage. Repo-wide grep for `marcel_core.skills.integrations.settings` and `settings.list_models|settings.get_model|settings.set_model` returns hits only in `project/issues/{wip,open,closed}/` — no source/docs/skills stragglers. The marcel-tool path (`tools/marcel/settings.py`) is unchanged and still shows 96% coverage in the test-suite report.

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 6/6 tasks addressed
- Shortcuts found: none
- Scope drift: none — diff is exactly delete handler + delete tests + update umbrella + rewrite WIP file
- Stragglers: none — only historical references in `project/issues/closed/ISSUE-036-*.md` remain, intentionally
- Notable: live marcel-tool path (`tools/marcel/settings.py`) and SKILL.md are correctly untouched; integration-pairs rule N/A (orphaned handler had no paired SKILL.md, which was the audit finding); plugin-surface table edit in ISSUE-2ccc10 is correct since `marcel_core.plugin.models` already landed in ISSUE-c48967 and no remaining migration target needs it.
- Verifier flagged "should kernel-runtime knobs ever be zoo-contributable as marcel-tool actions?" as a real-but-deferred follow-up worth capturing as a separate `/new-issue` if the question recurs. Not a blocker for close.

## Lessons Learned

### What worked well

- **Audit-first as Task 1.** The original plan was to migrate the handler; the audit task at the top of the task list immediately revealed the duplication and saved a full migration's worth of work moving dead code into the zoo. Cheap insurance against shipping a no-op.
- **Repo-wide grep for the dispatch pattern, not just the file name.** Grepping for `integration(id="settings\|"settings\.` (the actual call shape the agent would emit) — rather than just for `skills/integrations/settings.py` — was what proved no production caller existed. File-name grep alone would have found the test, missed the absence of real usage, and left ambiguity.
- **Rescope inside the same issue rather than spawn a new one.** Renaming the WIP file via `git mv` and rewriting Capture / Resolved-intent / Description / Tasks kept the audit findings, the decision rationale, and the cleanup work in one document. The `ISSUE-2ccc10` umbrella note points back here for the count change. No fragmented history.

### What to do differently

- **Cross-check the umbrella issue's plugin-surface plan against what already landed.** ISSUE-2ccc10's "surface grows to cover" table still listed `marcel_core.plugin.models` as a needed addition even though ISSUE-c48967 had landed it. With settings dropping out of the migration list, removing that table row was the right edit — but the row had been stale since c48967 merged. Lesson: when an umbrella references "plugin-surface additions needed", treat it as a TODO that goes stale with each landed sub-issue and update it on every sub-issue close, not only when scope changes.

### Patterns to reuse

- **Two-tier audit before a "migrate X" task.** Whenever a planned migration touches a name that exists in two places (here: `tools/marcel/settings.py` and `skills/integrations/settings.py`), Task 1 should be "identify which one is live before moving anything." For Marcel specifically, the marcel-tool / integration-handler split is exactly the kind of duplication that can hide dead registrations behind plausible-looking imports.
- **Update the umbrella's count and reasoning, not just its task list.** The umbrella's title, resolved-intent paragraph, and per-target task list all needed to reflect the 4→3 drop, plus a one-paragraph "X dropped (date): why" note linking back to the cleanup issue. Leaving the title saying "4 things" while the body says "3 things" is the kind of drift that bites three issues from now.
