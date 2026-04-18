# ISSUE-13c7f2: Migrate banking integration to a marcel-zoo habitat

**Status:** Open
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "Migrate the banking integration to the zoo habitat layout — LAST sub-issue under ISSUE-2ccc10 before ISSUE-63a946 (zoo repo extraction) can start. Follows icloud (ISSUE-e7d127) and news (ISSUE-d5f8ab) precedent. Banking is the largest of the three: `cache.py`, `client.py`, `sync.py`, three+ handlers (`banking.balance`, `banking.sync`, `banking.transactions`, `banking.accounts`, `banking.setup`, `banking.complete_setup`, `banking.status`), credentials, a dedicated pyproject dep (`enable_banking_client`), an on-disk scheduled sync, a `components.yaml`, and a dedicated doc page (`docs/integration-banking.md`). Split the current dual-tree layout (code at `src/marcel_core/skills/integrations/banking/` + docs at `src/marcel_core/defaults/skills/banking/`) into two habitats under `MARCEL_ZOO_DIR`: (a) `<zoo>/integrations/banking/` with `integration.yaml` (provides: all banking.* handlers; requires: credentials `ENABLEBANKING_APP_ID` + `ENABLEBANKING_SESSIONS`-or-`ENABLEBANKING_SESSION_ID`; requires packages: `enable_banking_client`). Declares `scheduled_jobs:` entry for `banking.sync` on the existing cron cadence (port from on-disk `~/.marcel/jobs/bank-sync/JOB.md` which today uses `trigger.type: interval` at 28800s = every 8 hours — convert to cron equivalent, e.g. `0 */8 * * *`, or keep as `interval_seconds` if the scheduled_jobs schema supports it). Handler fan-out on `user_slug='_system'` iterates `paths.list_user_slugs()` and calls `banking.sync(slug)` for each user with banking creds, mirroring the news pattern. `__init__.py`, `cache.py`, `client.py`, `sync.py`, `components.yaml` all move in. (b) `<zoo>/skills/banking/` with `SKILL.md` (depends_on: [banking]) + `SETUP.md` (the onboarding for linking an EnableBanking session). Use `marcel_core.plugin.paths.cache_dir(slug) / 'banking.db'` for the SQLite path (currently hardcoded via `storage._root.data_root()`). Use `marcel_core.plugin.credentials.load(slug)` for credentials (imports from `marcel_core.storage.credentials` today — follows icloud precedent). Use `marcel_core.plugin.get_logger(__name__)`. Imports must follow the external-habitat pattern `from .module import name` (NOT `from . import module`) per the news migration lesson (ISSUE-d5f8ab). Dependency move: `enable_banking_client` moves from kernel `[project.dependencies]` into `[project.optional-dependencies] zoo` group — same pattern icloud followed with `caldav`+`vobject` in ISSUE-e7d127. `uv sync --all-extras` (dev + Docker) still installs it. Verify `grep enable_banking pyproject.toml` shows it only under `[project.optional-dependencies]` after the migration. **Delete dead kernel code** — `_ensure_default_jobs()` in `src/marcel_core/jobs/scheduler.py` (currently creates per-user `Bank sync` jobs for any user with EnableBanking creds) becomes superseded by the `scheduled_jobs:` declaration in the banking habitat's `integration.yaml`. Delete the function and its single call site in `rebuild_schedule()`. Any imports it pulls in (`load_credentials`, `is_backup_slug`, `JobDefinition`, `NotifyPolicy`, `TriggerSpec`, `list_jobs`, `save_job`) that become unused at module level should also go. This is the final job-migration cleanup — after this, **no kernel code creates scheduled jobs from user state**; all periodic work flows through the habitat hook. Handle existing on-disk JOBs: the current `~/.marcel/jobs/bank-sync/` is a user-scope JOB created by `_ensure_default_jobs()`. After the migration, the habitat declaration creates a system-scope job with a stable sha256 ID. The old user-scope one stays on disk until manually archived (same as news-sync → .archived-news-sync-20260418 pattern). Document this in the Implementation Log — do not auto-delete user JOB.md files as part of the migration; user's history lives there. Tests: move all banking-specific tests from `tests/core/test_banking.py` (and anywhere else) into `<zoo>/integrations/banking/tests/`. Use the `types.ModuleType(_PKG)` synthetic-parent-package pattern from the news test loader so `from .cache import X` resolves inside the test context. Any kernel-side coverage that was incidentally hitting banking handlers becomes either a fake-plugin dispatch test (exercises the loader, not the real integration) or is deleted. Delete `src/marcel_core/skills/integrations/banking/` (full directory) and `src/marcel_core/defaults/skills/banking/` afterward. Delete any `banking` entry in `src/marcel_core/skills/skills.json` if present. Docs: delete `docs/integration-banking.md` entirely — follows icloud (ISSUE-e7d127) + news (ISSUE-d5f8ab) precedent. Canonical docs live inside the habitat. Remove the Integrations section from `mkdocs.yml` (it only held banking at this point — news was removed in ISSUE-d5f8ab, icloud never had a page). Update `docs/index.md` 'Adding a new skill or integration' row to reference the zoo habitats as the only integration references (kernel no longer ships any first-party integration). Update `docs/plugins.md` 'First-party vs. external integrations' section: strip the 'Marcel still ships one first-party integration' paragraph — after this migration, kernel ships zero. Update `docs/architecture.md` source tree to remove `integrations/banking/` (the whole `integrations/` subdirectory is gone from the kernel). Verify: (1) fresh Marcel install with empty `MARCEL_ZOO_DIR` has zero integration handlers registered and zero jobs with `template='habitat:*'`; (2) pointing `MARCEL_ZOO_DIR` at the zoo checkout brings banking handlers + banking skill + the banking sync job back as a system-scope `JobDefinition(template='habitat:banking')` with stable sha256-derived ID `sha256('banking:<entry name>')[:12]`; (3) `make check` green at 90% coverage after the kernel deletion + dep move; (4) `grep -r 'marcel_core.skills.integrations.banking\|defaults/skills/banking\|_ensure_default_jobs' .` confirms only historical references remain (`project/issues/closed/`, this issue file). After this issue closes: ISSUE-2ccc10 (umbrella) can close — all three real integrations (icloud, news, banking) migrated. ISSUE-63a946 (zoo repo extraction to github.com/shbunder/marcel-zoo) unblocks. Kernel ships zero first-party integrations; the plugin surface (`register`, `credentials`, `paths`, `models`, `rss`, `get_logger`) is the complete contract."

**Resolved intent:** Final sub-issue of the habitat-migration arc under ISSUE-2ccc10. Banking is the biggest and last first-party integration in the kernel — it has the largest code surface (`cache.py`, `client.py`, `sync.py` plus seven handlers), the heaviest dep (`enable_banking_client`), the only `components.yaml` (the Mini App surface), the only kernel-side scheduled-job bootstrap (`_ensure_default_jobs()`), and credentials that must survive the move. The point of this issue is not just the migration — it's the **retirement of kernel-side periodic-job code**. After banking ships as a zoo habitat declaring its own `scheduled_jobs:` entry, the function `_ensure_default_jobs()` becomes dead code and goes with it. From this issue onward, every scheduled background task Marcel runs is declared in an `integration.yaml`; the kernel has no hardcoded job bootstrap for any integration. The kernel plugin surface (`register`, `credentials`, `paths`, `models`, `rss`, `get_logger`) is the complete contract habitats need — nothing new should be required for banking. If something is missing, extend the surface explicitly rather than letting habitat code reach past it. Closing this issue closes the migration arc: ISSUE-2ccc10 can close, ISSUE-63a946 (zoo repo extraction to its own GitHub repo) unblocks, and `src/marcel_core/skills/integrations/` is left holding zero subdirectories.

## Description

Banking today lives across two kernel locations:

- **Handler + client + cache + sync** — [src/marcel_core/skills/integrations/banking/](../../src/marcel_core/skills/integrations/banking/) with `__init__.py` (handlers), `client.py` (EnableBanking HTTP client), `cache.py` (SQLite wrapper over transactions/balances), `sync.py` (periodic fetch-and-store loop).
- **Docs + resource** — [src/marcel_core/defaults/skills/banking/](../../src/marcel_core/defaults/skills/banking/) with `SKILL.md`, `SETUP.md`, and `components.yaml` (Mini App component descriptor).

Both move to the zoo (`MARCEL_ZOO_DIR`) and the kernel copies are deleted. The shape mirrors icloud (ISSUE-e7d127) and news (ISSUE-d5f8ab). What's new and net-net harder here than news:

1. **Credentials surface exercised for the first time at full depth.** icloud uses `credentials.load(slug)` for simple key/value auth. Banking needs read + write: the `banking.setup` / `banking.complete_setup` handlers persist a session ID back to credentials. If `marcel_core.plugin.credentials` doesn't already expose the write path, this issue adds it (preferred), or documents the narrow reach-past if adding to the surface is disproportionate. Decision goes in the Implementation Log.
2. **`scheduled_jobs:` entry for `banking.sync` + deletion of `_ensure_default_jobs()`.** The current kernel function walks `data_root()/users/`, checks each user's credentials for EnableBanking keys, and creates a `bank-sync` per-user `JobDefinition(template='sync', users=[slug], interval_seconds=28800)`. The replacement is a single `scheduled_jobs:` entry in `integration.yaml` → system-scope `JobDefinition(template='habitat:banking', users=[])` with a cron cadence (`0 */8 * * *` — every 8 hours). The handler fans out: on `user_slug='_system'`, iterate `paths.list_user_slugs()`, filter backups + `_system`, and for each slug with EnableBanking creds call `sync_bank(slug)`. Users without creds are silently skipped (no error — first-run bootstrap shouldn't blow up). The kernel function `_ensure_default_jobs()` is deleted along with its call site in `rebuild_schedule()`.
3. **`enable_banking_client` moves to optional deps.** Kernel `pyproject.toml` currently lists `enable_banking_client` in `[project.dependencies]`. Move to `[project.optional-dependencies] zoo` (creating the group if not already there — icloud's move in ISSUE-e7d127 put `caldav` + `vobject` there). The kernel Dockerfile and `make` targets already run `uv sync --all-extras` (verified during the icloud migration), so no build-pipeline changes. Verify `grep enable_banking pyproject.toml` only matches the optional section after the move.
4. **`components.yaml` travels to the zoo integration directory.** This is the Mini App (A2UI) component descriptor — the kernel's loader finds it by scanning `<zoo>/integrations/<name>/components.yaml` already (verify; if not, the loader needs the same search-path extension that `scheduled_jobs:` got in ISSUE-82f52b — a separate issue if so). Do NOT take on loader extension work as part of this issue unless verification shows it's blocking; record it as a follow-up if discovered.
5. **Handler count is higher.** News had three handlers, banking has seven: `balance`, `sync`, `transactions`, `accounts`, `setup`, `complete_setup`, `status`. `integration.yaml` lists all seven under `provides:`. The `setup` / `complete_setup` pair is the credential-onboarding dance — keep them in the habitat, they're part of the banking feature.

**Code changes inside the moved files:**

- `from marcel_core.skills.integrations import register` → `from marcel_core.plugin import register` (in `__init__.py`)
- `from marcel_core.storage.credentials import load_credentials, save_credential` → `from marcel_core.plugin import credentials` + `credentials.load(slug)` / `credentials.save(slug, key, value)` (if the `.save` helper exists; if not, add it — see Description #1)
- `from marcel_core.storage._root import data_root` + hardcoded `data/users/{slug}/cache/banking.db` → `from marcel_core.plugin import paths` + `paths.cache_dir(slug) / 'banking.db'`
- `logging.getLogger(__name__)` → `from marcel_core.plugin import get_logger` + `get_logger(__name__)`
- All relative imports follow `from .module import name`, never `from . import module` — external-habitat loader constraint (news ISSUE-d5f8ab Lessons Learned)
- SKILL.md frontmatter gains `depends_on: [banking]`
- Add `integration.yaml`: `name: banking`, `description`, `provides: [banking.balance, banking.sync, banking.transactions, banking.accounts, banking.setup, banking.complete_setup, banking.status]`, `requires: {credentials: [ENABLEBANKING_APP_ID, ...], packages: [enable_banking_client]}`, `scheduled_jobs: [{name: 'Bank sync', handler: banking.sync, cron: '0 */8 * * *', notify: on_failure, channel: telegram}]`

**Handler fan-out on `_system` slug:**

```python
if user_slug == _SYSTEM_USER:
    for slug in _live_user_slugs():
        creds = credentials.load(slug)
        if not _has_banking_creds(creds):
            continue  # silently skip — user didn't link an account yet
        try:
            await sync_bank(slug)
        except Exception as exc:
            log.warning('[banking-sync] user=%s failed: %s', slug, exc)
            # continue to next user — one broken link shouldn't block the batch
```

Mirrors news. The filtering (`_live_user_slugs`) deduplicates backup slugs and `_system` the same way.

**Dead-code deletion:**

- `_ensure_default_jobs()` in [src/marcel_core/jobs/scheduler.py](../../src/marcel_core/jobs/scheduler.py) — full function + its call site in `rebuild_schedule()` (one line).
- All imports the function pulled in that become unused after deletion — audit the module and remove them. `ruff` will catch any that are actually still referenced.

**Docs:**

- Delete `docs/integration-banking.md` entirely.
- Strip the `Integrations:` section from `mkdocs.yml` nav (news was already removed in ISSUE-d5f8ab; banking is the last entry).
- Update `docs/index.md` "Adding a new skill or integration" row — drop the banking reference, leave only the zoo-habitats pointer.
- Update `docs/plugins.md` "First-party vs. external integrations" — this migration drops the kernel count to zero. Rewrite the paragraph to describe the kernel as integration-free, list migrated habitats (docker, icloud, news, banking) as the migration outcome.
- Update `docs/architecture.md` source-tree diagram — remove the `integrations/` subdirectory entirely (it's empty after banking leaves).

**Tests:**

- Move all of `tests/core/test_banking.py` into `<zoo>/integrations/banking/tests/test_banking.py` under the `types.ModuleType(_PKG)` synthetic-parent-package pattern from news. Any test that was really exercising storage / credentials / jobs kernel code (not the banking integration itself) either relocates to a more appropriate kernel test file or, if it was incidentally catching banking coverage, becomes a fake-plugin dispatch test in `tests/skills/test_integration_dispatch.py` (or wherever icloud's equivalent landed).
- Delete tests that assert against `_ensure_default_jobs()` behavior — the function is gone.
- Kernel-side: add one small test that `rebuild_schedule()` with no habitats and no user credentials produces zero jobs. This is the "no hardcoded job bootstrap" regression guard.

**Verification:**

- `make check` green at 90% coverage. Banking is large — deleting it + its tests should *raise* coverage (same pattern as news). If it drops, investigate.
- Fresh start with `MARCEL_ZOO_DIR` unset: `discover()` registers zero banking handlers; `list_all_jobs()` returns nothing with `template='habitat:banking'` or `template='sync'`.
- Pointing `MARCEL_ZOO_DIR` at the zoo checkout: `discover()` registers all seven banking.* handlers; `_ensure_habitat_jobs()` materializes a `JobDefinition(id=sha256("banking:Bank sync")[:12], template='habitat:banking', users=[], cron='0 */8 * * *')`. Invoking `integration(id='banking.balance', user_slug=<real slug>)` dispatches into the zoo code.
- Orphan cleanup: remove the habitat directory, restart, confirm the habitat job is deleted from disk (same precedent as news).
- `grep -r 'marcel_core.skills.integrations.banking\|defaults/skills/banking\|_ensure_default_jobs' .` returns only historical references (this issue file, `project/issues/closed/`).

**Why this closes the migration arc:** icloud proved the `credentials` surface. News proved `scheduled_jobs:` as the habitat-declared replacement for hand-written JOB.md files. Banking is the last mile — biggest code surface, heaviest dep, the one remaining hardcoded-job-creation function, and the dedicated doc page. When this issue closes, `src/marcel_core/skills/integrations/` holds zero subdirectories, `src/marcel_core/defaults/skills/` holds zero integration skill dirs, and the kernel `pyproject.toml` has no integration-specific deps left. ISSUE-2ccc10 closes, ISSUE-63a946 (zoo extraction to its own GitHub repo) unblocks.

## Tasks

- [ ] Audit current banking code — enumerate every `marcel_core.*` import in `__init__.py`, `cache.py`, `client.py`, `sync.py`. Expected reach-past: `storage.credentials` (load + save), `storage._root.data_root`, `jobs` (unlikely — the scheduled-job creation happens from the scheduler, not from banking code). Document findings in the Implementation Log.
- [ ] Decide credentials write-path: does `marcel_core.plugin.credentials` need a `save(slug, key, value)` helper (recommended — mirrors `load`) or is there already an existing write path? If the surface needs to grow, do it in the first `🔧 impl:` commit and update `docs/plugins.md` accordingly.
- [ ] Verify kernel loader finds `components.yaml` under `<zoo>/integrations/<name>/` — grep the loader code, confirm the search path already covers external habitats. If not, flag as a follow-up issue and do NOT take on loader work inside this migration.
- [ ] Create `<zoo>/integrations/banking/{__init__.py, cache.py, client.py, sync.py, integration.yaml, components.yaml}` — imports rewritten to the plugin surface; `cache.py` uses `paths.cache_dir(slug) / 'banking.db'`; all relative imports follow `from .module import name`.
- [ ] Write `integration.yaml`: `provides:` lists all seven handlers; `requires:` declares credential keys + `enable_banking_client` package; `scheduled_jobs:` declares the sync on `0 */8 * * *` with `notify: on_failure`, `channel: telegram`.
- [ ] Implement `banking.sync` fan-out on `user_slug='_system'` — iterate live user slugs, filter those without EnableBanking creds (silently skip), call `sync_bank(slug)` per user with per-user exception handling (log warning, continue batch).
- [ ] Create `<zoo>/skills/banking/{SKILL.md, SETUP.md}` — copy from `src/marcel_core/defaults/skills/banking/`, add `depends_on: [banking]` to SKILL.md frontmatter.
- [ ] Move `enable_banking_client` from kernel `[project.dependencies]` to `[project.optional-dependencies] zoo` in `pyproject.toml`. Run `uv lock` to regenerate the lock file. Verify `grep enable_banking pyproject.toml` shows only the optional entry.
- [ ] Add `<zoo>/integrations/banking/tests/test_banking.py` — full coverage of the migrated code using the `types.ModuleType(_PKG)` synthetic-parent-package test loader pattern from news. Mock the EnableBanking HTTP client — no live API calls in the suite.
- [ ] Delete `src/marcel_core/skills/integrations/banking/` (full directory).
- [ ] Delete `src/marcel_core/defaults/skills/banking/` (full directory).
- [ ] Check `src/marcel_core/skills/skills.json` for any `banking` entry — delete if present.
- [ ] Delete `_ensure_default_jobs()` in [src/marcel_core/jobs/scheduler.py](../../src/marcel_core/jobs/scheduler.py) and its single call site in `rebuild_schedule()`. Remove now-unused imports (`load_credentials`, `is_backup_slug`, `JobDefinition`, `NotifyPolicy`, `TriggerSpec`, `list_jobs`, `save_job` — audit after deletion, `ruff` will flag).
- [ ] Delete `tests/core/test_banking.py` and any kernel-side tests targeting `_ensure_default_jobs()`. Relocate any test that was really testing kernel storage/jobs (not banking) to an appropriate kernel test file.
- [ ] Add one kernel regression test: `rebuild_schedule()` with no habitats and no user credentials returns zero jobs. Guards against re-introduction of hardcoded job bootstrap.
- [ ] Verify the `scheduled_jobs:` round-trip for banking: with the habitat loaded, `_ensure_habitat_jobs()` creates `JobDefinition(id=<sha256 prefix>, template='habitat:banking', users=[], cron='0 */8 * * *')`. Orphan cleanup verified by clearing `_metadata['banking']` and re-running reconciliation. Capture IDs and result in the Implementation Log.
- [ ] Verify fresh install story: `MARCEL_ZOO_DIR` unset → zero banking handlers, zero `template='habitat:banking'` jobs. `MARCEL_ZOO_DIR` set → all seven handlers live, scheduled job on disk.
- [ ] Docs: delete `docs/integration-banking.md`, strip the Integrations section from `mkdocs.yml`, update `docs/index.md` / `docs/plugins.md` / `docs/architecture.md` per the Description above. All four files ship in the final `🔧 impl:` commit.
- [ ] Update `project/issues/open/ISSUE-260418-2ccc10-...md` — mark the banking migration task `[✓]` and append an Implementation Log entry linking this issue. With banking done, mark the umbrella ready to close in a follow-up.
- [ ] `make check` green at 90% coverage after the deletion + dep move.
- [ ] Document in the Implementation Log: cadence decision (kept as cron or interval), credentials write-path decision, components.yaml loader check result, fan-out verification, and one paragraph on the `bank-sync` on-disk JOB — archived manually per the news-sync precedent (NOT auto-deleted).

## Relationships

- Depends on: ISSUE-6ad5c7 (habitat conventions — landed)
- Depends on: ISSUE-c48967 (`marcel_core.plugin` surface — landed)
- Depends on: ISSUE-82f52b (`scheduled_jobs:` hook — landed)
- Depends on: ISSUE-e7d127 (icloud migration — credentials surface precedent)
- Depends on: ISSUE-d5f8ab (news migration — scheduled-jobs hook + fan-out pattern + external-habitat import trap lessons)
- Part of: ISSUE-2ccc10 (umbrella tracker — closes the final "Migrate banking" task, unblocks umbrella close)
- Blocks: ISSUE-63a946 (zoo repo extraction — banking was the last first-party integration blocking zoo-as-its-own-repo)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
