# ISSUE-039: Rename `integration(skill=...)` parameter to `integration(id=...)`

**Status:** Open
**Created:** 2026-04-09
**Assignee:** Unassigned
**Priority:** Low
**Labels:** refactor

## Capture
**Original request:** "small thing but I don't like that 'banking.transactions' has to be passed through the skill-id. I would just call it id='banking.transactions' because it's the id of the integration, it's not a skill in itself. can you fix that everywhere in the code, create an issue for this"

**Resolved intent:** The `integration` tool currently accepts a `skill` parameter to identify which integration to dispatch to (e.g. `integration(skill="banking.transactions")`). The parameter name is misleading — it's actually an integration ID, not a skill name. Rename the parameter to `id` everywhere: in the MCP tool schema, the tool handler, all SKILL.md docs, and any other references.

## Description

The `integration` MCP tool in `src/marcel_core/skills/tool.py` defines its dispatch parameter as `skill`. This naming was inherited from an earlier design where integrations were called "skills". Now that the system distinguishes between skills (doc bundles) and integrations (executable handlers), the parameter name is confusing.

Renaming it to `id` makes the intent clear: you're passing the dotted integration ID (e.g. `"banking.transactions"`), not the name of a skill file.

Files affected:
- `src/marcel_core/skills/tool.py` — schema definition (`skill` → `id`) and handler lookup (`args.get('skill', ...)` → `args.get('id', ...)`)
- `.marcel/skills/banking/SKILL.md` — all `integration(skill=...)` call examples
- `.marcel/skills/icloud/SKILL.md` — all `integration(skill=...)` call examples
- `.marcel/skills/settings/SKILL.md` — all `integration(skill=...)` call examples
- `.marcel/skills/docker/SKILL.md` — all `integration(skill=...)` call examples
- `docs/skills.md` — any example snippets using `skill=`
- `project/CLAUDE.md` — Integration Pattern section references `integration(skill="name.action")`

## Tasks
- [ ] Rename `skill` → `id` in `_INTEGRATION_SCHEMA` properties and `required` list in `tool.py`
- [ ] Update handler to read `args.get('id', ...)` instead of `args.get('skill', ...)`
- [ ] Update all SKILL.md call examples in `.marcel/skills/`
- [ ] Update `docs/skills.md` example snippets
- [ ] Update `project/CLAUDE.md` Integration Pattern section
- [ ] Run `make check` to confirm nothing is broken

## Relationships

## Comments

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
