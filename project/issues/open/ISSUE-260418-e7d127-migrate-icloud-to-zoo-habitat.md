# ISSUE-e7d127: Migrate iCloud integration to a marcel-zoo habitat

**Status:** Open
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

- [ ] Audit the icloud habitat end-to-end against the plugin surface — identify any reach-past imports beyond `register` and `load_credentials` that this migration would need to add to the surface. (Should be none, but the icloud cleanup mirrors the discipline that paid off in ISSUE-e1b9c4.)
- [ ] Create `~/projects/marcel-zoo/integrations/icloud/{__init__.py, client.py, integration.yaml}` with imports rewritten to the plugin surface and the credentials call site updated to `credentials.load(slug)`.
- [ ] Create `~/projects/marcel-zoo/skills/icloud/{SKILL.md, SETUP.md}` — copy from `src/marcel_core/defaults/skills/icloud/`, add `depends_on: [icloud]` to the SKILL.md frontmatter so credential gating flows through `integration.yaml`.
- [ ] Add `integration.yaml`: `name: icloud`, `description`, `provides: [icloud.calendar, icloud.mail]`, `requires.credentials: [ICLOUD_APPLE_ID, ICLOUD_APP_PASSWORD]`.
- [ ] Add `~/projects/marcel-zoo/integrations/icloud/tests/test_handlers.py` with at least one unit test per handler that monkeypatches `caldav.DAVClient` / `imaplib.IMAP4_SSL` so the suite never hits Apple.
- [ ] Decide and document the zoo dep story for `caldav` — either follow ISSUE-2ccc10's pending decision or land the conservative interim (`requirements-zoo.txt` + Docker `pip install`).
- [ ] Drop `caldav>=3.1.0` from the kernel `pyproject.toml`. Verify the kernel image still builds.
- [ ] Delete `src/marcel_core/skills/integrations/icloud/` (both `__init__.py` and `client.py`).
- [ ] Delete `src/marcel_core/defaults/skills/icloud/` (both `SKILL.md` and `SETUP.md`).
- [ ] Update `docs/plugins.md` if needed — the "First-party vs. external integrations" section currently lists icloud among the kernel-bundled set; that line moves icloud out.
- [ ] Update `project/issues/open/ISSUE-260418-2ccc10-...md` — mark the icloud migration task `[✓]` and link this issue from the Implementation Log.
- [ ] Grep the repo for `marcel_core.skills.integrations.icloud` and `defaults/skills/icloud` — confirm only historical references remain.
- [ ] Smoke check: with `MARCEL_ZOO_DIR` set, `integration(id="icloud.calendar", params={"days_ahead": "1"})` dispatches into the zoo code (stubbed credentials are fine).
- [ ] `make check` green at the 90% coverage gate.

## Relationships

- Depends on: ISSUE-c48967 (plugin surface — landed; provides `marcel_core.plugin.credentials`)
- Depends on: ISSUE-6ad5c7 (docker POC — landed; provides the layout to mirror)
- Part of: ISSUE-2ccc10 (umbrella tracker — count drops from 3 to 2 when this closes)
- Pattern reference: ISSUE-e1b9c4 (settings cleanup — set the audit-first discipline this issue inherits)

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
