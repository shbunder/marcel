# ISSUE-c48967: Extend marcel_core.plugin API surface for habitat migrations

**Status:** Closed
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** plugin-system, refactor, marcel-zoo

## Capture

**Original request:** "Extend marcel_core.plugin with credentials, paths, get_logger, models submodules — additive plugin surface expansion to support upcoming habitat migrations (ISSUE-2ccc10 sub-issue 1 of 5). No integration migration in this issue; just grow the API surface and document it."

**Resolved intent:** Before any of the four real integrations (banking, icloud, news, settings) can move into zoo habitats, the `marcel_core.plugin` package needs the primitives those integrations actually use today: per-user credential storage, per-user data paths, and (for `settings`) read/write access to the model registry. Today those integrations reach past the surface — `banking` imports `marcel_core.storage.credentials` directly, `news` builds paths from `settings.data_dir`, and `settings` mutates `model_chain` internals. Each direct import becomes a load-bearing assumption that ties the integration to kernel internals and blocks a clean zoo extraction. This issue grows the plugin surface to cover those needs (and only those needs — no speculative additions), wires it as thin re-exports over the existing internals so behaviour is identical, and documents every new export. No integration is migrated here; the next sub-issues consume the new surface.

## Description

The plugin package currently re-exports `IntegrationHandler`, `register`, and `get_logger`. That covered docker (the POC), which needs nothing beyond the decorator. The four remaining first-party integrations need more.

The new surface, derived from what the four integrations actually call:

| Submodule | Exports | Backed by |
|---|---|---|
| `marcel_core.plugin.credentials` | `load(slug) -> dict[str,str]`, `save(slug, creds: dict[str,str]) -> None` | `marcel_core.storage.credentials` |
| `marcel_core.plugin.paths` | `user_dir(slug) -> Path`, `cache_dir(slug) -> Path`, `list_user_slugs() -> list[str]` | `marcel_core.storage.paths` (new kernel module) |
| `marcel_core.plugin.models` | `all_models() -> dict[str,str]`, `default_model() -> str`, `get_channel_model(slug, channel) -> str \| None`, `set_channel_model(slug, channel, model) -> None` | `marcel_core.harness.agent` + `marcel_core.storage.settings` |
| `marcel_core.plugin` (already present) | `get_logger(__name__)` | `logging.getLogger` |

Every addition is a thin re-export — no new logic, no new state. If the underlying call signature is wrong for plugin use, fix it at the source rather than wrapping it. Each new symbol gets a one-paragraph docstring describing its stability promise (the same promise as the rest of the surface: "won't break between Marcel versions without a migration note").

`docs/plugins.md` grows a section per submodule with a minimal example. The integration-pairs rule does not need updating — the contract for habitats is unchanged, only the toolbox they can reach for grew.

Out of scope (deferred to subsequent sub-issues):
- The "integration contributes a periodic job" hook — designed and implemented when `news` actually needs it (sub-issue 4).
- Any actual integration migration.
- Decisions about marcel-zoo's `pyproject.toml` — orthogonal.

## Tasks

- [✓] Audit the four integrations (`banking`, `icloud`, `news`, `settings`) for every `marcel_core.*` import that isn't already on the plugin surface — produce the definitive list of what the surface must expose.
- [✓] Add `marcel_core/plugin/credentials.py` — re-exports `load`, `save` from `marcel_core.storage.credentials`. Module docstring states the stability promise.
- [✓] Add `marcel_core/plugin/paths.py` — re-exports `user_dir`, `cache_dir`, `list_user_slugs`. Backed by new kernel module `marcel_core/storage/paths.py` (no `artifact_dir` — none of the four target integrations use artifact storage; defer until needed).
- [✓] Add `marcel_core/plugin/models.py` — re-exports `all_models`, `default_model`, `get_channel_model`, `set_channel_model`. Helpers live in the kernel; plugin re-exports.
- [✓] Update `marcel_core/plugin/__init__.py` to import the new submodules so `from marcel_core.plugin import credentials` works.
- [✓] Document each new submodule in `docs/plugins.md` with a one-paragraph rationale and a minimal usage example.
- [✓] Add tests in `tests/core/test_plugin.py` that import each new symbol from the plugin path and exercise it end-to-end against a temp data dir / fake user. The tests are the contract — anyone who reorganises the kernel must keep them green.
- [✓] Verify `from marcel_core.plugin import IntegrationHandler, register, get_logger, credentials, paths, models` works from a throwaway file outside `src/`.

## Relationships

- Depends on: ISSUE-6ad5c7 (habitat conventions + docker POC — landed)
- Blocks: ISSUE-2ccc10 (real integration migration — needs this surface)

## Implementation Log

### 2026-04-18 — Plugin surface extension

**Audit findings (Task 1):**

| Direct kernel import | Used by | Plugin export added |
|---|---|---|
| `storage._root.data_root()` | banking (cache, sync, client), news (cache) | `paths.user_dir`, `paths.cache_dir`, `paths.list_user_slugs` |
| `storage.credentials.load_credentials` | banking (sync, client), icloud (client) | `credentials.load` |
| `storage.credentials.save_credentials` | banking (client) | `credentials.save` |
| `harness.agent.all_models` | settings | `models.all_models` |
| `harness.agent.default_model` | settings | `models.default_model` |
| `storage.settings.load_channel_model` | settings | `models.get_channel_model` |
| `storage.settings.save_channel_model` | settings | `models.set_channel_model` |
| `tools.rss.fetch_feed` | news (sync) | not promoted — news-specific dep travels with the news habitat |

**Files added:**
- `src/marcel_core/storage/paths.py` — kernel helpers (`user_dir`, `cache_dir`, `list_user_slugs`)
- `src/marcel_core/plugin/credentials.py` — re-exports `load`, `save`
- `src/marcel_core/plugin/paths.py` — re-exports the three storage helpers
- `src/marcel_core/plugin/models.py` — re-exports four model-registry helpers

**Files modified:**
- `src/marcel_core/plugin/__init__.py` — surface docstring + `__all__` updated
- `docs/plugins.md` — per-submodule reference tables and usage examples
- `tests/core/test_plugin.py` — 15 new tests covering identity-of-reexport, round-trip, isolation

**Deviations from issue spec (decided during audit):**
- `credentials.save(slug, creds: dict)` instead of `(slug, key, value)` — the underlying `save_credentials` overwrites the whole file; a per-key wrapper would silently drop sibling keys.
- No `artifact_dir` — none of the four target integrations use artifact storage; deferred per "no speculative additions".
- Added `paths.cache_dir` and `paths.list_user_slugs` (not in original spec but used by every existing callsite).

**Verification:** `make check` passes — 1529 tests, 91.98% coverage. Smoke import from an out-of-tree file succeeds for every symbol.

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 8/8 tasks addressed
- Shortcuts found: none. No TODOs, no bare excepts, re-exports are true `is`-identity aliases (verified by dedicated tests).
- Scope drift: none. The four target integrations still import from kernel internals — that is the intended state until the next sub-issue migrates them.
- Stragglers: only the Description table in this issue file (still showed the old spec) and the sibling planning doc `project/issues/open/ISSUE-260418-2ccc10-...` — Description fixed in the close commit; the sibling planning doc is left for the next sub-issue writer.

## Lessons Learned

### What worked well
- Audit-first scoping: grepping the four target integrations for non-plugin `marcel_core.*` imports produced a precise list of nine helpers, and the surface design fell out mechanically. Avoided the speculative `artifact_dir` that had survived from the parent issue's brainstorm.
- Treating the underlying API as the source of truth: the original spec called for `save(slug, key, value)`, but `save_credentials` overwrites the whole file. Exposing the truth (`save(slug, creds: dict)`) avoided shipping a wrapper that would silently drop sibling keys the moment a real integration tried to use it.
- `is`-identity tests for re-exports — `assert plugin.credentials.load is load_credentials` — pin the contract that the surface is a re-export, not a wrapper. Future drift to a wrapper function fails CI loudly.

### What to do differently
- The Description table in the issue file shipped the original (pre-audit) spec and only the Tasks checklist was updated to match what was implemented. The pre-close-verifier called this out as a straggler. Next time, when the audit changes the surface design, update both the Tasks list AND the Description table in the same edit, before the first impl commit.
- Decide once at audit time whether helper names mirror kernel names or get renamed at the boundary. I went both ways: `credentials.load` (renamed from `load_credentials`) but `models.all_models` (kept). Both are defensible, but the inconsistency is now baked in. A single rule — "drop the noun suffix at the boundary" or "keep the kernel name" — would have been cleaner.

### Patterns to reuse
- The "kernel helper + plugin re-export" pair: put the real implementation in `storage/<topic>.py` (or wherever it naturally lives), then a thin `plugin/<topic>.py` that does `from kernel import x as y` and lists `__all__`. Use `is`-identity tests as the contract pin. This pattern scales for the channel/job/agent surfaces still to come (ISSUE-7d6b3f, ISSUE-a7d69a, ISSUE-e22176).
