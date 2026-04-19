# ISSUE-7d6b3f: Channel plugin contract + migrate Telegram habitat

**Status:** Closed
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** High
**Labels:** refactor, plugin-system, marcel-zoo

## Capture

**Original request:** "Each component should be modular (meaning all, the logic to interact with a channel should also be here, but in a modular integrated setup)."

**Follow-up clarification:** "WebSocket is a core pattern, like REST" — WebSocket stays in the kernel as transport primitive; only concrete transport *plugins* (Telegram, future Discord/Slack/Signal) are channel habitats.

**Resolved intent:** Define the channel habitat plugin contract, break direct imports of `marcel_core.channels.telegram.*` from the rest of the kernel (currently 12 files — `tools/marcel/notifications.py`, `tools/charts.py`, `api/chat.py`, `jobs/executor.py`, etc.), replace them with channel-registry lookups, and migrate [src/marcel_core/channels/telegram/](../../src/marcel_core/channels/telegram/) into `~/.marcel/channels/telegram/`. This is the largest architectural lift in the marcel-zoo extraction — Telegram is deeply coupled today.

## Description

**What stays in the kernel:**
- [channels/adapter.py](../../src/marcel_core/channels/adapter.py) — `ChannelCapabilities`, adapter protocol
- [channels/websocket.py](../../src/marcel_core/channels/websocket.py) — WebSocket transport primitive
- `main.py` — channel discovery + router mounting (generic)
- The `integration` / `marcel` tool infrastructure

**What moves to a channel habitat:**
- [channels/telegram/__init__.py](../../src/marcel_core/channels/telegram/__init__.py), `bot.py`, `webhook.py`, `formatting.py`, `sessions.py`
- [defaults/channels/telegram.md](../../src/marcel_core/defaults/channels/telegram.md) (channel prompt fragment)
- Telegram-specific tests

**Channel plugin contract:**

```
~/.marcel/channels/<name>/
├── __init__.py          # exports: router (APIRouter), capabilities (ChannelCapabilities)
├── <transport>.py       # webhook.py, bot.py, session handling, etc.
├── channel.yaml         # name, requires (env vars like TELEGRAM_BOT_TOKEN), endpoints
├── CHANNEL.md           # agent-visible prompt fragment for formatting hints
└── tests/
```

`main.py` discovers each channel habitat, loads `__init__.py`, mounts `.router` at a conventional path (`/channels/<name>/*`), registers its capabilities, and checks `channel.yaml requires:` — missing credentials → channel is disabled with a log warning, not a crash.

**The hard part — breaking direct imports.** 12 files reach into `marcel_core.channels.telegram` directly today. Each needs to route through a channel-capability registry instead:

- `tools/marcel/notifications.py` — "send a notification to Telegram" becomes "ask the channel registry for the 'telegram' channel and call `.send_message()`".
- `tools/charts.py`, `tools/marcel/ui.py` — same pattern: artifact delivery through the channel registry, not through direct Telegram bot imports.
- `api/chat.py`, `api/artifacts.py`, `api/components.py`, `api/conversations.py` — these may be Telegram-agnostic already; audit at implementation time.
- `jobs/executor.py` — per-job channel delivery via the registry.
- `main.py` — mounts routers via discovery, not via explicit `from marcel_core.channels.telegram import router`.

The registry needs a stable public API — something like `marcel_core.plugin.channels.get("telegram")` returning an object with `.send_message(user_slug, text)`, `.send_artifact(user_slug, artifact)`, `.capabilities`. Zoo channels export the same interface; kernel code depends only on the interface.

**Timebox spike first.** Before committing to the scope, spike the abstraction against two call sites (`notifications.py` and `charts.py`) to make sure the API shape is right. If it requires awkward plumbing for artifact delivery or auth, iterate on the shape before the bulk refactor.

## Tasks

- [✓] Define channel habitat layout + `channel.yaml` schema. Document in `docs/plugins.md`.
- [✓] Define `marcel_core.plugin.channels` — registry, channel interface protocol, `get(name)`, `list()`, `capabilities(name)`.
- [✓] Spike: refactor `tools/marcel/notifications.py` + `tools/charts.py` to use the registry. Confirm the API is ergonomic.
- [✓] Refactor remaining 10 files that import `marcel_core.channels.telegram.*` to use the registry. File list at top of issue.
- [✓] Refactor `main.py` to discover channel habitats from `<data_root>/channels/` and mount their routers at `/channels/<name>/*` (or whatever path convention the current telegram webhook uses — preserve URLs).
- [✓] Migrate Telegram: move `src/marcel_core/channels/telegram/` → `<MARCEL_ZOO_DIR>/channels/telegram/`. Include CHANNEL.md from `defaults/channels/telegram.md`.
- [✓] Update the `_RICH_UI_CHANNELS` set in [channels/adapter.py](../../src/marcel_core/channels/adapter.py) — this currently hardcodes channel names; must move into the plugin contract (each channel declares `rich_ui: true` in its `ChannelCapabilities`).
- [✓] Move Telegram tests to the habitat's `tests/` dir.
- [✓] Kernel-side tests: a fake channel habitat exercised against the registry + main.py mounting flow.
- [✓] Docs: `docs/channels/` gets updated to reflect habitat layout; [docs/channels/telegram.md](../../docs/channels/telegram.md) links into the zoo.
- [✓] Verify: fresh install with no zoo → Telegram not mounted, server still boots (WebSocket + REST still work).
- [✓] Verify: webhook URL still resolves (don't change public URLs during the migration).

## Relationships

- Depends on: ISSUE-3c87dd (plugin API pattern), ISSUE-6ad5c7 (habitat conventions)
- Blocks: ISSUE-63a946 (zoo repo extraction)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

### 2026-04-19 — stage 1: channel plugin registry scaffolding

- Added [src/marcel_core/plugin/channels.py](../../src/marcel_core/plugin/channels.py) with `ChannelPlugin` Protocol (`name`, `capabilities`, optional `router`), the `register_channel` / `get_channel` / `list_channels` registry API, and a `channel_has_rich_ui` three-valued query (registered True/False, or `None` for unknown).
- Re-exported the surface from [src/marcel_core/plugin/__init__.py](../../src/marcel_core/plugin/__init__.py) so zoo channel habitats get a stable import path (`from marcel_core.plugin import register_channel`).
- [src/marcel_core/channels/adapter.py](../../src/marcel_core/channels/adapter.py) — `channel_supports_rich_ui()` now consults the plugin registry first and only falls back to `_BUILTIN_RICH_UI_CHANNELS` (`websocket`, `app`, `ios`, `macos`, and temporarily `telegram`) when the channel is unregistered. The `telegram` entry in the builtin set is a stage-1 bootstrap and will be removed when the habitat migrates (stage 4 of this issue).
- [src/marcel_core/channels/telegram/__init__.py](../../src/marcel_core/channels/telegram/__init__.py) — telegram self-registers a `_TelegramPlugin` dataclass declaring its `ChannelCapabilities(markdown=True, rich_ui=True, streaming=True, progress_updates=True, attachments=True)` at import time. Behaviourally identical to today; the capability is now declared *by the channel* rather than hardcoded in the kernel.
- [tests/core/test_plugin_channels.py](../../tests/core/test_plugin_channels.py) — new module covering registry CRUD, re-registration warnings, plugin-over-builtin precedence, and the builtin fallback.
- No call sites migrated yet. Stage 2 (push sites: `tools/marcel/notifications.py`, `tools/charts.py`, `jobs/executor.py`, `tools/marcel/ui.py`) and stage 3 (pull sites: the four `api/*.py` files reading `get_user_slug`) will grow the Protocol with `send_message`/`send_artifact` and `resolve_user_slug` respectively.

### 2026-04-19 — stage 2: push-side refactor (4 call sites)

- Extended the `ChannelPlugin` Protocol with three push methods — `send_message(user_slug, text)`, `send_photo(user_slug, image_bytes, caption)`, `send_artifact_link(user_slug, artifact_id, title)`. Each returns `bool` ("did the recipient receive it?"), with `False` covering both unsupported shape and unresolved recipient.
- Replaced the frozen dataclass in [src/marcel_core/channels/telegram/__init__.py](../../src/marcel_core/channels/telegram/__init__.py) with a `_TelegramPlugin` class that implements the three methods. Every call into `bot`/`sessions`/`formatting` now flows through this class — it is the single place that imports those modules for push delivery.
- Refactored 4 push sites to route through the registry:
  - [src/marcel_core/tools/marcel/notifications.py](../../src/marcel_core/tools/marcel/notifications.py) — `get_channel('telegram').send_message(...)` replaces the direct `bot.send_message`. Covers the `'telegram'` and `'job'` channel cases.
  - [src/marcel_core/tools/charts.py](../../src/marcel_core/tools/charts.py) — `send_photo(...)` replaces the direct `bot.send_photo` for the chart-to-Telegram flow.
  - [src/marcel_core/tools/marcel/ui.py](../../src/marcel_core/tools/marcel/ui.py) — `send_artifact_link(...)` replaces the bespoke `artifact_markup` + `send_message(reply_markup=...)` sequence for the A2UI Mini App button delivery.
  - [src/marcel_core/jobs/executor.py](../../src/marcel_core/jobs/executor.py) — `_notify_telegram` now resolves the channel through the registry; if telegram is not registered (e.g. host-only fresh install), it logs and returns instead of raising.
- [tests/core/test_plugin_channels.py](../../tests/core/test_plugin_channels.py) gains a `TestTelegramPluginDelegation` class: imports the real telegram module, asserts it self-registers with the expected capabilities, and exercises all three push methods against mocked `bot` / `sessions` (no network).
- `make check` green, 1523 tests pass, coverage 91.94%.
- Pull sites (`api/chat.py`, `api/artifacts.py`, `api/components.py`, `api/conversations.py`) remain untouched — they get `resolve_user_slug` in stage 3.

### 2026-04-19 — stage 3: pull-side refactor (4 api sites)

- Added `resolve_user_slug(external_id: str) -> str | None` to the `ChannelPlugin` Protocol. Telegram's plugin implements it by delegating to `sessions.get_user_slug`. Channels without a separate identity space (e.g. plain WebSocket) return `None`.
- Dropped the `from marcel_core.channels.telegram.sessions import get_user_slug as get_telegram_user_slug` import from four API modules:
  - [src/marcel_core/api/chat.py](../../src/marcel_core/api/chat.py)
  - [src/marcel_core/api/components.py](../../src/marcel_core/api/components.py)
  - [src/marcel_core/api/artifacts.py](../../src/marcel_core/api/artifacts.py)
  - [src/marcel_core/api/conversations.py](../../src/marcel_core/api/conversations.py)
- Each site now does `tg_channel = get_channel('telegram'); slug = tg_channel.resolve_user_slug(str(tg_user['id'])) if tg_channel else None`. The auth helper (`verify_telegram_init_data`) stays in `marcel_core.auth` — it is generic auth surface, not channel-specific.
- [tests/core/test_api_endpoints.py](../../tests/core/test_api_endpoints.py) — 11 `patch('marcel_core.api.X.get_telegram_user_slug')` calls rewritten to patch `get_channel` with a `MagicMock` whose `resolve_user_slug` returns the test user slug. Added a small `_mock_tg_channel()` helper to keep the patterns tight.
- [tests/core/test_plugin_channels.py](../../tests/core/test_plugin_channels.py) gains a direct `resolve_user_slug` test against the telegram plugin with mocked sessions.
- `grep "marcel_core.channels.telegram"` in `src/` now shows only telegram's own internals, `main.py` (the import + router mount), and a Makefile CLI helper. Everything in `tools/` and `api/` has been migrated.
- `make check` green, 1528 tests pass, coverage 91.95%.
- Remaining stages: stage 4 migrates the telegram module to `<MARCEL_ZOO_DIR>/channels/telegram/` with channel.yaml + CHANNEL.md + discovery in main.py; stage 5 verifies fresh install behaviour with and without the zoo.

### 2026-04-19 — stage 4a: main.py mounts routers via registry

- [src/marcel_core/main.py](../../src/marcel_core/main.py) — replaced the hard-coded `from marcel_core.channels.telegram import router as telegram_router` + `app.include_router(telegram_router)` with two pieces: (a) a side-effect `import marcel_core.channels.telegram` to trigger self-registration, and (b) a loop over `list_channels()` that mounts each plugin's `.router` when present. Plugins without a router are silently skipped.
- The side-effect import is the one remaining direct reference to the telegram module in the kernel. It goes away in stage 4c when the habitat moves to `<MARCEL_ZOO_DIR>/channels/telegram/` and stage 4b's zoo-discovery loop takes over.
- Behaviourally identical today (same router mounted, same URLs). Sets up the discovery path so a second channel registering itself in stage 4b requires zero new lines in main.py.
- `make check` green, 1528 tests pass, coverage 91.96%.

### 2026-04-19 — stage 4b: zoo channel discovery

- [src/marcel_core/plugin/channels.py](../../src/marcel_core/plugin/channels.py) — added `discover()` and `_load_external_channel()`, mirroring `marcel_core.skills.integrations.discover` but scoped to `<MARCEL_ZOO_DIR>/channels/`. Each habitat is loaded under the private `_marcel_ext_channels.<name>` module namespace via `importlib.util.spec_from_file_location`. Failures in one habitat are logged and contained; siblings continue loading. Unset `MARCEL_ZOO_DIR` → silent no-op.
- [src/marcel_core/main.py](../../src/marcel_core/main.py) — calls `discover_channels()` at module load before the `list_channels()` mount loop, so zoo-hosted channel routers are available when FastAPI assembles. The telegram side-effect import stays for one more stage; stage 4c removes it after telegram moves into the zoo.
- [tests/core/test_plugin_channels.py](../../tests/core/test_plugin_channels.py) — new `TestDiscoverExternalChannels` with four cases: successful import, sibling failure isolation, unset zoo dir is a no-op, missing `channels/` subdir is a no-op. Tests patch `settings.marcel_zoo_dir` (the mutable pydantic field) rather than the computed `zoo_dir` property.
- `make check` green, 1532 tests pass, coverage 91.86%.

### 2026-04-19 — stage 4c: migrate telegram to zoo habitat

- Moved five modules (`__init__.py`, `bot.py`, `formatting.py`, `sessions.py`, `webhook.py`) and the `CHANNEL.md` prompt fragment out of `src/marcel_core/channels/telegram/` and `src/marcel_core/defaults/channels/telegram.md` into `<MARCEL_ZOO_DIR>/channels/telegram/` in the companion marcel-zoo repo (copy, not `git mv` — cross-repo history loss is the expected cost; attribution lives in this issue). The habitat ships with its own `channel.yaml` declaring `TELEGRAM_BOT_TOKEN` under `requires.credentials` and the full `capabilities` block.
- [src/marcel_core/main.py](../../src/marcel_core/main.py) — dropped the stage-4a side-effect `import marcel_core.channels.telegram`. The kernel now has zero references to a concrete channel transport; `discover_channels()` + the `list_channels()` mount loop handle every case.
- [src/marcel_core/channels/adapter.py](../../src/marcel_core/channels/adapter.py) — `_BUILTIN_RICH_UI_CHANNELS` reduced to `{'websocket', 'app', 'ios', 'macos'}`. Telegram's `rich_ui=True` now comes from the plugin's declared `ChannelCapabilities` at registration time.
- Deleted seven kernel-side test modules (`test_telegram*.py`, `test_formatting.py`, `test_webhook_scenarios.py`). Equivalent tests live in the zoo at `channels/telegram/tests/` with their own conftest that loads the habitat as `marcel_core.channels.telegram` so existing `patch('marcel_core.channels.telegram.*')` sites keep working.
- [conftest.py](../../conftest.py) — new `_load_zoo_telegram_at_legacy_namespace()` hook (pytest-only) that searches `MARCEL_ZOO_DIR`, `../marcel-zoo`, and `~/projects/marcel-zoo` for the habitat, loads it via `importlib.util.spec_from_file_location`, and aliases it under the legacy `marcel_core.channels.telegram` name so kernel tests that monkey-patch `marcel_core.channels.telegram.{bot,sessions}` continue to resolve. Runtime production code uses the private `_marcel_ext_channels.telegram` namespace via `discover()`; the alias is strictly a test-side convenience.
- [tests/core/test_conversations.py](../../tests/core/test_conversations.py), [tests/tools/test_integration_tools.py](../../tests/tools/test_integration_tools.py) — rewrote the half-dozen `from marcel_core.channels.telegram.sessions import ...` imports as `importlib.import_module('marcel_core.channels.telegram.sessions').<name>`. Static form would break pyright once the kernel module vanishes; importlib resolves at runtime against the conftest alias.
- [tests/core/test_plugin_channels.py](../../tests/core/test_plugin_channels.py) — deleted the `TestTelegramPluginDelegation` class. Those assertions now belong to the zoo habitat's own test suite.
- [Makefile](../../Makefile) — two fixes: (a) `test-cov` now runs `--cov=src/marcel_core` (path) rather than `--cov=marcel_core` (package), so pytest-cov stops tracing the zoo habitat files loaded via the conftest alias as uncovered kernel lines — this is what dropped reported coverage from 91.95% to 86.08% after the migration. (b) The `link-telegram` operator helper calls `marcel_core.plugin.channels.discover()` first and then imports from `_marcel_ext_channels.telegram.sessions`, the real runtime path (the conftest alias is test-only).
- [SETUP.md](../../SETUP.md), [docs/channels/telegram.md](../../docs/channels/telegram.md) — updated every operator-facing `link_user` example to `make link-telegram USER=... CHAT=...`, replaced the ad-hoc `set_webhook` / `delete_webhook` snippets with the `discover()`-then-`_marcel_ext_channels.telegram.bot` form, added a banner explaining the zoo-habitat location, and deleted the `::: marcel_core.channels.telegram.*` mkdocstrings references (the kernel package no longer exports them).
- `make check` green, 1337 tests pass, coverage 91.46%. 195 tests live in the zoo now, so the absolute test count is lower but each kernel line is still covered.

### 2026-04-19 — stage 4c follow-up: docs/plugins.md + zoo relative imports

Pre-close-verifier caught two items that belong with the last impl commit:

- [docs/plugins.md](../../docs/plugins.md) — Task 1 ("Document channel habitat + `channel.yaml` schema") was unmet. Added a full `## Channel habitat` section (directory layout, minimal example, `ChannelPlugin` protocol table, `channel.yaml` schema, discovery + error isolation) at parity with the existing integration/skill docs. Also corrected the Status callout — channels are now listed as shipped, ISSUE-7d6b3f removed from the roadmap list.
- Zoo habitat ([marcel-zoo commit 483ecda](../../../../marcel-zoo/channels/telegram/)) — the verifier noticed that `channels/telegram/__init__.py`, `bot.py`, and `webhook.py` still imported sibling modules via the absolute `from marcel_core.channels.telegram import ...` path. At runtime the kernel loads the habitat as `_marcel_ext_channels.telegram`, so absolute imports referencing the deleted kernel namespace would fail. Kernel `make check` only passed because the test-only conftest alias installs the habitat under the legacy name for pytest. Rewrote every non-test import inside the habitat as relative (`from . import bot, sessions`), which resolves correctly under either namespace. Kernel tests still green (1337 passed); zoo tests still green (190 passed).

No kernel source-code changes in this commit — only the kernel-side docs update lands here. The zoo-side fix is already committed upstream (`483ecda`).

## Lessons Learned

### What worked well

- **Five-stage slicing.** Cutting the migration into (1) scaffolding, (2) push sites, (3) pull sites, (4a) main.py mount loop, (4b) zoo discovery, (4c) physical move kept every step testable against the previous step's output. `make check` went green at every stage boundary and caught the one real issue (the coverage-scope trap at 4c) inside a narrow diff window rather than under a mountain of churn.
- **Plugin push/pull split.** Defining the push surface (`send_message` / `send_photo` / `send_artifact_link`) and the pull surface (`resolve_user_slug`) on the same `ChannelPlugin` Protocol let both directions migrate to registry lookups without adding a second abstraction. The `resolve_user_slug(external_id) -> str | None` shape covered every call site unchanged.
- **Registry-before-move.** Moving the plugin registry + discovery in stages 1–4b *before* touching the filesystem in 4c meant 4c was almost pure deletion on the kernel side: the discovery path was already proven against a concrete plugin (the kernel-hosted telegram self-register) before it ever had to load from the zoo.

### What to do differently

- **Run the verifier earlier.** Both findings the pre-close-verifier flagged — the missing `docs/plugins.md` section and the zoo habitat's absolute imports — were visible in the code days before the close. Invoking the verifier mid-stage instead of only at the gate would have caught them without a late scramble. Next migration, run it after stage-2-style milestones, not just before close.
- **Check both load paths for external habitats.** The zoo habitat's absolute `from marcel_core.channels.telegram import ...` imports passed tests because the conftest alias installed the habitat under the legacy name. They would have failed the moment the kernel loaded the habitat in production (where no alias exists). The takeaway: when a module is loadable under two different namespaces, the rule is *relative imports inside, absolute imports only for external kernel surfaces*. Write this into the channel habitat docs so future habitats start from the right pattern.
- **`--cov` scope is not an incidental flag.** `--cov=marcel_core` (package) and `--cov=src/marcel_core` (path) look interchangeable and behave identically until `sys.modules['marcel_core.X']` points at files outside `src/`. The conftest alias was the trigger here; any future sys.modules-level injection would reopen the same trap. Prefer the path form by default and justify the package form when it is needed.

### Patterns to reuse

- **Conftest alias for cross-repo migration with zero-change test imports.** `sys.modules['<old_name>'] = module` plus attaching to the parent package via `parent.child = module` (pytest's `monkeypatch.setattr` walks attribute chains, not just sys.modules) lets existing `patch('old.path')` and `from old.path import X` sites keep resolving after the module physically moves. Strictly a test-time pattern — production code must use the real runtime namespace. Reuse on future channel / integration / subagent migrations where the test suite has many deep patches.
- **`_marcel_ext_<kind>.<name>` private namespace.** Mirrors the integration loader's convention (`_marcel_ext_integrations`, `_marcel_ext_channels`) and keeps externally-loaded habitats syntactically distinct from kernel modules. Discoverable with one grep; impossible to confuse with a real top-level package.
- **Three-valued capability query.** `channel_has_rich_ui(name) -> bool | None` — registered True / registered False / unknown. The `None` bucket lets the caller fall back to a built-in set for kernel-native surfaces without the registered-False and unknown cases collapsing. Apply the same shape to any future registry whose "default" and "explicitly off" need to stay distinguishable.
