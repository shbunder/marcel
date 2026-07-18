---
paths:
  - "src/marcel_core/skills/**/*.py"
  - "tests/skills/**/*.py"
  - "src/marcel_core/toolkit/**/*.py"
  - "tests/core/test_toolkit*.py"
---

# Rule — toolkit / skill habitat pairs

Every Marcel toolkit habitat that carries user-visible behaviour normally
ships with a paired **skill habitat**: the toolkit holds the handler, the
skill holds the agent prompting. Modifying one without the other is
**half-shipped work** — the pre-close-verifier treats it as scope drift.

Habitats live in [marcel-zoo](https://github.com/shbunder/marcel-zoo) (or
any directory pointed to by `MARCEL_ZOO_DIR`), not in this repo. The
kernel itself ships zero toolkit habitats and zero bundled skills — the
loader and plugin surface live under `src/marcel_core/toolkit/` and
`src/marcel_core/skills/` but the habitats they discover live
exclusively in the zoo.

See [docs/habitats.md](../../docs/habitats.md) for the full five-kind
taxonomy. This rule is specifically about the toolkit ↔ skill pairing —
the two-habitat pattern that most real features use.

## The two habitats

### 1. Toolkit habitat — `<MARCEL_ZOO_DIR>/toolkit/<name>/`

- **`__init__.py`** — Python package; runs at discovery time. Registers
  handlers with `@marcel_tool("<name>.<action>")` from
  `marcel_core.plugin`. Every handler name **must** start with `<name>.`
  (the directory name); handlers outside that namespace cause the whole
  habitat to be rolled back.
- **`toolkit.yaml`** — declarative metadata used by skill habitats that
  name this toolkit in `metadata.marcel-connectors`. Schema:

  ```yaml
  name: docker                  # must equal directory name
  description: Manage Docker containers on the home NUC
  provides:                     # documentation; must all be in <name>.* namespace
    - docker.list
    - docker.status
  requires:                     # what this toolkit needs to function
    credentials: [BANK_API_KEY]
    env: [DOCKER_HOST]
    files: [signing_key.pem]
    packages: [icloudpy]
  ```

  Without `toolkit.yaml`, the registered handlers still work, but **no
  skill habitat can name it in `marcel-connectors`** —
  `get_toolkit_metadata()` returns `None`, which the skill loader treats
  as "requirements not met" → the user is shown `SETUP.md`.

### 2. Skill habitat — `<MARCEL_ZOO_DIR>/skills/<name>/`

- **`SKILL.md`** — teaches the agent how to call the toolkit. Frontmatter
  is agentskills.io-conformant (`name` == dir name + `description`); the
  link to a toolkit lives in the spec-legal `metadata:` map as
  `metadata.marcel-connectors: <toolkit>` (comma-separated for several).
  Skill-local requirements go in `metadata.marcel-requires-credentials`
  / `metadata.marcel-requires-env`, but for any skill that calls
  `toolkit(id="...")`, prefer `marcel-connectors` so the credential/env
  list lives in one place (the toolkit's `toolkit.yaml`). The pre-v2
  top-level `depends_on:` / `requires:` keys are still accepted (migrated
  with a deprecation warning) but new skills use `metadata.marcel-*`.
- **`SETUP.md`** — shown instead of `SKILL.md` when **either** the skill's
  own `marcel-requires-*` OR any `marcel-connectors` toolkit's `requires:`
  is unsatisfied. This is how Marcel conversationally onboards family
  members.

## Back-compat

During Phases 1–4 of the five-habitat-taxonomy migration
(ISSUE-3c1534 — archived at `$MARCEL_ADMIN_DIR/JIRA/archive/ISSUE-260422-3c1534-five-habitat-taxonomy.md`)
the kernel still accepts both `@register(...)` and the legacy
`integration.yaml` filename as aliases. Phase 5 drops them. New code
should always use `@marcel_tool` and `toolkit.yaml`.

## Why

A family member says *"I want you to read my iCloud calendar"*. Marcel
loads the `icloud` skill's docs:

- If credentials are configured → Marcel reads `SKILL.md`, calls
  `toolkit(id="icloud.list_events", ...)`, returns the events.
- If credentials are missing → Marcel reads `SETUP.md`, walks Alice
  through getting an app-specific password, stores it encrypted, and
  tries again.

A skill without `SETUP.md` means the agent silently fails when the
toolkit isn't configured. A handler without `SKILL.md` means the agent
doesn't know the toolkit exists. A `SKILL.md` whose `marcel-connectors`
toolkit has no `toolkit.yaml` falls into setup mode forever — easy to
miss in dev where the env var is exported in the shell.

## Checklist before closing

- [ ] New toolkit handler → toolkit habitat has `toolkit.yaml` AND a
      paired skill habitat with both `SKILL.md` and `SETUP.md` exists
- [ ] `provides:` in `toolkit.yaml` lists every `@marcel_tool`'d handler
      name and matches the namespace
- [ ] Renamed `@marcel_tool("...")` action → toolkit handler, `provides:`
      list, and `SKILL.md` examples all reflect the new name
- [ ] Removed action → handler, `provides:` entry, and `SKILL.md`
      instructions cleaned up; if the whole toolkit is gone, both
      habitats removed
- [ ] New `requires:` entry in `toolkit.yaml` → `SETUP.md` in the paired
      skill habitat walks the user through providing it
- [ ] New toolkit must not require changes to `tool.py`, `executor.py`,
      or `runner.py` — if it does, the abstraction is wrong (per
      Marcel's "self-contained habitats" principle)

## Enforcement

[.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md)
checks any diff touching `src/marcel_core/skills/` or zoo habitats for
mismatched pair updates.
