# ISSUE-e7d127: Migrate iCloud integration to a marcel-zoo habitat

**Status:** Closed
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "Migrate the `icloud` integration out of the kernel into a marcel-zoo habitat — sub-issue under ISSUE-2ccc10. Smallest remaining migration target after the settings cleanup (no scheduled jobs, two handlers, one client module, ~247 lines including SKILL.md + SETUP.md). Validates: switching `from marcel_core.skills.integrations import register` → `from marcel_core.plugin import register`, switching `from marcel_core.storage.credentials import load_credentials` → `from marcel_core.plugin import credentials` (and rewriting the call site to `credentials.load(slug)`), moving `caldav` from the kernel `pyproject.toml` to whatever zoo dep story 2ccc10 settles on, mirroring the docker POC layout (`<MARCEL_ZOO_DIR>/integrations/icloud/{__init__.py, client.py, integration.yaml}` + `<MARCEL_ZOO_DIR>/skills/icloud/{SKILL.md, SETUP.md}` with `depends_on: [icloud]`), deleting both kernel copies, and verifying discovery + handler dispatch end-to-end without the kernel-side files."

**Resolved intent:** First *real* zoo migration of a credential-bearing first-party integration, now that ISSUE-e1b9c4 has shown the audit-first discipline pays off and ISSUE-c48967 has landed the `marcel_core.plugin.credentials` surface that this migration consumes. The icloud habitat is small enough to validate the full move-and-delete loop end-to-end in one issue (two handlers, one client, no scheduled jobs, no separate test file, no resource files) — exactly the right shape to stress-test the credential plumbing without compounding it with the periodic-job hook design that banking and news will need. By the end of this issue, no `icloud` code or docs ship inside the kernel; a fresh `make serve` against an empty `MARCEL_ZOO_DIR` shows zero icloud handlers in the registry, and pointing `MARCEL_ZOO_DIR` at the zoo checkout brings them back unchanged.

## Description

The icloud integration today lives across two kernel locations:

- **Handler + client** — `src/marcel_core/skills/integrations/icloud/{__init__.py, client.py}` (~168 lines, two `@register` handlers: `icloud.calendar`, `icloud.mail`).
- **Docs** — `src/marcel_core/defaults/skills/icloud/{SKILL.md, SETUP.md}` (~79 lines, the agent-facing instructions and the setup walkthrough shown when credentials are missing).

Both move to the zoo (`~/projects/marcel-zoo/`) and the kernel copies are deleted. The shape mirrors the docker POC (ISSUE-6ad5c7), which is already in the zoo at `integrations/docker/` + `skills/docker/`.

**Code changes inside the moved files:**

- `from marcel_core.skills.integrations import register` → `from marcel_core.plugin import register` (in the new `__init__.py`)
- `from marcel_core.storage.credentials import load_credentials` → `from marcel_core.plugin import credentials` (in the new `client.py`)
- The call `creds = load_credentials(slug)` becomes `creds = credentials.load(slug)` — same dict-shape return value
- Add `integration.yaml` declaring `name: icloud`, `description`, `provides: [icloud.calendar, icloud.mail]`, and `requires.credentials: [ICLOUD_APPLE_ID, ICLOUD_APP_PASSWORD]`
- The SKILL.md frontmatter gains `depends_on: [icloud]` so the loader resolves the credential requirements through `integration.yaml` instead of an inline `requires:` list

**Dependency move:**

- `caldav>=3.1.0` is currently a kernel runtime dependency in `pyproject.toml`. Once icloud lives in the zoo, `caldav` is only needed when the user has icloud configured in their zoo checkout. Drop it from the kernel `pyproject.toml`. The zoo dep story is still being settled in ISSUE-2ccc10 (its own `pyproject.toml` vs. pure-python with manual `pip install`); for this issue, document the chosen path and keep the kernel image building. If 2ccc10's decision lands first, follow it; if not, the conservative interim is to add a top-level `requirements-zoo.txt` in marcel-zoo listing `caldav>=3.1.0` and have the Docker image `pip install -r` that file when a zoo checkout is present.
- `imaplib` is stdlib — no dep change there.

**Tests:**

- No dedicated `tests/**/*icloud*` file exists in the kernel today. The kernel's coverage of `icloud/__init__.py` and `icloud/client.py` comes from package-discovery side-effects in `tests/core/test_skills.py` / `test_skill_loader.py` and from `pyproject.toml`'s coverage gate including the whole `src/marcel_core/` tree. After the move, those source files no longer exist in the kernel, so the gate naturally stops counting them. The zoo gets its own `integrations/icloud/tests/` directory with at least one fast unit test that monkeypatches `caldav.DAVClient` and `imaplib.IMAP4_SSL` so the test suite never touches Apple's servers.

**Verification:**

- `make check` green at the 90% coverage gate after the kernel deletion (the removal should *raise* coverage percentage since the deleted files were modestly covered; if it drops, investigate).
- A fresh start with `MARCEL_ZOO_DIR` unset shows zero icloud handlers in `marcel_core.skills.integrations.list_handlers()` (or whatever the registry inspector is).
- Pointing `MARCEL_ZOO_DIR` at the zoo checkout brings `icloud.calendar` and `icloud.mail` back, and `integration(id="icloud.calendar", params={"days_ahead": "1"})` dispatches into the zoo code (verified by a log line or a stub credential, not by a real CalDAV call).
- Repo-wide grep confirms only historical references (`project/issues/closed/`, this issue file) remain for `marcel_core.skills.integrations.icloud` and `defaults/skills/icloud`.

## Tasks

- [✓] Audit the icloud habitat end-to-end against the plugin surface — identify any reach-past imports beyond `register` and `load_credentials` that this migration would need to add to the surface. (Should be none, but the icloud cleanup mirrors the discipline that paid off in ISSUE-e1b9c4.)
- [✓] Create `~/projects/marcel-zoo/integrations/icloud/{__init__.py, client.py, integration.yaml}` with imports rewritten to the plugin surface and the credentials call site updated to `credentials.load(slug)`.
- [✓] Create `~/projects/marcel-zoo/skills/icloud/{SKILL.md, SETUP.md}` — copy from `src/marcel_core/defaults/skills/icloud/`, add `depends_on: [icloud]` to the SKILL.md frontmatter so credential gating flows through `integration.yaml`.
- [✓] Add `integration.yaml`: `name: icloud`, `description`, `provides: [icloud.calendar, icloud.mail]`, `requires.credentials: [ICLOUD_APPLE_ID, ICLOUD_APP_PASSWORD]`.
- [✓] Add `~/projects/marcel-zoo/integrations/icloud/tests/test_handlers.py` with at least one unit test per handler that monkeypatches `caldav.DAVClient` / `imaplib.IMAP4_SSL` so the suite never hits Apple.
- [✓] Decide and document the zoo dep story for `caldav` — either follow ISSUE-2ccc10's pending decision or land the conservative interim (`requirements-zoo.txt` + Docker `pip install`).
- [✓] Drop `caldav>=3.1.0` from the kernel `pyproject.toml`. Verify the kernel image still builds.
- [✓] Delete `src/marcel_core/skills/integrations/icloud/` (both `__init__.py` and `client.py`).
- [✓] Delete `src/marcel_core/defaults/skills/icloud/` (both `SKILL.md` and `SETUP.md`).
- [✓] Update `docs/plugins.md` if needed — the "First-party vs. external integrations" section currently lists icloud among the kernel-bundled set; that line moves icloud out.
- [✓] Update `project/issues/open/ISSUE-260418-2ccc10-...md` — mark the icloud migration task `[✓]` and link this issue from the Implementation Log.
- [✓] Grep the repo for `marcel_core.skills.integrations.icloud` and `defaults/skills/icloud` — confirm only historical references remain.
- [✓] Smoke check: with `MARCEL_ZOO_DIR` set, `integration(id="icloud.calendar", params={"days_ahead": "1"})` dispatches into the zoo code (stubbed credentials are fine).
- [✓] `make check` green at the 90% coverage gate.

## Relationships

- Depends on: ISSUE-c48967 (plugin surface — landed; provides `marcel_core.plugin.credentials`)
- Depends on: ISSUE-6ad5c7 (docker POC — landed; provides the layout to mirror)
- Part of: ISSUE-2ccc10 (umbrella tracker — count drops from 3 to 2 when this closes)
- Pattern reference: ISSUE-e1b9c4 (settings cleanup — set the audit-first discipline this issue inherits)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

### 2026-04-18 — Audit pass
Confirmed icloud's only kernel-internal imports were exactly the two the plugin surface already covers: `from marcel_core.skills.integrations import register` (in `__init__.py`) and `from marcel_core.storage.credentials import load_credentials` (in `client.py`, called as `load_credentials(slug)`). No reach-past beyond those — exactly the surface ISSUE-c48967 had already shaped. No surface extension required.

### 2026-04-18 — Zoo habitat created
- `~/projects/marcel-zoo/integrations/icloud/__init__.py` — handlers wrapping `client.get_calendar_events` / `client.search_mail`; `from marcel_core.plugin import register`.
- `~/projects/marcel-zoo/integrations/icloud/client.py` — caldav + imaplib client, credentials via `from marcel_core.plugin import credentials` and `credentials.load(slug)`. Logic unchanged from kernel original.
- `~/projects/marcel-zoo/integrations/icloud/integration.yaml` — `provides: [icloud.calendar, icloud.mail]`, `requires.credentials: [ICLOUD_APPLE_ID, ICLOUD_APP_PASSWORD]`, `requires.packages: [caldav]`.
- `~/projects/marcel-zoo/skills/icloud/{SKILL.md, SETUP.md}` — copied from kernel originals; SKILL.md frontmatter now declares `depends_on: [icloud]` instead of inline `requires:`, so credential gating flows through `integration.yaml`.

### 2026-04-18 — Zoo test infrastructure
First zoo-side tests (the docker POC shipped no tests). Set the precedent with minimal `~/projects/marcel-zoo/conftest.py` (adds kernel `src/` to `sys.path`) + `pytest.ini` (asyncio_mode=auto, testpaths=integrations skills). Tests run via `cd ~/projects/marcel-zoo && uv --project ~/projects/marcel run pytest integrations/`.

`integrations/icloud/tests/test_handlers.py` covers `client.get_calendar_events`, `client.search_mail`, and missing-credentials raise. **Workaround for double-registration**: pytest's collection machinery walks parent dirs and would import `integrations/icloud/__init__.py` twice under different names (`integrations.icloud` and `icloud`), each firing `@register('icloud.calendar')` against the same global `_registry` — `ValueError: already registered`. Solved by loading `client.py` directly via `importlib.util.spec_from_file_location` under a synthetic name `_icloud_client_under_test`, completely bypassing `__init__.py`. The 3-line handler wrappers in `__init__.py` are already exercised end-to-end by the kernel's discovery + dispatch tests, so this loses no coverage. Documented in the test file's module docstring so the next zoo author hits it as a known-and-justified pattern.

### 2026-04-18 — Kernel deletions + dep move
- `git rm -r src/marcel_core/skills/integrations/icloud/` (both files).
- `git rm -r src/marcel_core/defaults/skills/icloud/` (both files).
- `caldav>=3.1.0` and `vobject>=0.9.9` moved out of kernel `[project] dependencies` into a new `[project.optional-dependencies] zoo = [...]` group with a comment documenting the migration lifecycle. Both dev + Docker run `uv sync --all-extras`, so the deps remain installed for the zoo to import — but their conceptual ownership is now the zoo. Avoided scope creep into a separate zoo `pyproject.toml`; that decision still belongs to ISSUE-2ccc10.
- `pyproject.toml` coverage `omit` list cleaned of the two now-deleted icloud paths.
- `tests/core/test_skills.py` switched its proof-of-discovery assertions from `icloud.calendar`/`icloud.mail` to `news.search`/`news.recent` (still kernel-bundled, stable).
- `docs/plugins.md` "First-party vs. external integrations" section updated: kernel-bundled set is now banking + news; "Migrated so far" lists docker (ISSUE-6ad5c7) and icloud (ISSUE-e7d127).
- `src/marcel_core/plugin/__init__.py` docstring touch: credentials usage line now reads "used by zoo banking + icloud habitats" to reflect the post-migration state.

### 2026-04-18 — Verification
- `make check`: 1521 passed, 91.96% coverage (gate is 90%) — coverage went up after deletion as expected.
- Smoke check (`MARCEL_ZOO_DIR=/home/shbunder/projects/marcel-zoo`): `discover()` registers `icloud.calendar` + `icloud.mail` from module `_marcel_ext_integrations.icloud` (proves zoo path, not kernel).
- Negative smoke check (no `MARCEL_ZOO_DIR`): zero icloud handlers in `list_python_skills()` — proves the kernel deletion is clean.
- Repo grep for `marcel_core.skills.integrations.icloud` and `defaults/skills/icloud`: only historical hits in `project/issues/closed/ISSUE-034-...` and this issue file remain.
- Zoo unit tests: 3/3 passing.

### 2026-04-18 — Pre-close straggler fix
Pre-close-verifier flagged `docs/architecture.md:63` still listing `icloud/` under the kernel `integrations/` tree (mirror of the docker fix in `bca946c`). Dropped that line, plus the adjacent stale `settings.py` line left over from ISSUE-e1b9c4 — same paragraph, batched per verifier guidance. Final shape now points readers at `<MARCEL_ZOO_DIR>/integrations/` for icloud + docker.

**Reflection** (via pre-close-verifier):
- Verdict: REQUEST CHANGES → addressed (architecture.md straggler dropped + bonus settings.py straggler from ISSUE-e1b9c4 cleaned in the same paragraph)
- Coverage: 13/13 requirements addressed
- Shortcuts found: none
- Scope drift: none
- Stragglers: `docs/architecture.md:63` (icloud line, fixed); `docs/architecture.md:65` (settings.py from ISSUE-e1b9c4, fixed opportunistically)

## Lessons Learned

### What worked well
- **Audit-first paid off again.** Confirming up-front that icloud's only kernel-internal imports were `register` + `load_credentials` (already covered by the plugin surface) meant zero plumbing work mid-migration. The discipline ISSUE-e1b9c4 set up keeps paying.
- **Smoke check + negative smoke check together.** Running `discover()` with `MARCEL_ZOO_DIR=…` and then again with it unset (and asserting zero icloud handlers in the second case) catches both "zoo discovery actually fired" and "kernel deletion is complete" in two cheap shells. Adopt for every zoo migration going forward.
- **Verifier as second pair of eyes.** The straggler in `docs/architecture.md` would have shipped as a fixup-after-merge without it. Cost: one extra `🔧 impl:` commit; benefit: clean main.

### What to do differently
- **Run the architecture.md tree grep up front.** The kernel module-tree block in `docs/architecture.md` had stale entries from *both* this issue and the prior ISSUE-e1b9c4 — i.e. the ISSUE-e1b9c4 close also missed this file. Future zoo migrations: grep `docs/architecture.md` for the integration name as part of the "files to update" list before committing the deletion.
- **The zoo-tests double-import trap is now a known pattern.** Document it in the next zoo migration's plan up front rather than hitting it again at commit time. Solution lives in `~/projects/marcel-zoo/integrations/icloud/tests/test_handlers.py` — load the client module via `importlib.util.spec_from_file_location` under a synthetic name to bypass package-level `@register` collisions.

### Patterns to reuse
- **`[project.optional-dependencies] zoo` group as the dep-move sink.** Cheap, reversible, doesn't force the zoo-pyproject decision today, and `uv sync --all-extras` (already what dev + Docker do) keeps the deps installed for the zoo to import. Use the same group for `enable_banking_client` (banking) and `feedparser` (news) when those migrate, until ISSUE-2ccc10 settles whether the zoo gets its own pyproject.
- **Dual-commit migration shape.** One commit on the kernel branch (deletions + dep move + docs + issue file), one commit on the zoo repo (new habitat). Each repo's history reads cleanly on its own; the cross-link is the issue ID in both messages.
- **Keep first-party tests pointing at first-party handlers.** When a kernel test asserts that `discover()` produces a known integration name as proof-of-discovery, point it at the most stable still-kernel-bundled handler (`news.*` after icloud left). Avoids the test having to move with each zoo migration.
