---
paths:
  - "src/marcel_core/skills/**/*.py"
  - "src/marcel_core/defaults/skills/**/*"
  - "tests/skills/**/*.py"
---

# Rule — integration pairs

Every Marcel integration is three artifacts that ship together. Modifying one without the others is **half-shipped work** — the pre-close-verifier treats it as scope drift.

## The three pieces

1. **Handler** — `src/marcel_core/skills/integrations/<name>.py` with `@register("<name>.<action>")` async functions, OR a JSON entry in `skills.json` for simple HTTP/shell integrations.
2. **SKILL.md** — `src/marcel_core/defaults/skills/<name>/SKILL.md` teaching the agent how to call the integration, with inline examples. Must have a `requires:` field listing credentials / env vars / files needed.
3. **SETUP.md** — `src/marcel_core/defaults/skills/<name>/SETUP.md` shown **instead of** `SKILL.md` when `requires:` is not satisfied. This is how Marcel conversationally onboards family members.

The default skill files in `src/marcel_core/defaults/skills/` are seeded to the user's `~/.marcel/skills/` on first startup by `src/marcel_core/skills/loader.py`.

## Why

A family member says *"I want you to read my iCloud calendar"*. Marcel loads the `icloud` skill's docs:

- If credentials are configured → Marcel reads `SKILL.md`, calls `integration(id="icloud.list_events", ...)`, returns the events.
- If credentials are missing → Marcel reads `SETUP.md`, walks Alice through getting an app-specific password, stores it encrypted, and tries again.

A skill without `SETUP.md` means the agent silently fails when the integration isn't configured. A handler without `SKILL.md` means the agent doesn't know the integration exists. A `SKILL.md` without a `requires:` field means the setup fallback never activates.

## Checklist before closing

- [ ] New integration handler → both `SKILL.md` and `SETUP.md` exist at `defaults/skills/<name>/`
- [ ] Renamed `@register("...")` action → all three files (or just SKILL.md if it's a simple rename) reflect the new name
- [ ] Removed action → all three files cleaned up, or the whole integration removed
- [ ] New `requires:` entry in SKILL.md → SETUP.md walks the user through providing it
- [ ] New integration must not require changes to `tool.py`, `executor.py`, or `runner.py` — if it does, the abstraction is wrong (per Marcel's "self-contained integrations" principle)

## Enforcement

[.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md) checks any diff touching `skills/integrations/` or `defaults/skills/` for mismatched pair updates.
