# ISSUE-f74948: Job failure noise — backup-user jobs, leaking internals, RSS stack traces

**Status:** Closed
**Created:** 2026-04-17
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** bug, robustness, observability

## Capture
**Original request:** Robustness: suppress backup-user jobs, humanize failure notifications, gracefully handle non-XML RSS responses. Fixes A+B+D from the logs review; C (redeploy) happens at the end to also pick up the Ministral 3 upgrade that hasn't deployed yet.

**Follow-up Q&A:** Surfaced from a logs-review conversation where three distinct noise sources were found:
- Bank sync jobs scheduled for `shaun.backup-059-*` users, failing permanently with *"credit balance too low"* on `claude-haiku-4-5`.
- Telegram failure notifications include raw `str(exc)` from pydantic_ai, leaking `request_id`, model tag, HTTP status, and the internal user slug.
- Non-XML RSS responses (Knack Trends feeds redirecting to HTML) produce ElementTree stack traces in the logs instead of a clean warning.

**Resolved intent:** Three independent robustness fixes that all surfaced in the same logs review. Each reduces noise for the zoo keeper (the human operator) *or* the family (the non-technical users): backup snapshots should not run real jobs, Telegram error messages should not contain internal identifiers, and a badly-formed upstream feed should not fill the logs with tracebacks. After merge, the container needs a `request_restart()` redeploy to also pick up the Ministral 3 upgrade merged earlier today (issue 9b3867).

## Description

Three distinct problems, one issue because they all surfaced from the same logs-review pass and share the theme "stop the running system from generating avoidable noise."

### A — Backup user dirs get default jobs

[scheduler.py:127-163](../../src/marcel_core/jobs/scheduler.py#L127-L163) walks every directory under `users/` and, for any user with EnableBanking credentials, creates a `Bank sync (<slug>)` job. Backup snapshots (`shaun.backup-059-20260411T184915`, `shaun.backup-059-20260411T184951`) inherit the creds, so they each got their own bank-sync job — currently running every 8 hours and failing on every invocation because they use the old `anthropic:claude-haiku-4-5-20251001` default and our Anthropic credit balance is exhausted.

Fix: skip slugs matching `*.backup-*` in `_ensure_default_jobs`, and deactivate/delete the two existing backup-user bank-sync job definitions so the scheduler stops dispatching them.

### B — Telegram failure notifications leak internals

[executor.py:584-587](../../src/marcel_core/jobs/executor.py#L584-L587) formats the failure notification as `f'Job "{job.name}" failed: {run.error}'` where `run.error = str(exc)`. For pydantic_ai HTTP errors this produces a message like:

> Job "Bank sync (shaun.backup-059-20260411T184915)" failed: status_code: 400, model_name: claude-haiku-4-5-20251001, body: {'type': 'error', ..., 'request_id': 'req_011Ca8…'}

Family members should not see Anthropic request IDs, model tags, raw exception dicts, or the internal backup-user slug. Stored `run.error` keeps the full text for debugging; only the *Telegram-bound* string needs sanitising.

Fix: add a small `humanize_error(exc_text)` helper and apply it in `_notify_if_needed` just before building the Telegram message. Map known patterns (Anthropic credit exhaustion, OpenAI rate limit, timeouts, connection errors) to human text; for anything else, strip obvious technical noise (`request_id=...`, `model_name=...`, Python module paths). Also strip the `(slug)` suffix from the job name when it matches the user being notified — it is redundant and leaks the internal slug.

### D — Non-XML RSS responses crash into a stack trace

[tools/rss.py:123](../../src/marcel_core/tools/rss.py#L123) calls `ET.fromstring(xml_text)` unconditionally. When a feed returns HTML (e.g. `trends.knack.be/tech/feed/` currently serves a redirect page), the `xml.etree.ElementTree.ParseError` propagates up through [skills/integrations/news/sync.py:74](../../src/marcel_core/skills/integrations/news/sync.py#L74) and is logged as a warning *with a traceback*. Functionally it is fine — the news-sync continues — but it generates ~six lines of log spam per failed feed per sync, and we have two feeds failing × three syncs per day.

Fix: detect the non-XML case early in `_parse_feed` (e.g. response text does not start with `<?xml` or `<rss` / `<feed` after stripping whitespace) and raise a typed, short error that the caller logs as a one-line warning without a traceback.

### C — Redeploy (not a code change)

After merging, trigger the redeploy via `request_restart()` to pick up both the code changes in this issue and the Ministral 3 14B upgrade from issue 9b3867 (merged earlier today but never deployed — the container image dates from 2026-04-16 14:30). This is the standard [self-modification path](../../.claude/rules/self-modification.md), not a scripted step in the issue.

## Tasks
- [✓] A: skip `*.backup-*` slugs in `_ensure_default_jobs` (scheduler.py) — also applied to `_consolidate_memories` and `find_user_by_telegram_chat_id` via a shared `is_backup_slug` helper in `storage/users.py`
- [✓] A: the two backup-user bank-sync JOB.md files are already absent from disk (verified via `find`); stale entries in `scheduler_state.json` will self-clean on the post-merge restart since `load_job` returns None for non-existent IDs and `_dispatch` pops them
- [✓] A: add a regression test covering the backup-user skip in `_ensure_default_jobs` (+ tests for `is_backup_slug` directly)
- [✓] B: add `humanize_error` helper in `executor.py` with mappings for credit-exhausted, rate-limit, timeout, request-limit, connection errors, plus a generic stripper for `request_id`/`model_name`/`status_code`/`body: {…}`/Python exception class prefixes
- [✓] B: apply `humanize_error` to the Telegram-bound message in `_notify_if_needed`; strip the `(slug)` suffix from `job.name` via `_presentable_job_name` when the slug matches the notified user
- [✓] B: regression tests for `humanize_error` (5 cases — empty, pydantic_ai credit-exhausted, rate-limit, request-limit, timeout pass-through, generic stripping) and for `_presentable_job_name` (3 cases)
- [✓] D: detect non-XML responses in `_parse_feed` and raise a short `ValueError` (no traceback in the caller's log)
- [✓] D: `news/sync.py` now handles `ValueError` as a one-line warning; keeps the traceback path for other exceptions
- [✓] D: regression test for `_parse_feed` on HTML, empty body, and valid-XML-still-parses
- [✓] Run `make check` — all passed, coverage 91.69%
- [✓] Update any stale docs — grep found only closed-issue and lessons-learned references, no live docs to update
- [✓] After merge: `request_restart()` to deploy A+B+D and the pending Ministral 3 upgrade — fired as part of this close flow.

## Relationships
- Related to: [[ISSUE-9b3867-local-model-ministral3-upgrade]] — the pending redeploy after this issue also activates that upgrade

## Comments

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

### 2026-04-17 11:20 - LLM Implementation
**Action**: Implemented A+B+D robustness fixes end-to-end.
**Files Modified**:
- `src/marcel_core/storage/users.py` — new `is_backup_slug()` helper + applied in `find_user_by_telegram_chat_id`
- `src/marcel_core/jobs/scheduler.py` — `_ensure_default_jobs` and `_consolidate_memories` both skip backup slugs
- `src/marcel_core/jobs/executor.py` — new `humanize_error` and `_presentable_job_name`; `_notify_if_needed` routes the Telegram message through both
- `src/marcel_core/tools/rss.py` — `_parse_feed` sniffs the first bytes and raises `ValueError` for non-XML before ET.fromstring; `rss_fetch` catches both `ParseError` and `ValueError`
- `src/marcel_core/skills/integrations/news/sync.py` — distinct `ValueError` branch logs a single line
- `tests/core/test_storage.py` — `TestIsBackupSlug` (3 cases, incl. the `ops.backup-manager` false-positive guard)
- `tests/jobs/test_scheduler_scenarios.py` — `test_skips_backup_snapshot`
- `tests/jobs/test_executor.py` — `TestHumanizeError` (6 cases) + `TestPresentableJobName` (3 cases)
- `tests/tools/test_rss.py` — `TestNonXmlBody` (3 cases: HTML body, empty body, valid XML doesn't regress)

**Commands Run**: `make check`
**Result**: Success — 1387 tests pass, 91.69% coverage.
**Next**: commit, merge, then `request_restart()` so the fixes and the pending Ministral 3 upgrade both land on the running container.

### 2026-04-18 — Close (branch resumed from prior session)

Branch was sitting on `d6be767` from a previous session; resumed for close after ISSUE-82f52b merged. No additional source changes needed — full A+B+D implementation was already committed.

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 10/10 feature tasks addressed; the "After merge: request_restart()" item is procedural deployment, fired as part of this close flow.
- Shortcuts found: none
- Scope drift: none — diff is exactly A+B+D. The extension of the backup-slug guard into `_consolidate_memories` and `find_user_by_telegram_chat_id` is in-scope per the task text.
- Stragglers: none — grep across `docs/`, `.claude/`, `src/marcel_core/defaults/`, `project/`, and `~/.marcel/skills/` turns up only files the diff already touches plus closed-issue history.
- Notes (non-blocking): `_BODY_DICT_RE` uses non-greedy `.*?` — a payload with nested dicts could under-strip, but real Anthropic credit-exhaustion payload short-circuits via the `'credit balance is too low'` keyword before the generic stripper runs. BOM handling: `str.lstrip()` doesn't strip `\ufeff`, so a BOM-prefixed XML feed would fall into the "non-XML" branch; no current feed surfaces this.

## Lessons Learned

### What worked well
- **Bundling three related logs-review findings into one issue** kept the review context together. A, B, and D were all "operator noise" fixes surfaced from the same logs pass — splitting would have meant three separate dives into the same conversation, three redeploys.
- **Humanizing at the notification boundary, not the exception boundary.** `run.error` still stores the full raw string for debugging; only the Telegram-bound message gets sanitised via `humanize_error`. That preserves the debug story while fixing the family-facing UX.
- **Keyword short-circuits before regex stripping.** The `credit balance is too low` / `rate limit` / `timeout` checks run before the generic `request_id`/`model_name`/`body:` stripper, so the common production payload never hits the fragile regex path.

### What to do differently
- The non-greedy `body: {.*?}` regex is a latent footgun for any future error payload with nested dicts that isn't caught by a keyword short-circuit. If this helper grows past five keyword matchers, revisit with a brace-counting strip or anchor on a trailing `}` boundary.
- The `request_restart()` step was flagged as a task in the issue body but is fundamentally post-close procedural. Future "code + deploy" issues should separate the deploy into the Implementation Log's "next step" note rather than a task checkbox, to avoid the "tick an intent" dance at close time.

### Patterns to reuse
- **Shared predicate helper in `storage/users.py` applied at every call site that iterates `users/`.** `is_backup_slug()` + guards in `_ensure_default_jobs`, `_consolidate_memories`, `find_user_by_telegram_chat_id` is the cleanest "stop doing X for these users" shape — one regex, three predicates, no drift.
- **Prefix sniff before format-specific parser.** `_parse_feed` sniffs `head[:200]` and raises a typed short `ValueError` *before* `ET.fromstring` touches the body. Same idea applies to any parser that would otherwise turn a bad upstream into a multi-line traceback (JSON vs HTML, YAML vs garbage).
- **Notification-boundary sanitising.** Storage keeps raw; transport sanitises. Applies beyond jobs → any place Marcel surfaces internal text to a non-technical user.
