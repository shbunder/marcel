# ISSUE-d5f8ab: Migrate news integration to a marcel-zoo habitat

**Status:** Closed
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "Migrate the news integration to the zoo habitat layout — first real consumer of the scheduled_jobs hook landed in ISSUE-82f52b. Sub-issue under ISSUE-2ccc10. Split the current dual-tree layout (code at src/marcel_core/skills/integrations/news/ — __init__.py handlers, cache.py, sync.py; docs + feeds.yaml at src/marcel_core/defaults/skills/news/) into two habitats under ~/.marcel/ via MARCEL_ZOO_DIR: (a) `<zoo>/integrations/news/` with integration.yaml (provides: news.sync + news.search + news.recent; declares scheduled_jobs: entry for news.sync on the existing cron cadence — check current deployment for the actual cron, fall back to every 4 hours if unset), __init__.py using @register from marcel_core.plugin, cache.py, sync.py, and feeds.yaml moved in; (b) `<zoo>/skills/news/` with SKILL.md (depends_on: [news]) + SETUP.md. Use marcel_core.plugin.paths.artifact_dir(slug) for the sqlite cache db path — currently hardcoded at data/users/{slug}/cache/news.db; use marcel_core.plugin.get_logger(__name__). Since news requires no credentials or external python deps beyond what's already in kernel (feedparser — check pyproject, may need to move to optional zoo deps like icloud did with caldav+vobject in ISSUE-e7d127), the credentials surface is not needed. Move all news-specific tests from tests/tools/test_news.py into <zoo>/integrations/news/tests/; any kernel-side coverage that was incidentally hitting news becomes a fake-plugin dispatch test that exercises the loader, not the real integration. Delete src/marcel_core/skills/integrations/news/ and src/marcel_core/defaults/skills/news/ afterward. Delete any news entry in src/marcel_core/skills/skills.json if present. Verify fresh Marcel install with empty ~/.marcel/ has no news integration — user has to install marcel-zoo to get it back. Confirm the scheduled_jobs: declaration produces a JobDefinition with template='habitat:news', system-scope (users=[]), stable sha256-derived id, and reconciles correctly on restart (orphan check if user uninstalls the habitat). Docs: update docs/integration-news.md to reflect habitat layout OR move canonical docs into the habitat's README — pick one and stay consistent with icloud precedent from ISSUE-e7d127. After this migration, only banking remains under ISSUE-2ccc10; banking is the next and last sub-issue."

**Resolved intent:** Second real zoo migration after icloud (ISSUE-e7d127) and the **first real consumer of the `scheduled_jobs:` hook** (ISSUE-82f52b). News is the right shape to prove both: three handlers (`news.sync`, `news.search`, `news.recent`), a SQLite cache, a `feeds.yaml` resource file, and a periodic sync that today runs via a user-created `JOB.md` or an ad-hoc invocation — after this issue, the sync job is declared *in* `integration.yaml` and reconciled by `_ensure_habitat_jobs()`. Unlike icloud, news has **no credentials** and **no external python deps** (`feedparser` is not used — parsing goes through `marcel_core.tools.rss.fetch_feed`, which wraps stdlib `xml.etree`). That makes the migration cleanly focused on two things: (1) proving the habitat-contributed job hook against a real integration, and (2) deciding the plugin-surface story for kernel tools like `tools.rss` that habitats need to reach — either add a re-export (`marcel_core.plugin.rss`) or document the narrow reach-past as acceptable. By the end of this issue, no `news` code or docs ship inside the kernel; a fresh `make serve` against an empty `MARCEL_ZOO_DIR` shows zero news handlers *and* zero `template='habitat:news'` jobs on disk; pointing `MARCEL_ZOO_DIR` at the zoo checkout brings both back.

## Description

The news integration today lives across two kernel locations:

- **Handler + cache + sync** — [src/marcel_core/skills/integrations/news/](../../src/marcel_core/skills/integrations/news/) with `__init__.py` (three `@register` handlers: `news.sync`, `news.search`, `news.recent`), `cache.py` (SQLite wrapper), `sync.py` (fetch-and-store loop).
- **Docs + resource** — [src/marcel_core/defaults/skills/news/](../../src/marcel_core/defaults/skills/news/) with `SKILL.md`, `feeds.yaml` (feed URLs × source names).

Both move to the zoo (`~/projects/marcel-zoo/`) and the kernel copies are deleted. The shape mirrors icloud (ISSUE-e7d127), with two net-new pieces:

1. **`scheduled_jobs:` entry in `integration.yaml`** — this is why the issue matters. News becomes the first real integration habitat to exercise the declarative-jobs hook landed in ISSUE-82f52b. The entry specifies `name: "News sync"`, a cron cadence (check current deployment — if an existing `news.sync` JOB.md on disk has a trigger.cron, port it verbatim; else default to `"0 */4 * * *"` — every four hours — matching the cadence the banking-sync default uses), and `handler: news.sync`. The kernel synthesises a system-scope `JobDefinition` with `template='habitat:news'`, stable ID `sha256("news:News sync")[:12]`, running under `_system`. On the next restart, `_ensure_habitat_jobs()` picks it up; on habitat uninstall (zoo removed), reconciliation deletes the orphan job from disk.
2. **Plugin-surface decision for `marcel_core.tools.rss`** — news imports `fetch_feed` from the kernel tool module. Every such reach-past is a bug per the ISSUE-2ccc10 rule, so either (a) re-export it at `marcel_core.plugin.rss.fetch_feed` and update the migrated `sync.py` to import from the plugin surface, or (b) explicitly document in `docs/plugins.md` that `marcel_core.tools.*` kernel tools are safe to import directly and the "no reach-past" rule applies only to storage / internals. **Recommendation: (a)** — keep the surface as the single stable import path for habitats, same discipline that paid off for `credentials` and `paths`. If (b) turns out simpler during implementation, document why in the Implementation Log.

**Code changes inside the moved files:**

- `from marcel_core.skills.integrations import register` → `from marcel_core.plugin import register` (in the new `__init__.py`)
- `from marcel_core.tools.rss import fetch_feed` → `from marcel_core.plugin.rss import fetch_feed` (in the new `sync.py`) — contingent on the surface decision above
- `from marcel_core.storage._root import data_root` (or any hardcoded `data/users/{slug}/cache/news.db`) → `from marcel_core.plugin import paths` + `paths.artifact_dir(slug) / "news.db"` (in the new `cache.py`)
- `logging.getLogger(__name__)` → `from marcel_core.plugin import get_logger` + `get_logger(__name__)` — consistent with icloud precedent
- Add `integration.yaml` declaring `name: news`, `description`, `provides: [news.sync, news.search, news.recent]`, `requires: {}` (no credentials), and the `scheduled_jobs:` block per the cron-cadence research above
- The SKILL.md frontmatter gains `depends_on: [news]`

**Plugin surface addition (if option (a) chosen):**

- `marcel_core/plugin/rss.py` — thin re-export of `fetch_feed` from `marcel_core.tools.rss`. One file, one symbol, following the `credentials.py`/`paths.py` pattern landed in ISSUE-c48967.

**Dependency move:**

- **None.** Confirmed via `grep feedparser pyproject.toml src/` — no match. News parses RSS/Atom through `marcel_core.tools.rss`, which uses stdlib `xml.etree.ElementTree` (hardened with the non-XML sniff from ISSUE-f74948). The kernel `pyproject.toml` stays untouched on this migration.

**Tests:**

- `tests/tools/test_news.py` contains the current news-specific tests. Move the ones that exercise `news.sync` / `news.search` / `news.recent` directly into `<zoo>/integrations/news/tests/test_handlers.py`, monkeypatching `fetch_feed` so the suite never hits live feeds. Retain any tests in `tests/tools/test_news.py` that are actually testing the kernel `rss` tool (not the news integration) — rename the file to `tests/tools/test_rss_news_fixtures.py` or consolidate them into the existing `tests/tools/test_rss.py`.
- Add a kernel-side "fake-plugin dispatch" test under `tests/jobs/test_habitat_jobs.py` (or a new file) that sets up a synthetic habitat with a single `scheduled_jobs:` entry, calls `_ensure_habitat_jobs()`, and asserts a `JobDefinition` with `template='habitat:<name>'` lands on disk with the right stable ID. This test already exists per ISSUE-82f52b — verify it still passes after the news deletion; if it relied on news as an accidental coverage source, refactor.

**Verification:**

- `make check` green at the 90% coverage gate after the kernel deletion. Because news was sizeable (`cache.py` ≈ 150 lines, `sync.py` ≈ 120 lines, three handlers), deleting it should *raise* coverage percentage; if it drops, investigate.
- Fresh start with `MARCEL_ZOO_DIR` unset: `marcel_core.skills.integrations.list_handlers()` (or equivalent registry inspector) shows zero news handlers; `JobScheduler.rebuild_schedule()` produces zero jobs with `template='habitat:news'`.
- Pointing `MARCEL_ZOO_DIR` at the zoo checkout brings the three handlers back, *and* the scheduled job appears on disk as a system-scope entry with the stable sha256-derived ID. Invoking `integration(id="news.sync")` dispatches into the zoo code.
- `grep -r 'marcel_core.skills.integrations.news\|defaults/skills/news' .` confirms only historical references (`project/issues/closed/`, this issue file) remain.
- Orphan reconciliation: with the habitat loaded, the scheduled job is on disk; remove the habitat (delete `<zoo>/integrations/news/`), restart, confirm `_ensure_habitat_jobs()` deletes the orphan. This is exactly the precedent the ISSUE-82f52b tests cover — this issue is the first real-integration proof.

**Why this is the right next sub-issue under ISSUE-2ccc10:** icloud proved the `credentials` / `paths` surface. Banking will exercise the same plus `scheduled_jobs:` plus the heaviest dep tree (`enable_banking_client`). News is in between: no credentials, no new deps, but the first real test of the scheduled-jobs hook. Landing news *before* banking de-risks the hook against a smaller blast radius before banking's extra complexity piles on.

## Tasks

- [✓] Audit the news habitat end-to-end against the plugin surface — enumerate every `marcel_core.*` import in `__init__.py`, `cache.py`, `sync.py`. Expected reach-past: `tools.rss.fetch_feed`, `storage._root.data_root` (or similar). Every other kernel import is either already on the plugin surface or must be added.
- [✓] Research current `news.sync` cron cadence — check any on-disk `JOB.md` named `news*.json|.md` (both dev and prod) and the last committed banking-sync cadence as reference. Decide the default `cron:` for the `scheduled_jobs:` entry; fall back to `"0 */4 * * *"` (every 4 hours) if no existing cadence is found. Document the choice in the Implementation Log.
- [✓] Decide: expose `fetch_feed` via `marcel_core.plugin.rss` (recommended, mirrors `credentials`/`paths`) vs. explicitly document `marcel_core.tools.*` kernel tools as safe direct imports for habitats. Write the decision + rationale in the Implementation Log.
- [✓] If option (a): add `src/marcel_core/plugin/rss.py` re-exporting `fetch_feed` from `marcel_core.tools.rss`. Update `docs/plugins.md` "What `marcel_core.plugin` exposes" table with the new submodule.
- [✓] Create `~/projects/marcel-zoo/integrations/news/{__init__.py, cache.py, sync.py, integration.yaml, feeds.yaml}` — imports rewritten to the plugin surface; `cache.py` uses `paths.cache_dir(slug) / "news.db"`; `feeds.yaml` moved in alongside.
- [✓] Add `integration.yaml`: `name: news`, `description`, `provides: [news.sync, news.search, news.recent]`, `requires: {}`, and a `scheduled_jobs:` block per the cadence research above.
- [✓] Create `~/projects/marcel-zoo/skills/news/{SKILL.md, SETUP.md}` — copy from `src/marcel_core/defaults/skills/news/`, add `depends_on: [news]` to the SKILL.md frontmatter. If no SETUP.md exists today, write one covering "news has no credentials but needs `feedparser`-free XML feeds — if a feed is broken see the logs".
- [✓] Add `~/projects/marcel-zoo/integrations/news/tests/test_news.py` — full cache + sync coverage, monkeypatching `fetch_feed` so the suite never hits live feeds. Seed a temp SQLite DB via `paths.cache_dir(tmp_slug)`.
- [✓] Move news-specific cases from `tests/tools/test_news.py` into the zoo's `tests/test_news.py`. What's left in the kernel (if anything) either belongs in `tests/tools/test_rss.py` (if it's actually testing the kernel rss tool) or is deleted.
- [✓] Delete `src/marcel_core/skills/integrations/news/` (both `__init__.py`, `cache.py`, `sync.py`).
- [✓] Delete `src/marcel_core/defaults/skills/news/` (both `SKILL.md`, `feeds.yaml`).
- [✓] Check `src/marcel_core/skills/skills.json` for any `news` entry — delete if present.
- [✓] Verify the `scheduled_jobs:` round-trip end-to-end: with the habitat loaded, `_ensure_habitat_jobs()` creates a `JobDefinition` with `template='habitat:news'`, `users=[]`, stable sha256-derived ID; on habitat removal, `_ensure_habitat_jobs()` deletes the orphan. Capture the result in the Implementation Log (this is the signature deliverable of the issue).
- [✓] Docs: delete `docs/integration-news.md` to reflect the habitat layout — follows the icloud precedent from ISSUE-e7d127 (canonical docs move out of the kernel, the zoo habitat is self-describing).
- [✓] Docs: update the "First-party vs. external integrations" section in `docs/plugins.md` to drop news from the kernel-bundled list and add it to the migrated set (mirroring the icloud edit in ISSUE-e7d127).
- [✓] Update `project/issues/open/ISSUE-260418-2ccc10-...md` — mark the news migration task `[✓]` and append an Implementation Log entry linking this issue. Flag that banking is now the only first-party migration remaining before ISSUE-63a946 (zoo repo extraction) can start.
- [✓] `make check` green at the 90% coverage gate.

## Relationships

- Depends on: ISSUE-6ad5c7 (habitat conventions — landed)
- Depends on: ISSUE-c48967 (`marcel_core.plugin` surface — landed)
- Depends on: ISSUE-82f52b (`scheduled_jobs:` hook — landed; this issue is the first real consumer)
- Follows precedent: ISSUE-e7d127 (icloud migration — credential surface + habitat layout proof)
- Part of: ISSUE-2ccc10 (umbrella tracker — closes the "Migrate news" task)
- Blocks: ISSUE-63a946 (zoo repo extraction — news was the last blocker alongside banking)

## Implementation Log

### 2026-04-18 — news migrated to zoo habitat

**`.python-version` pin.** Mid-migration the venv broke — `uv` resolved to a stale managed Python 3.13 path while the kernel still pins 3.12. Pinned `3.12.3` in a `.python-version` file at the repo root and rebuilt the venv. Unrelated to news proper but blocking on the same branch, so it shipped as its own `🔧 impl:` commit (`36f1dcf`) rather than being deferred to a follow-up.

**Plugin surface decision.** Option (a) — added `src/marcel_core/plugin/rss.py` as a thin re-export of `marcel_core.tools.rss.fetch_feed`. Same discipline as `credentials` / `paths`: one stable import path for habitats, no reach-past into kernel internals. The alternative (document kernel `tools.*` as safe) would have blurred the surface boundary for minimal gain.

**Cron cadence.** Current deployment has a user-created `JOB.md` at `~/.marcel/jobs/news-sync/JOB.md` with `trigger.cron = "0 6,18 * * *"` (Europe/Brussels). Ported verbatim into `integration.yaml` — twice daily rather than every-4-hours. The old user-scoped JOB on disk needs manual cleanup before the next restart so the new system-scope habitat job replaces it cleanly.

**System-scope fan-out.** The scheduler hardcodes `users=[]` for every `scheduled_jobs:` entry, so the handler is always invoked with `user_slug='_system'`. Implemented the fan-out inside `news.sync`: when called with the sentinel slug, iterate `paths.list_user_slugs()`, filter out backup snapshots (`*.backup-<n>`) and `_system` itself, and call `sync_feeds(slug)` for each. A direct invocation with a real slug (via the `integration` tool) syncs just that user.

**Storage path.** `cache.py` uses `paths.cache_dir(slug) / 'news.db'` via the plugin surface. The old hardcoded `data/users/{slug}/cache/news.db` is gone.

**Dependency check.** Confirmed via `grep feedparser pyproject.toml src/` — no match. News parses via stdlib `xml.etree` wrapped by `marcel_core.tools.rss`. No kernel pyproject changes needed.

**Scheduled-jobs round-trip verified.**
- `discover()` loads the news habitat; `_metadata['news'].scheduled_jobs` contains one `ScheduledJobSpec` (cron `0 6,18 * * *`, tz Europe/Brussels, handler `news.sync`, notify `on_failure`, channel `telegram`).
- `_ensure_habitat_jobs()` materialises `JobDefinition(id='6af525725b45', template='habitat:news', users=[], trigger.cron='0 6,18 * * *', trigger.timezone='Europe/Brussels')`.
- Stable ID — `sha256("news:News sync")[:12] == 6af525725b45` — matches across restarts.
- Orphan cleanup verified by clearing `_metadata['news']` and re-running `_ensure_habitat_jobs()`: the habitat job is deleted from disk. "Uninstall = remove directory" holds for jobs.

**External-habitat import trap.** The external loader registers habitats as `_marcel_ext_integrations.<name>` via `spec_from_file_location` with `submodule_search_locations=[pkg_dir]`, but the parent package `_marcel_ext_integrations` is not a real package in `sys.modules`. Module-level `from . import cache` resolves the parent and fails with `ModuleNotFoundError: No module named '_marcel_ext_integrations'`. Rewrote to `from .cache import <names>` — the pattern icloud already uses. Same fix for `sync.py`. The habitat test loader synthesises a parent package (`types.ModuleType(_PKG)` with `__path__`) so the same relative imports resolve inside the test context.

**Kernel test retargeting.** `tests/core/test_skills.py` had seven assertions hardcoded to `news.search` / `news.recent`. Swapped to `banking.balance` / `banking.sync` (the only remaining in-kernel integration). News handlers are now exercised by the habitat's own test suite (`integrations/news/tests/test_news.py`, 21 tests).

**`make check` — 91.99% coverage**, 1545 tests pass. Up from pre-migration baseline (news deletion didn't break the gate).

**Docs.** Followed icloud precedent — deleted `docs/integration-news.md` entirely rather than stub-pointing at the habitat. Removed from `mkdocs.yml` nav. `docs/index.md` "Adding a new skill or integration" row now references banking as the in-kernel reference and the zoo habitats for external examples. `docs/plugins.md` documents the new `marcel_core.plugin.rss` submodule and moves news into the "Migrated so far" list.

**Reflection** (via inline verification — see straggler grep + round-trip test above, pre-close-verifier invoked separately):
- Coverage: all 17 tasks addressed
- Shortcuts found: none
- Scope drift: none (zoo README rewrite was committed separately in the zoo repo — orthogonal polish, not bundled with this migration)
- Stragglers: none in docs/ or src/; only historical references in `project/issues/closed/` and this issue file itself

## Lessons Learned

### What worked well

- **Plugin re-export stayed the right call.** Option (a) for `marcel_core.plugin.rss` was the right default; it kept the "habitats only import from `marcel_core.plugin`" rule intact without adding accidental surface. The one-file `rss.py` re-export pattern is cheap to repeat for the next kernel tool a habitat needs.
- **Scheduled-jobs reconciliation behaved exactly as the ISSUE-82f52b tests promised.** The stable-ID derivation (`sha256("<habitat>:<name>")[:12]`) made the round-trip test a one-liner, and the orphan path "just worked" — clear `_metadata['news']`, re-run `_ensure_habitat_jobs()`, job gone.
- **Handler fan-out on `user_slug='_system'` is a clean pattern.** It localises the "this job is system-scope but the work is per-user" concern inside the handler, where the filtering rules (skip backups, skip `_system` itself) naturally live next to the user-slug listing. The scheduler stays dumb — it doesn't know about users at all for habitat-declared jobs.

### What to do differently

- **Default to `from .module import name` in habitat code from day one.** The `from . import module` pattern works inside real packages but fails under the external-habitat loader because `_marcel_ext_integrations` is not a real `sys.modules` entry. Paid for this debug twice (once in the main code, once in the test loader). The icloud precedent already used the right pattern — should have copied it verbatim instead of rediscovering the constraint.
- **Surface test-loader ergonomics earlier.** The first version of `test_news.py` needed a monkeypatch (`sync.cache = cache`) because I loaded the files as top-level modules. Synthesising a parent package with `types.ModuleType` and `__path__` is the cleaner pattern — worth baking into a habitat-test fixture if a third habitat needs it.

### Patterns to reuse

- **Plugin re-export for kernel tools that habitats need** — one file, one symbol, one `__all__`. Cheap.
- **Stable-ID sha256 prefix** for any "declarative config → reconciled state" hook. The orphan-cleanup story collapses to "re-run reconcile after removing the source."
- **Synthetic parent package in `importlib` tests** — `types.ModuleType(name)` + `.__path__ = [dir]` + `sys.modules[name] = parent`, then load children as `{parent}.{child}` via `spec_from_file_location`. Makes habitat unit tests independent of the real kernel loader.
- **Fan-out inside the handler on `_system` slug** — when the scheduler dispatches a system-scope job but the actual work is per-user, the handler owns the iteration. Keeps the scheduler generic.
