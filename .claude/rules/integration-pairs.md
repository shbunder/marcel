---
paths:
  - "src/marcel_core/skills/**/*.py"
  - "tests/skills/**/*.py"
---

# Rule — integration / skill habitat pairs

Every Marcel integration is now **two habitats** that ship together — an *integration habitat* (the handler) and a *skill habitat* (the docs the agent reads). Modifying one without the other is **half-shipped work** — the pre-close-verifier treats it as scope drift.

Habitats live in [marcel-zoo](https://github.com/shbunder/marcel-zoo) (or any directory pointed to by `MARCEL_ZOO_DIR`), not in this repo. The kernel itself ships zero integrations and zero bundled skills — the loader and plugin surface live under `src/marcel_core/skills/` but the habitats they discover live exclusively in the zoo.

## The two habitats

### 1. Integration habitat — `<MARCEL_ZOO_DIR>/integrations/<name>/`

- **`__init__.py`** — Python package; runs at discovery time. Registers handlers with `@register("<name>.<action>")` from `marcel_core.plugin`. Every handler name **must** start with `<name>.` (the directory name); handlers outside that namespace cause the whole habitat to be rolled back.
- **`integration.yaml`** — declarative metadata used by skill habitats that `depends_on:` this integration. Schema:

  ```yaml
  name: docker                  # must equal directory name
  description: Manage Docker containers on the home NUC
  provides:                     # documentation; must all be in <name>.* namespace
    - docker.list
    - docker.status
  requires:                     # what this integration needs to function
    credentials: [BANK_API_KEY]
    env: [DOCKER_HOST]
    files: [signing_key.pem]
    packages: [icloudpy]
  ```

  Without `integration.yaml`, the integration handlers still work, but **no skill habitat can `depends_on:` it** — `get_integration_metadata()` returns `None`, which the skill loader treats as "requirements not met" → the user is shown SETUP.md.

### 2. Skill habitat — `<MARCEL_ZOO_DIR>/skills/<name>/`

- **`SKILL.md`** — teaches the agent how to call the integration. Frontmatter must declare `depends_on: [<integration>]` linking it to the integration habitat. Inline `requires:` is still supported for skills with no integration handler, but for any skill that calls `integration(id="...")`, prefer `depends_on:` so the credential/env list lives in one place (the integration's `integration.yaml`).
- **`SETUP.md`** — shown instead of `SKILL.md` when **either** the inline `requires:` OR any `depends_on:` integration's `requires:` is unsatisfied. This is how Marcel conversationally onboards family members.

## Why

A family member says *"I want you to read my iCloud calendar"*. Marcel loads the `icloud` skill's docs:

- If credentials are configured → Marcel reads `SKILL.md`, calls `integration(id="icloud.list_events", ...)`, returns the events.
- If credentials are missing → Marcel reads `SETUP.md`, walks Alice through getting an app-specific password, stores it encrypted, and tries again.

A skill without `SETUP.md` means the agent silently fails when the integration isn't configured. A handler without `SKILL.md` means the agent doesn't know the integration exists. A `SKILL.md` whose `depends_on:` integration has no `integration.yaml` falls into setup mode forever — easy to miss in dev where the env var is exported in the shell.

## Checklist before closing

- [ ] New integration handler → integration habitat has `integration.yaml` AND a paired skill habitat with both `SKILL.md` and `SETUP.md` exists
- [ ] `provides:` in `integration.yaml` lists every `@register`'d handler name and matches the namespace
- [ ] Renamed `@register("...")` action → integration handler, `provides:` list, and SKILL.md examples all reflect the new name
- [ ] Removed action → handler, `provides:` entry, and SKILL.md instructions cleaned up; if the whole integration is gone, both habitats removed
- [ ] New `requires:` entry in `integration.yaml` → SETUP.md in the paired skill habitat walks the user through providing it
- [ ] New integration must not require changes to `tool.py`, `executor.py`, or `runner.py` — if it does, the abstraction is wrong (per Marcel's "self-contained integrations" principle)

## Enforcement

[.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md) checks any diff touching `src/marcel_core/skills/` or zoo habitats for mismatched pair updates.
