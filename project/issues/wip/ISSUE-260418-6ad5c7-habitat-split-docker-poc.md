# ISSUE-6ad5c7: Skill/integration habitat split + docker proof-of-concept

**Status:** WIP
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "The zoo habitats are jobs, integrations, skills, channels. They contain the markdown files and code required to specifically run them (...) I would keep the split between skills and integrations. There are indeed skills that depend on integrations (and we can make that explicit) but they should stay cleanly separated."

**Resolved intent:** Define the marcel-zoo habitat conventions for two of the four habitat types — **integration** (external-system code: API clients, handlers, requires-declaration) and **skill** (agent-facing teaching: SKILL.md, SETUP.md, components.yaml, `depends_on` link) — as cleanly separated directory layouts. Prove the model end-to-end with `docker`, the smallest, least-coupled integration in the tree. No credentials, no external SDK, no storage coupling — if it works for docker, the pattern is sound.

## Description

Two conventions get nailed down in this issue:

**Integration habitat** at `<data_root>/integrations/<name>/`:
```
<name>/
├── __init__.py        # @register("<name>.*") async handlers
├── <client>.py        # extra code as needed
├── integration.yaml   # provides: [<name>.*], requires: {credentials/env/files/packages}, description
└── tests/
```

**Skill habitat** at `<data_root>/skills/<name>/`:
```
<name>/
├── SKILL.md           # frontmatter: depends_on: [<integration-name>, ...]
├── SETUP.md           # fallback shown when depended-on integration's requires are unmet
├── components.yaml    # optional
└── tests/             # optional — tests teaching-material correctness, mocks the integration
```

Per [.claude/rules/data-boundaries.md](../../.claude/rules/data-boundaries.md), `requires:` moves from the skill frontmatter (where it is today) to `integration.yaml` — the integration is the layer that actually needs credentials/env/files. The skill's `depends_on:` points at the integration; the loader resolves SKILL.md vs SETUP.md by checking the declared integration's requirements.

The docker integration migrates from two trees into two habitats:

- `src/marcel_core/skills/integrations/docker/` → `~/.marcel/integrations/docker/`
- `src/marcel_core/defaults/skills/docker/` → `~/.marcel/skills/docker/`

The old JSON entries in [skills/skills.json](../../src/marcel_core/skills/skills.json) for `docker.*` are removed (integrations auto-register via `@register`). Skill loader and integration loader each read from their respective data-root dir.

Depends on ISSUE-3c87dd shipping first — it provides the `marcel_core.plugin` surface and the widened `discover()`.

## Tasks

- [ ] Define `integration.yaml` schema: `provides` (list of handler names), `requires` (credentials/env/files/packages, same keys as today's skill frontmatter), `description`. Document schema in `docs/plugins.md`.
- [ ] Add a loader for `integration.yaml` that reads alongside the `@register` decorator. The decorator provides the handler; the YAML provides the declarative metadata.
- [ ] Extend `depends_on:` frontmatter in SKILL.md. Loader resolves: for each dependency, look up the integration's `integration.yaml`, run its `requires:` check, short-circuit to SETUP.md if any fail.
- [ ] Update [skills/loader.py](../../src/marcel_core/skills/loader.py) `_check_requirements` to consult `depends_on` integrations instead of in-frontmatter `requires:` (but keep in-frontmatter `requires:` working for pure-markdown skills that don't depend on an integration — covered in ISSUE-bde0a1).
- [ ] Migrate docker: move handler code to `~/.marcel/integrations/docker/__init__.py`, author `integration.yaml` with `requires: env: [DOCKER_HOST]`.
- [ ] Migrate docker skill: move SKILL.md from `defaults/skills/docker/` to `~/.marcel/skills/docker/SKILL.md`, add `depends_on: [docker]`, drop the `requires:` block (now lives in integration.yaml). Same for SETUP.md.
- [ ] Remove `docker` entries from [skills/skills.json](../../src/marcel_core/skills/skills.json).
- [ ] Remove `src/marcel_core/skills/integrations/docker/` and `src/marcel_core/defaults/skills/docker/` from source tree.
- [ ] Move [tests/tools/test_integration_tools.py](../../tests/tools/test_integration_tools.py) docker cases to `~/.marcel/integrations/docker/tests/` (or keep core-side tests that use a fake integration — decide during implementation).
- [ ] Verify: fresh boot with empty `~/.marcel/` seeds nothing for docker (kernel no longer ships it); a user who wants docker copies the habitat in from marcel-zoo.
- [ ] Docs: extend `docs/plugins.md` with the two habitat layouts + `depends_on` mechanics. Update [docs/skills.md](../../docs/skills.md) to reflect split.

## Relationships

- Depends on: ISSUE-3c87dd (plugin API + widened discovery)
- Blocks: ISSUE-2ccc10 (banking/icloud/news/settings migrations follow this pattern)
- Blocks: ISSUE-bde0a1 (pure-markdown skill habitats share the skill layout)

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
