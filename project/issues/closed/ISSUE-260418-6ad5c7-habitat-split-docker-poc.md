# ISSUE-6ad5c7: Skill/integration habitat split + docker proof-of-concept

**Status:** Closed
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

- [✓] Define `integration.yaml` schema: `provides` (list of handler names), `requires` (credentials/env/files/packages, same keys as today's skill frontmatter), `description`. Document schema in `docs/plugins.md`.
- [✓] Add a loader for `integration.yaml` that reads alongside the `@register` decorator. The decorator provides the handler; the YAML provides the declarative metadata.
- [✓] Extend `depends_on:` frontmatter in SKILL.md. Loader resolves: for each dependency, look up the integration's `integration.yaml`, run its `requires:` check, short-circuit to SETUP.md if any fail.
- [✓] Update [skills/loader.py](../../src/marcel_core/skills/loader.py) `_check_requirements` to consult `depends_on` integrations instead of in-frontmatter `requires:` (but keep in-frontmatter `requires:` working for pure-markdown skills that don't depend on an integration — covered in ISSUE-bde0a1).
- [✓] Migrate docker: move handler code to `~/projects/marcel-zoo/integrations/docker/__init__.py`, author `integration.yaml` with `requires: env: [DOCKER_HOST]`. *(Habitats live under `MARCEL_ZOO_DIR` now, not `~/.marcel/`.)*
- [✓] Migrate docker skill: move SKILL.md to `~/projects/marcel-zoo/skills/docker/SKILL.md`, add `depends_on: [docker]`, drop the `requires:` block. Same for SETUP.md.
- [✓] Remove `docker` entries from [skills/skills.json](../../src/marcel_core/skills/skills.json).
- [✓] Remove `src/marcel_core/skills/integrations/docker/` and `src/marcel_core/defaults/skills/docker/` from source tree.
- [✓] Move [tests/tools/test_integration_tools.py](../../tests/tools/test_integration_tools.py) docker cases to `~/projects/marcel-zoo/integrations/docker/tests/` (or keep core-side tests that use a fake integration — decide during implementation). *(Decided: keep core-side. The integration-tool tests use generic mocked handlers, not docker-specific assertions, so nothing to move.)*
- [✓] Verify: fresh boot with empty `~/.marcel/` seeds nothing for docker (kernel no longer ships it); a user who wants docker copies the habitat in from marcel-zoo. *(Test coverage in `TestSkillDirsAndZooDiscovery` + `TestExternalDiscovery::test_unset_zoo_dir_is_noop` + `test_missing_integrations_dir_is_noop` exercises the zoo-unset / zoo-empty paths end-to-end.)*
- [✓] Docs: extend `docs/plugins.md` with the two habitat layouts + `depends_on` mechanics. Update [docs/skills.md](../../docs/skills.md) to reflect split.

## Relationships

- Depends on: ISSUE-3c87dd (plugin API + widened discovery)
- Blocks: ISSUE-2ccc10 (banking/icloud/news/settings migrations follow this pattern)
- Blocks: ISSUE-bde0a1 (pure-markdown skill habitats share the skill layout)

## Implementation Log

### 2026-04-18 - LLM Implementation
**Action**: Shipped the habitat split end-to-end and migrated docker as the proof of concept.
**Files Modified**:
- `src/marcel_core/config.py` — added `marcel_zoo_dir` setting (no default) + `zoo_dir` Path property.
- `src/marcel_core/skills/integrations/__init__.py` — added `IntegrationMetadata` dataclass + `_metadata` registry, `get_integration_metadata()`, `list_integrations()`, `_load_integration_metadata()`. Repointed `_discover_external` to `settings.zoo_dir`.
- `src/marcel_core/skills/loader.py` — added `_skill_dirs()`, `_check_depends_on()`, `_normalize_depends_on()`. `_load_skill_dir` now extracts `depends_on` and aggregates credentials across inline `requires:` + every depended-on integration. `load_skills()` and `_find_skill_dir()` walk both zoo and data-root sources, with data-root winning on collision.
- `src/marcel_core/skills/skills.json` — emptied (docker entries removed; `@register` auto-discovery handles registration).
- `src/marcel_core/skills/integrations/docker/` — deleted.
- `src/marcel_core/defaults/skills/docker/` — deleted.
- `src/marcel_core/plugin/__init__.py` — refresh docstring path to `<MARCEL_ZOO_DIR>/...`.
- `pyproject.toml` — drop coverage exclusion for the deleted docker handler.
- `tests/core/test_plugin.py` — `isolated_registry` fixture extended to reset `_metadata` too; added `TestIntegrationMetadata` (6 cases covering valid YAML, missing yaml, invalid YAML, name mismatch, namespace violations, unknown requires keys).
- `tests/core/test_skill_loader.py` — added `isolated_metadata` fixture, `TestNormalizeDependsOn` (6 cases), `TestCheckDependsOn` (5 cases), `TestLoadSkillDirDependsOn` (4 cases), `TestSkillDirsAndZooDiscovery` (5 cases) — covers normalization, integration metadata resolution, SETUP fallback, credential aggregation, zoo+data ordering, and collision precedence.
- `docs/plugins.md` — documented `MARCEL_ZOO_DIR` opt-in, the integration.yaml schema (name/description/provides/requires), validation rules.
- `docs/skills.md` — described two-source loading + the `depends_on:` linkage to integration.yaml; updated the python-integration walkthrough to the habitat layout.
- `.claude/rules/integration-pairs.md` — rewrote around the two-habitat model.
- `.claude/agents/code-reviewer.md` — refreshed orientation bullet.
- `~/projects/marcel-zoo/` (separate repo) — initial layout: `README.md`, `.gitignore`, `integrations/docker/{__init__.py,integration.yaml}`, `skills/docker/{SKILL.md,SETUP.md}`.

**Commands Run**: `uv run pytest tests/core/test_skill_loader.py tests/core/test_plugin.py -x -q` (all pass), `make check` via pre-commit hooks on every commit (1505 passed, 91.87% coverage).
**Result**: Success — all tests pass; full suite + coverage gate green; docker now opt-in via `MARCEL_ZOO_DIR=~/projects/marcel-zoo`.
**Next**: ISSUE-2ccc10 — same pattern for banking, icloud, news, settings.

**Reflection** (via pre-close-verifier):
- Verdict: REQUEST CHANGES → addressed
- Coverage: 11/11 tasks addressed
- Shortcuts found: none
- Scope drift: none
- Stragglers: `docs/architecture.md:65` still listed `integrations/docker/` in the kernel module-layout tree → fixed in commit `bca946c`. The verifier separately confirmed no `request_restart()` bypass, no data-boundary violations, and that the habitat-namespace rollback in `_load_external_integration` cannot leak partial state into either `_registry` or `_metadata`.

## Lessons Learned

### What worked well
- Splitting the work into 6 small `🔧 impl:` commits made each step independently reviewable and kept the formatter happy commit-by-commit.
- The "data_dir wins on collision" rule was a clean way to preserve user customizations while letting marcel-zoo ship sane defaults.
- Aggregating `cred_keys` across inline `requires:` + every `depends_on:` integration (rather than picking one source) means the system-prompt credential auto-injection list is always complete, even for hybrid skills.
- Treating "metadata not registered" as "requirements not met" (so the user sees SETUP.md) is a better default than raising or silently allowing the SKILL.md through — it surfaces zoo-config mistakes the moment the user invokes the skill.

### What to do differently
- Should have set up `~/projects/marcel-zoo/`'s git config (or used the global one) **before** the first commit there — the "Author identity unknown" error wasted a round-trip.
- The pre-commit formatter post-processed file changes after the commit returned, leaving the working tree dirty and forcing a tiny formatter-cleanup commit (fd39ef5). Worth investigating whether the format step can run *before* commit or whether the longer string literals can be kept ≤120 chars from the start.
- The `_find_skill_dir()` precedence (data_dir wins) duplicates the order logic in `load_skills()`. Worth extracting a shared "resolve skill dir by name" helper if a third caller emerges.

### Patterns to reuse
- **Habitat = directory + manifest YAML.** Integration habitats: `__init__.py` + `integration.yaml`. Skill habitats: `SKILL.md` + `SETUP.md` + optional `components.yaml`. Same shape will work for channels (channel.yaml), jobs (job.yaml), agents (agent.yaml).
- **`depends_on:` resolution via metadata registry.** The pattern of "skill says it needs X; loader looks up X's metadata and runs its requires-check" is reusable for any habitat type that depends on another. No hard-coded credential lists, no duplication.
- **Test fixture per registry.** `isolated_registry` for handlers, `isolated_metadata` for integration metadata — small, scoped fixtures save/restore the global registries so tests can stomp freely. Reuse for any future module-level registry.
- **Opt-in env var, no defaults.** `MARCEL_ZOO_DIR` unset → silent no-op. Forces explicit configuration in `.env.local`, keeps fresh installs minimal, avoids the "magic default path" bug class.
