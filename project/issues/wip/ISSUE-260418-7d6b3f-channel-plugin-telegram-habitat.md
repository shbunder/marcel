# ISSUE-7d6b3f: Channel plugin contract + migrate Telegram habitat

**Status:** WIP
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

- [ ] Define channel habitat layout + `channel.yaml` schema. Document in `docs/plugins.md`.
- [ ] Define `marcel_core.plugin.channels` — registry, channel interface protocol, `get(name)`, `list()`, `capabilities(name)`.
- [ ] Spike: refactor `tools/marcel/notifications.py` + `tools/charts.py` to use the registry. Confirm the API is ergonomic.
- [ ] Refactor remaining 10 files that import `marcel_core.channels.telegram.*` to use the registry. File list at top of issue.
- [ ] Refactor `main.py` to discover channel habitats from `<data_root>/channels/` and mount their routers at `/channels/<name>/*` (or whatever path convention the current telegram webhook uses — preserve URLs).
- [ ] Migrate Telegram: move `src/marcel_core/channels/telegram/` → `~/.marcel/channels/telegram/`. Include CHANNEL.md from `defaults/channels/telegram.md`.
- [ ] Update the `_RICH_UI_CHANNELS` set in [channels/adapter.py](../../src/marcel_core/channels/adapter.py) — this currently hardcodes channel names; must move into the plugin contract (each channel declares `rich_ui: true` in its `ChannelCapabilities`).
- [ ] Move Telegram tests to the habitat's `tests/` dir.
- [ ] Kernel-side tests: a fake channel habitat exercised against the registry + main.py mounting flow.
- [ ] Docs: `docs/channels/` gets updated to reflect habitat layout; [docs/channels/telegram.md](../../docs/channels/telegram.md) links into the zoo.
- [ ] Verify: fresh install with no zoo → Telegram not mounted, server still boots (WebSocket + REST still work).
- [ ] Verify: webhook URL still resolves (don't change public URLs during the migration).

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

## Lessons Learned
<!-- Filled in at close time. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
