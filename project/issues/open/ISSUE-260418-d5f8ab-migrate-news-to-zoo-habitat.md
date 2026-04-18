# ISSUE-d5f8ab: Migrate news integration to a marcel-zoo habitat

**Status:** Open
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

- [ ] Audit the news habitat end-to-end against the plugin surface — enumerate every `marcel_core.*` import in `__init__.py`, `cache.py`, `sync.py`. Expected reach-past: `tools.rss.fetch_feed`, `storage._root.data_root` (or similar). Every other kernel import is either already on the plugin surface or must be added.
- [ ] Research current `news.sync` cron cadence — check any on-disk `JOB.md` named `news*.json|.md` (both dev and prod) and the last committed banking-sync cadence as reference. Decide the default `cron:` for the `scheduled_jobs:` entry; fall back to `"0 */4 * * *"` (every 4 hours) if no existing cadence is found. Document the choice in the Implementation Log.
- [ ] Decide: expose `fetch_feed` via `marcel_core.plugin.rss` (recommended, mirrors `credentials`/`paths`) vs. explicitly document `marcel_core.tools.*` kernel tools as safe direct imports for habitats. Write the decision + rationale in the Implementation Log.
- [ ] If option (a): add `src/marcel_core/plugin/rss.py` re-exporting `fetch_feed` from `marcel_core.tools.rss`. Update `docs/plugins.md` "What `marcel_core.plugin` exposes" table with the new submodule.
- [ ] Create `~/projects/marcel-zoo/integrations/news/{__init__.py, cache.py, sync.py, integration.yaml, feeds.yaml}` — imports rewritten to the plugin surface; `cache.py` uses `paths.artifact_dir(slug) / "news.db"`; `feeds.yaml` moved in alongside.
- [ ] Add `integration.yaml`: `name: news`, `description`, `provides: [news.sync, news.search, news.recent]`, `requires: {}`, and a `scheduled_jobs:` block per the cadence research above.
- [ ] Create `~/projects/marcel-zoo/skills/news/{SKILL.md, SETUP.md}` — copy from `src/marcel_core/defaults/skills/news/`, add `depends_on: [news]` to the SKILL.md frontmatter. If no SETUP.md exists today, write one covering "news has no credentials but needs `feedparser`-free XML feeds — if a feed is broken see the logs".
- [ ] Add `~/projects/marcel-zoo/integrations/news/tests/test_handlers.py` — at least one test per handler (`sync`, `search`, `recent`), monkeypatching `fetch_feed` so the suite never hits live feeds. Seed a temp SQLite DB via `paths.artifact_dir(tmp_slug)`.
- [ ] Move news-specific cases from `tests/tools/test_news.py` into the zoo's `tests/test_handlers.py`. What's left in the kernel (if anything) either belongs in `tests/tools/test_rss.py` (if it's actually testing the kernel rss tool) or is deleted.
- [ ] Delete `src/marcel_core/skills/integrations/news/` (both `__init__.py`, `cache.py`, `sync.py`).
- [ ] Delete `src/marcel_core/defaults/skills/news/` (both `SKILL.md`, `feeds.yaml`).
- [ ] Check `src/marcel_core/skills/skills.json` for any `news` entry — delete if present.
- [ ] Verify the `scheduled_jobs:` round-trip end-to-end: with the habitat loaded, `JobScheduler.rebuild_schedule()` creates a `JobDefinition` with `template='habitat:news'`, `users=[]`, stable sha256-derived ID; on habitat removal, `_ensure_habitat_jobs()` deletes the orphan. Capture the result in the Implementation Log (this is the signature deliverable of the issue).
- [ ] Docs: update `docs/integration-news.md` to reflect the habitat layout — follow the icloud precedent from ISSUE-e7d127. If icloud moved the canonical docs into the habitat README, do the same for news; if icloud kept `docs/integration-icloud.md` and just updated it, do likewise.
- [ ] Docs: update the "First-party vs. external integrations" section in `docs/plugins.md` if it lists news among the kernel-bundled set (mirroring the icloud edit in ISSUE-e7d127).
- [ ] Update `project/issues/open/ISSUE-260418-2ccc10-...md` — mark the news migration task `[✓]` and append an Implementation Log entry linking this issue. Flag that banking is now the only first-party migration remaining before ISSUE-63a946 (zoo repo extraction) can start.
- [ ] `make check` green at the 90% coverage gate.

## Relationships

- Depends on: ISSUE-6ad5c7 (habitat conventions — landed)
- Depends on: ISSUE-c48967 (`marcel_core.plugin` surface — landed)
- Depends on: ISSUE-82f52b (`scheduled_jobs:` hook — landed; this issue is the first real consumer)
- Follows precedent: ISSUE-e7d127 (icloud migration — credential surface + habitat layout proof)
- Part of: ISSUE-2ccc10 (umbrella tracker — closes the "Migrate news" task)
- Blocks: ISSUE-63a946 (zoo repo extraction — news was the last blocker alongside banking)

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
