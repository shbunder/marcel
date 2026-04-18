# ISSUE-2ccc10: Migrate banking, icloud, news to integration habitats

**Status:** Open
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "I want to move code related to integrations to .marcel/integrations (...) everything in .marcel will become part of 'marcel-zoo'."

**Resolved intent:** With the habitat pattern proven on docker (ISSUE-6ad5c7), extract the three remaining first-party integrations — **banking**, **icloud**, **news** — into the zoo layout. This is where the `marcel_core.plugin` API surface is stress-tested: these integrations need credentials, user paths, and storage helpers. If the plugin surface can't cover them without leaking internals, we find that out here and extend the surface explicitly rather than letting zoo code spelunk.

**Settings dropped (2026-04-18):** Originally a fourth migration target. Audit under ISSUE-e1b9c4 revealed `src/marcel_core/skills/integrations/settings.py` was vestigial dead code — the live settings surface is the marcel utility tool at `src/marcel_core/tools/marcel/settings.py`, not an integration handler. The dead handler was deleted in ISSUE-e1b9c4 rather than migrated. Count drops from 4 to 3.

## Description

Each of the three integrations moves from its two-tree home (code in [src/marcel_core/skills/integrations/<name>/](../../src/marcel_core/skills/integrations/), docs in [src/marcel_core/defaults/skills/<name>/](../../src/marcel_core/defaults/skills/)) into two habitats:

- `~/.marcel/integrations/<name>/` — handler code, client code, caches, `integration.yaml`, tests
- `~/.marcel/skills/<name>/` — SKILL.md with `depends_on: [<name>]`, SETUP.md, components.yaml, tests

The **plugin API surface grows** to cover what they actually need (and nothing more):

| Need | Plugin surface addition |
|---|---|
| Credential read/write per-user | `marcel_core.plugin.credentials` — `load(user_slug) -> dict`, `save(user_slug, key, value)` |
| Per-user data path for cache files | `marcel_core.plugin.paths` — `user_dir(slug) -> Path`, `artifact_dir(slug) -> Path` |
| Logger with the plugin's name | `marcel_core.plugin.get_logger(__name__)` |

Every other import reaches past the surface and is a bug. `banking` today imports `marcel_core.storage.credentials` directly; that becomes `marcel_core.plugin.credentials` after this issue.

Banking is the largest — its `cache.py`, `client.py`, and `sync.py` all travel together. The scheduled sync task currently registered in [jobs/scheduler.py](../../src/marcel_core/jobs/scheduler.py) needs a new registration hook in the integration habitat (a zoo integration should be able to contribute a periodic job without touching kernel code — surface grows again if needed).

The three integrations' tests move with the code. Core-side tests that currently exercise these integrations for coverage get replaced with fake-plugin fixtures that test the **dispatch and loader**, not the real integrations.

## Tasks

- [✓] Extend `marcel_core.plugin` with `credentials`, `paths`, `get_logger`, `models` submodules — landed in ISSUE-c48967. Every addition documented in `docs/plugins.md`.
- [✓] Audit the `settings` integration — turned out to be dead code; deleted in ISSUE-e1b9c4 rather than migrated.
- [✓] Design the "integration contributes a periodic job" hook. Options: (a) `integration.yaml` declares `scheduled_jobs: [...]`, kernel scheduler reads them; (b) handler exports a `register_scheduled(scheduler)` function called at discovery. Pick one. — landed in ISSUE-82f52b as **thick declarative** (every entry becomes a system-scope `JobDefinition` with `template='habitat:<name>'`, full agent-pipeline reuse, per-entry overrides for the LLM-creative case). News migration unblocked.
- [✓] Migrate **icloud** first (smallest remaining, no scheduled jobs): handler + client + SKILL.md + SETUP.md. Credentials via plugin surface — landed in ISSUE-e7d127.
- [✓] Migrate **news**: handler + cache + sync + SKILL.md + SETUP.md + `feeds.yaml` resource. Scheduled-job hook required — landed in ISSUE-d5f8ab.
- [ ] Migrate **banking**: handler + client + cache + sync + SKILL.md + SETUP.md + components.yaml. Scheduled-job hook + credentials + EnableBanking dep.
- [ ] Decide: does the zoo get its own `pyproject.toml` now (with `enable_banking_client`, `pyicloud`, `feedparser` as deps) or stay pure-python? If its own pyproject, Docker image needs to `pip install` the zoo after clone. Document the decision.
- [ ] Move integration-specific tests out of [tests/core/test_banking.py](../../tests/core/test_banking.py) and [tests/tools/test_news.py](../../tests/tools/test_news.py) into each habitat's `tests/` dir.
- [ ] Replace moved tests with fake-plugin fixtures that cover `discover()` + dispatch + `depends_on` resolution.
- [ ] Delete `src/marcel_core/skills/integrations/{banking,icloud,news}/` and `src/marcel_core/defaults/skills/{banking,icloud,news}/`.
- [ ] Delete any remaining entries in [skills/skills.json](../../src/marcel_core/skills/skills.json) that referenced these integrations.
- [ ] Docs: per-integration pages [docs/integration-banking.md](../../docs/integration-banking.md), [docs/integration-news.md](../../docs/integration-news.md) — update to reflect habitat layout, or move the canonical docs to live inside the habitats themselves.
- [ ] Verify: fresh Marcel install (empty `~/.marcel/`) has none of these integrations; user needs to install marcel-zoo to get them back.

## Relationships

- Depends on: ISSUE-6ad5c7 (habitat conventions + docker POC)
- Blocks: ISSUE-63a946 (zoo repo extraction — can't happen until all first-party integrations have moved)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

### 2026-04-18 — icloud migrated (sub-issue ISSUE-e7d127)
- icloud handler + client moved to `<MARCEL_ZOO_DIR>/integrations/icloud/`, skill habitat to `<MARCEL_ZOO_DIR>/skills/icloud/` with `depends_on: [icloud]`.
- Imports switched to `marcel_core.plugin.register` and `marcel_core.plugin.credentials.load(slug)`.
- `caldav` + `vobject` moved out of kernel `dependencies` into `[project.optional-dependencies] zoo` group (still installed by `uv sync --all-extras` in dev + Docker).
- Two remaining: news (scheduled-job hook needed), banking (largest — sync, cache, components.yaml, EnableBanking dep).

### 2026-04-18 — news migrated (sub-issue ISSUE-d5f8ab)
- Handler + cache + sync moved to `<MARCEL_ZOO_DIR>/integrations/news/`, skill habitat to `<MARCEL_ZOO_DIR>/skills/news/` with `depends_on: [news]`.
- First real consumer of the `scheduled_jobs:` hook (ISSUE-82f52b). `integration.yaml` declares `news.sync` on cron `0 6,18 * * *` (Europe/Brussels), ported verbatim from the existing user-created JOB.md. `_ensure_habitat_jobs()` reconciles to a system-scope `JobDefinition(template='habitat:news', id=6af525725b45)`; orphan cleanup on habitat removal verified.
- System-scope fan-out happens inside the handler: when called with `user_slug='_system'`, `news.sync` iterates `paths.list_user_slugs()` (filtering backup snapshots and `_system` itself) and syncs each user in turn.
- Plugin surface grew by one submodule: `marcel_core.plugin.rss` re-exports `fetch_feed` from `marcel_core.tools.rss`. One file, one symbol — same discipline as `credentials` / `paths`.
- No dependency move: news parses RSS/Atom through stdlib `xml.etree` wrapped by `marcel_core.tools.rss`; no `feedparser` import anywhere. Kernel `pyproject.toml` unchanged.
- External-habitat relative-import trap: external habitats load under `_marcel_ext_integrations.<name>` via `spec_from_file_location`, but that parent is not a real `sys.modules` package. `from . import cache` fails; `from .cache import <names>` (icloud's pattern) works.
- Only banking remains under ISSUE-2ccc10 before ISSUE-63a946 (zoo repo extraction) unblocks.

### 2026-04-18 — scheduled-jobs hook landed (sub-issue ISSUE-82f52b)
- `integration.yaml` accepts a `scheduled_jobs:` block (declarative). Each entry becomes a system-scope `JobDefinition` with `template='habitat:<name>'`, stable ID `sha256("<habitat>:<name>")[:12]`, default-or-override `task` / `system_prompt` / `model`.
- Reconciliation built into `_ensure_habitat_jobs()` (called from `rebuild_schedule()`): orphans whose habitat no longer declares them are deleted from disk on next startup. "Uninstall = remove directory" now holds for jobs too.
- Validation strict — any malformed `scheduled_jobs:` entry rolls back the *whole habitat* (handlers + metadata), mirroring the namespace-check precedent from ISSUE-6ad5c7.
- Documented in `docs/plugins.md` under "Scheduled jobs from habitats". News migration is now fully unblocked — handler + scheduled `news.sync` move together as the first real consumer of the hook.

## Lessons Learned
<!-- Filled in at close time. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
