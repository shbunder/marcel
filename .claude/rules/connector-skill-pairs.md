---
paths:
  - "src/marcel_core/skills/**/*.py"
  - "src/marcel_core/connectors/**/*.py"
  - "tests/connectors/**/*.py"
---

# Rule — connector / skill habitat pairs

Every Marcel connector habitat that carries user-visible behaviour normally
ships with a paired **skill habitat**: the connector holds the MCP server and
its per-user auth, the skill holds the agent prompting. Modifying one without
the other is **half-shipped work** — the pre-close-verifier treats it as scope
drift. (This rule succeeded the retired toolkit/skill pairing when the toolkit
habitat became the connector habitat, FEAT-260718-c232d9.)

Habitats live in [marcel-zoo](https://github.com/shbunder/marcel-zoo) (or any
directory pointed to by `MARCEL_ZOO_DIR`), not in this repo. The kernel ships
zero connector habitats and zero bundled skills — the loader surfaces live
under `src/marcel_core/connectors/` and `src/marcel_core/skills/` but the
habitats they discover live exclusively in the zoo.

## The two habitats

### 1. Connector habitat — `<MARCEL_ZOO_DIR>/connectors/<name>/`

`connector.yaml` (name == dir, transport http/stdio/inprocess, auth
oauth/api_key/none, optional `scheduled_jobs:`) plus, for bundled servers,
`server.py` exposing `mcp` (user-agnostic singleton) or `build(user_slug)`
(per-user data). Full schema: [docs/connectors.md](../../docs/connectors.md).
Optional `SETUP.md` carries admin-facing setup notes.

### 2. Skill habitat — `<MARCEL_ZOO_DIR>/skills/<name>/`

`SKILL.md` teaches the agent *when* to reach for the connector's tools —
named directly (e.g. `transactions(search="Colruyt")`), never through a
dispatcher. The link lives in the spec-legal `metadata:` map as
`metadata.marcel-connectors: <connector>` (comma-separated for several); a
name that resolves to no connector puts the skill into `SETUP.md` mode.

## Why

A family member says *"what did I spend at Colruyt?"*. Marcel loads the
`banking` skill via `load_capability`; the skill's `marcel-connectors`
bundles the banking connector's tools into the same step. If the user has not
linked the connector, the connector surfaces as a readable "needs setup"
capability and the conversation onboards them instead of failing silently.

A connector without a paired skill means the agent has tools it does not know
when to use. A skill naming a connector that does not exist falls into setup
mode forever — easy to miss in dev.

## Checklist before closing

- [ ] New connector → paired skill habitat exists with `SKILL.md` (and the
      connector has `SETUP.md` if it needs admin setup)
- [ ] Renamed/removed connector tool → `SKILL.md` examples updated
- [ ] New credential key in `auth.credential_keys` → onboarding path covers it
- [ ] New connector must not require changes to `runner.py`/`executor.py` —
      if it does, the abstraction is wrong (self-contained habitats)

## Enforcement

[.claude/agents/pre-close-verifier.md](../agents/pre-close-verifier.md)
checks any diff touching `src/marcel_core/connectors/`, `src/marcel_core/skills/`
or zoo habitats for mismatched pair updates.
