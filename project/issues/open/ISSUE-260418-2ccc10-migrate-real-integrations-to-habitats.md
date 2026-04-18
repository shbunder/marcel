# ISSUE-2ccc10: Migrate banking, icloud, news, settings to integration habitats

**Status:** Open
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "I want to move code related to integrations to .marcel/integrations (...) everything in .marcel will become part of 'marcel-zoo'."

**Resolved intent:** With the habitat pattern proven on docker (ISSUE-6ad5c7), extract the four remaining first-party integrations — **banking**, **icloud**, **news**, **settings** — into the zoo layout. This is where the `marcel_core.plugin` API surface is stress-tested: these integrations need credentials, user paths, storage helpers, and (for `settings`) harness primitives. If the plugin surface can't cover them without leaking internals, we find that out here and extend the surface explicitly rather than letting zoo code spelunk.

## Description

Each of the four integrations moves from its two-tree home (code in [src/marcel_core/skills/integrations/<name>/](../../src/marcel_core/skills/integrations/), docs in [src/marcel_core/defaults/skills/<name>/](../../src/marcel_core/defaults/skills/)) into two habitats:

- `~/.marcel/integrations/<name>/` — handler code, client code, caches, `integration.yaml`, tests
- `~/.marcel/skills/<name>/` — SKILL.md with `depends_on: [<name>]`, SETUP.md, components.yaml, tests

The **plugin API surface grows** to cover what they actually need (and nothing more):

| Need | Plugin surface addition |
|---|---|
| Credential read/write per-user | `marcel_core.plugin.credentials` — `load(user_slug) -> dict`, `save(user_slug, key, value)` |
| Per-user data path for cache files | `marcel_core.plugin.paths` — `user_dir(slug) -> Path`, `artifact_dir(slug) -> Path` |
| Logger with the plugin's name | `marcel_core.plugin.get_logger(__name__)` |
| Settings integration — model registry access | `marcel_core.plugin.models` — `all_models()`, `default_model()`, `set_channel_model()` |

Every other import reaches past the surface and is a bug. `banking` today imports `marcel_core.storage.credentials` directly; that becomes `marcel_core.plugin.credentials` after this issue.

Banking is the largest — its `cache.py`, `client.py`, and `sync.py` all travel together. The scheduled sync task currently registered in [jobs/scheduler.py](../../src/marcel_core/jobs/scheduler.py) needs a new registration hook in the integration habitat (a zoo integration should be able to contribute a periodic job without touching kernel code — surface grows again if needed).

The four integrations' tests move with the code. Core-side tests that currently exercise these integrations for coverage get replaced with fake-plugin fixtures that test the **dispatch and loader**, not the real integrations.

## Tasks

- [ ] Extend `marcel_core.plugin` with `credentials`, `paths`, `get_logger`, `models` submodules. Every addition documented in `docs/plugins.md`.
- [ ] Design the "integration contributes a periodic job" hook. Options: (a) `integration.yaml` declares `scheduled_jobs: [...]`, kernel scheduler reads them; (b) handler exports a `register_scheduled(scheduler)` function called at discovery. Pick one.
- [ ] Migrate **settings** first (smallest, no credentials): `~/.marcel/integrations/settings/` + `~/.marcel/skills/settings/`.
- [ ] Migrate **icloud**: handler + client + SKILL.md + SETUP.md. Credentials via plugin surface.
- [ ] Migrate **news**: handler + cache + sync + SKILL.md + SETUP.md + `feeds.yaml` resource. Scheduled-job hook required.
- [ ] Migrate **banking**: handler + client + cache + sync + SKILL.md + SETUP.md + components.yaml. Scheduled-job hook + credentials + EnableBanking dep.
- [ ] Decide: does the zoo get its own `pyproject.toml` now (with `enable_banking_client`, `pyicloud`, `feedparser` as deps) or stay pure-python? If its own pyproject, Docker image needs to `pip install` the zoo after clone. Document the decision.
- [ ] Move integration-specific tests out of [tests/core/test_banking.py](../../tests/core/test_banking.py), [tests/tools/test_news.py](../../tests/tools/test_news.py), [tests/core/test_settings.py](../../tests/core/test_settings.py) into each habitat's `tests/` dir.
- [ ] Replace moved tests with fake-plugin fixtures that cover `discover()` + dispatch + `depends_on` resolution.
- [ ] Delete `src/marcel_core/skills/integrations/{banking,icloud,news,settings}/` and `src/marcel_core/defaults/skills/{banking,icloud,news,settings}/`.
- [ ] Delete any remaining entries in [skills/skills.json](../../src/marcel_core/skills/skills.json) that referenced these integrations.
- [ ] Docs: per-integration pages [docs/integration-banking.md](../../docs/integration-banking.md), [docs/integration-news.md](../../docs/integration-news.md) — update to reflect habitat layout, or move the canonical docs to live inside the habitats themselves.
- [ ] Verify: fresh Marcel install (empty `~/.marcel/`) has none of these integrations; user needs to install marcel-zoo to get them back.

## Relationships

- Depends on: ISSUE-6ad5c7 (habitat conventions + docker POC)
- Blocks: ISSUE-63a946 (zoo repo extraction — can't happen until all first-party integrations have moved)

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
