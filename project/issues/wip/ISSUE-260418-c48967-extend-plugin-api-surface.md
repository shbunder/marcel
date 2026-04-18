# ISSUE-c48967: Extend marcel_core.plugin API surface for habitat migrations

**Status:** WIP
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
| `marcel_core.plugin.credentials` | `load(user_slug) -> dict`, `save(user_slug, key, value) -> None` | `marcel_core.storage.credentials` |
| `marcel_core.plugin.paths` | `user_dir(slug) -> Path`, `artifact_dir(slug) -> Path` | `marcel_core.config.settings.data_dir` |
| `marcel_core.plugin.models` | `all_models() -> list[ModelInfo]`, `default_model() -> ModelInfo`, `set_channel_model(channel, model_id) -> None` | `marcel_core.runtime.model_chain` (or wherever the registry lives today) |
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

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
