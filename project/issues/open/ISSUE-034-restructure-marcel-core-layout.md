# ISSUE-034: Restructure marcel_core Layout

**Status:** Open
**Created:** 2026-04-09
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor

## Capture
**Original request:** "can we structure the marcel_core repo a bit better? Some examples
1) src/marcel_core/banking exist but there is also code for this in src/marcel_core/skills/integrations => skills should be treated as "sub-apps" in the code, all logic should be put inside a single-folder inside integrations (except for the skills in .marcel
2) aren't channels closely related to the api? shouldn't this live closer together? also there is separate folder marcel_core/telegram, should this not be a subfolder of marcel_core/channels?
Check the code-base for more logical grouping of files, be critical and ensure the code-base is easy to navigate. Also check for obsolete code and folders"

**Resolved intent:** The `marcel_core` package has grown organically and now has several grouping issues: integration business logic (`banking/`, `icloud/`) lives at the top level instead of inside `skills/integrations/` where it belongs; the `telegram/` channel lives at the top level instead of as a subfolder of `channels/`; and two empty placeholder folders (`skills/descriptions/`, `marcel_app/`) can be deleted. The work is purely a structural refactor — no logic changes, all imports updated to match new paths.

## Description

Three concrete moves + two deletions will make the package layout consistent with the integration pattern documented in `project/CLAUDE.md`:

1. **`banking/` → `skills/integrations/banking/`** — Banking is an integration. Its client, cache, and sync logic belongs co-located with the integration dispatcher in `skills/integrations/`.
2. **`icloud/` → `skills/integrations/icloud/`** — Same reasoning. iCloud is an integration.
3. **`telegram/` → `channels/telegram/`** — Telegram is a channel. Its webhook, bot, session, and formatting code belongs under the `channels/` transport layer.
4. **Delete `skills/descriptions/`** — Empty folder, only contains `.keep`. No known purpose.
5. **Delete `marcel_app/`** — Empty placeholder at `src/marcel_app/`. Only contains `.keep`.

All import paths across the codebase must be updated to reflect the new locations. No logic changes.

## Tasks
- [ ] Move `src/marcel_core/banking/` → `src/marcel_core/skills/integrations/banking/`
- [ ] Move `src/marcel_core/icloud/` → `src/marcel_core/skills/integrations/icloud/`
- [ ] Move `src/marcel_core/telegram/` → `src/marcel_core/channels/telegram/`
- [ ] Update all import statements referencing the old paths
- [ ] Delete `src/marcel_core/skills/descriptions/` (empty)
- [ ] Delete `src/marcel_app/` (empty placeholder)
- [ ] Run `make check` — all checks must pass
- [ ] Restart the service to verify the new layout works at runtime

## Relationships

## Comments

## Implementation Log
