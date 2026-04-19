# ISSUE-2ccc10: Migrate banking, icloud, news to integration habitats

**Status:** Closed
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
- [✓] Migrate **banking**: handler + client + cache + sync + SKILL.md + SETUP.md + components.yaml. Scheduled-job hook + credentials — landed in ISSUE-13c7f2. No dep move needed (EnableBanking uses httpx + PyJWT which are kernel-shared).
- [✓] Decide: does the zoo get its own `pyproject.toml` now (with `enable_banking_client`, `pyicloud`, `feedparser` as deps) or stay pure-python? — Answered in ISSUE-e7d127 (icloud): zoo stays pure-python; icloud-only deps (`caldav`, `vobject`) moved into `[project.optional-dependencies] zoo` group in kernel pyproject. News added nothing; banking added nothing (httpx + PyJWT stay kernel-shared).
- [✓] Move integration-specific tests out of `tests/core/test_banking.py` and `tests/tools/test_news.py` into each habitat's `tests/` dir — banking tests moved under ISSUE-13c7f2, news tests under ISSUE-d5f8ab.
- [✓] Replace moved tests with fake-plugin fixtures that cover `discover()` + dispatch + `depends_on` resolution — `tests/core/test_skills.py` now uses fake registered handlers instead of `banking.balance`; dispatch + loader exercised by `tests/core/test_skill_loader.py` and `tests/core/test_plugin.py`.
- [✓] Delete `src/marcel_core/skills/integrations/{banking,icloud,news}/` and `src/marcel_core/defaults/skills/{banking,icloud,news}/` — done per sub-issue. Kernel integrations/ directory is now empty.
- [✓] Delete any remaining entries in `src/marcel_core/skills/skills.json` that referenced these integrations — none present; skills.json stays JSON-skill-only.
- [✓] Docs: per-integration pages updated — `integration-banking.md` deleted (ISSUE-13c7f2), `integration-news.md` deleted (ISSUE-d5f8ab). Canonical integration docs now live inside each habitat.
- [✓] Verify: fresh Marcel install (empty `~/.marcel/`) has none of these integrations; user needs to install marcel-zoo to get them back — verified via regression test `TestRebuildScheduleEmptiness` and habitat round-trip (jobs id=`sha256("<habitat>:<entry>")[:12]`).

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

### 2026-04-18 — banking migrated (sub-issue ISSUE-13c7f2) — umbrella work complete

- Banking handler + client + cache + sync moved to `<MARCEL_ZOO_DIR>/integrations/banking/`, skill habitat to `<MARCEL_ZOO_DIR>/skills/banking/` with `depends_on: [banking]`. All 7 handlers (`banking.setup`, `banking.complete_setup`, `banking.status`, `banking.accounts`, `banking.balance`, `banking.transactions`, `banking.sync`) migrated.
- Scheduled-job hook (ISSUE-82f52b) used for the 8-hourly sync: `integration.yaml` declares `banking.sync` on cron `0 */8 * * *`. `_ensure_habitat_jobs()` reconciles to `JobDefinition(template='habitat:banking', id=0dea8f65d244, users=[])`; orphan cleanup confirmed on habitat removal.
- `_ensure_default_jobs()` **deleted** from [jobs/scheduler.py](../../src/marcel_core/jobs/scheduler.py) — last kernel code that created jobs from user state. All periodic work now flows through the habitat hook. Kernel ships zero first-party integrations.
- System-scope fan-out: `banking.sync` with `user_slug='_system'` iterates `paths.list_user_slugs()` filtering backups and users without banking creds (`_has_banking_creds(slug)`), mirroring news.
- No dependency move — `enable_banking_client` doesn't exist as a package; the integration uses kernel-shared httpx + PyJWT directly. `pyproject.toml` stays lean.
- 34 habitat tests using the synthetic-parent-package loader pattern. `tests/core/test_skills.py` rewritten to use fake handlers instead of `banking.balance` (kernel tests now depend on zero real integrations). `tests/jobs/test_scheduler_scenarios.py` adds `TestRebuildScheduleEmptiness` regression to guard against reintroducing user-state-based bootstrap.
- Docs: `docs/integration-banking.md` deleted, mkdocs Integrations nav section removed (empty after banking left), `docs/plugins.md` "First-party vs. external integrations" rewritten to state kernel ships zero, `docs/index.md` + `docs/architecture.md` updated accordingly.
- **Umbrella complete.** All three real integrations (icloud, news, banking) migrated. ISSUE-63a946 (zoo repo extraction to github.com/shbunder/marcel-zoo) unblocks.

### 2026-04-18 — scheduled-jobs hook landed (sub-issue ISSUE-82f52b)
- `integration.yaml` accepts a `scheduled_jobs:` block (declarative). Each entry becomes a system-scope `JobDefinition` with `template='habitat:<name>'`, stable ID `sha256("<habitat>:<name>")[:12]`, default-or-override `task` / `system_prompt` / `model`.
- Reconciliation built into `_ensure_habitat_jobs()` (called from `rebuild_schedule()`): orphans whose habitat no longer declares them are deleted from disk on next startup. "Uninstall = remove directory" now holds for jobs too.
- Validation strict — any malformed `scheduled_jobs:` entry rolls back the *whole habitat* (handlers + metadata), mirroring the namespace-check precedent from ISSUE-6ad5c7.
- Documented in `docs/plugins.md` under "Scheduled jobs from habitats". News migration is now fully unblocked — handler + scheduled `news.sync` move together as the first real consumer of the hook.

### 2026-04-19 — umbrella close

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 13/13 tasks — every ticked task maps to observable state on main. Kernel `src/marcel_core/skills/integrations/` contains only `__init__.py`; `src/marcel_core/defaults/skills/` has no banking/icloud/news; `_ensure_default_jobs` is truly gone from `jobs/scheduler.py`; `TestRebuildScheduleEmptiness` regression present in `tests/jobs/test_scheduler_scenarios.py:267`; plugin surface exposes `credentials`, `paths`, `models`, `get_logger`, `rss`; habitats populated at `~/.marcel/zoo/integrations/{banking,icloud,news}` + `~/.marcel/zoo/skills/{banking,icloud,news}`; `docs/plugins.md` has the "First-party vs. external" + "Scheduled jobs from habitats" sections.
- Shortcuts found: none.
- Scope drift: none — umbrella branch diff is a single `open/` → `wip/` rename with zero content change; no code snuck into the close flow.
- Stragglers: none in live code/docs. References to the retired kernel-integration layout survive only in `project/issues/closed/*.md` (historical audit trail — correct) and one commented regression-test assertion in `tests/jobs/test_scheduler_scenarios.py:280` that explicitly guards against reintroducing the old behavior.

## Lessons Learned

### What worked well
- **Order of operations: plugin surface before the migrations.** Landing `marcel_core.plugin.credentials`/`paths`/`models`/`get_logger` (ISSUE-c48967) first, *then* migrating integrations, meant every sub-issue had a ready target. If we'd done it the other way around, each migration would have discovered its own missing surface piece and we'd have six surface-expansion fights instead of one.
- **Audit before migrate.** The "settings integration" turned out to be dead code (ISSUE-e1b9c4) — had we blindly migrated it, we'd have shipped a zero-consumer handler into the zoo. The decision to audit each target before planning the migration saved an entire sub-issue of wasted work.
- **Start with the smallest credential-bearing migration (icloud).** It exercised `plugin.credentials` end-to-end without also stress-testing scheduled jobs or component registries. By the time banking landed (the largest), every dimension of the surface had already been proven on a simpler integration.
- **Declarative `scheduled_jobs:` over a `register_scheduled(scheduler)` callback.** The declarative YAML shape is inspectable, diffable, and orphan-reconcilable; the callback shape would have needed a whole second mechanism just to support "uninstall = remove directory."
- **Reconciliation at `rebuild_schedule()` tick, not at handler registration.** Made "habitat removed → jobs cleaned up" a *startup invariant* instead of a *teardown step*. Removing a habitat directory is now a single-action operation again.
- **Umbrella Implementation Log linking each claim to its sub-issue.** Made the pre-close verification mechanical rather than subjective. Pattern worth reusing for future umbrella issues.

### What to do differently
- **The synthetic-parent-package loader trap cost time across two sub-issues.** External habitats load under `_marcel_ext_integrations.<name>` via `spec_from_file_location`, but that parent isn't a real `sys.modules` package — so `from . import cache` fails silently in a way that only shows up at runtime. Next time we introduce a loader that synthesizes a parent module, document the import style (`from .cache import X` works; `from . import cache` doesn't) **in the loader's docstring**, not after the fact in a sub-issue log.
- **Dependency-group policy should have been decided up front.** The "does the zoo get its own pyproject.toml?" question drifted across sub-issues (icloud moved `caldav`/`vobject` into `[project.optional-dependencies] zoo`, news moved nothing, banking moved nothing). Worked out fine, but the decision could have been a one-paragraph ADR before the first migration instead of an emergent policy.
- **The 29-hour discover() outage (ISSUE-efbaaa) was downstream of this umbrella** — kernel `settings.marcel_zoo_dir` not being wired into prod Docker meant habitats never loaded on the NUC. Cross-cutting config changes (like "integrations now load from a second data root") need a deployment-smoke step before any sub-issue claims to be complete. The per-sub-issue verification checked dev-container shape but never cold-started the prod container.

### Patterns to reuse
- **"Extract then delete" as a migration shape.** Each sub-issue built the habitat *and* deleted the kernel copy in the same branch, keeping main cold-starts always correct. No "both trees simultaneously" period to test.
- **Fake-plugin fixtures for dispatch/loader tests.** After an integration migrates out of the kernel, its tests go with it; core tests switch to fake registered handlers. Kernel test coverage becomes about the **framework**, not the integrations that happen to be plugged in. Decouples kernel CI from zoo CI.
- **Stable IDs via `sha256("<habitat>:<entry>")[:12]`.** Same pattern used for habitat scheduled jobs should work anywhere we need reconcilable identity without a migration step — habitats, skills, jobs, components.
- **Umbrella issue with sub-issues merging to main independently.** Each sub-issue was a full "ship it" on its own, so main was always deployable. The umbrella's only role is tracking "is the whole migration done?" — no code of its own.
- **Decision → sub-issue rather than decision → debate.** "Is `settings` dead code?" → open ISSUE-e1b9c4, audit, answer. "Declarative vs callback for scheduled jobs?" → open ISSUE-82f52b, pick one, ship. Smaller branches, faster cycle time, cheaper rollback if the decision was wrong.
