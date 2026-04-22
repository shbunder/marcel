# ISSUE-931b3f: Extend UDS isolation to channel and job habitats (Phase 3 of f60b09)

**Status:** Open
**Created:** 2026-04-22
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, plugin-system, isolation, channels, jobs

## Capture

**Follow-up to [[ISSUE-f60b09]] (Phase 1) and [[ISSUE-14b034]] (Phase 2).** After integrations run under UDS isolation, two python-carrying habitat kinds still load in-process: channels and jobs. This issue extends the same pattern to both, completing UDS coverage for every habitat kind that runs python code.

**Resolved intent:** Channel habitats (Telegram today) and python-carrying job habitats gain the same `isolation: uds` mode as integrations. Channels are architecturally harder because they are **bidirectional** — the kernel receives webhooks on an HTTP route that must reach into the habitat, and the kernel pushes outbound messages (`send_message`, `send_photo`, `send_artifact_link`) at the habitat. The HTTP-webhook side needs a kernel-side proxy route. The outbound side fits the existing JSON-RPC-over-UDS shape.

## Description

### Channel habitats — the bidirectional problem

A channel plugin today exposes:

- A FastAPI router (the webhook endpoint, e.g. `/telegram/webhook`) — mounted by the kernel's `for plugin in list_channels(): app.include_router(plugin.router)`.
- `send_message`, `send_photo`, `send_artifact_link`, `resolve_user_slug` — kernel-side push methods.

Neither side is a problem for inprocess channels: the router is a Python object, the methods are regular async functions. Under UDS isolation, both directions need a bridge.

**Inbound (HTTP → habitat):** the kernel owns the HTTP server. For a UDS-isolated channel habitat, the kernel mounts a generic proxy route (e.g. `POST /channels/{channel_name}/{...path:path}`) that:

1. Serializes the FastAPI `Request` — headers, path, query, body — into a JSON-RPC `channel.webhook` call.
2. Forwards over UDS to the channel habitat.
3. Deserializes the RPC response (status code, headers, body) back into a FastAPI `Response`.

The channel habitat's bridge exposes a `channel.webhook` handler that runs its existing webhook logic.

**Outbound (kernel push → habitat → network):** the existing `ChannelPlugin` protocol methods (`send_message`, `send_photo`, …) become UDS RPC calls. Same shape as integrations — kernel-registered proxy coroutines forward to the habitat. Capabilities (`rich_ui`, `attachments`, …) declared in `channel.yaml` are read by the kernel at spawn time so `channel_has_rich_ui()` can answer without round-tripping.

**Identity resolution (`resolve_user_slug`)** is read-heavy. For telegram it reads `~/.marcel/users/*/profile.md` frontmatter. Over UDS: either (a) each call is an RPC, adding latency to the hot path of every inbound webhook, or (b) the habitat caches the mapping and the kernel signals a refresh on user-add events. Start with (a); optimise if profiling shows it matters.

### Job habitats — simpler case

Job habitats at `<zoo>/jobs/<name>/template.yaml` are YAML-only today (see [src/marcel_core/plugin/jobs.py](src/marcel_core/plugin/jobs.py)). They carry no python code — just a `system_prompt:`, `default_trigger:`, etc. consumed by the kernel's scheduler.

If Phase 3 introduces python-carrying job habitats (a hook called `before_run:` or `format_result:`), they should adopt the same UDS pattern as integrations — `isolation: uds` in `template.yaml`, handler registered via `@register`, dispatched over UDS.

If no python-carrying job habitats land before Phase 4, this half of Phase 3 is a no-op — jobs stay YAML-only, and Phase 4 focuses on integrations + channels only.

## Tasks

- [ ] Design the kernel-side HTTP→UDS proxy route — path shape, request serialisation format, response reconstruction
- [ ] Extend `marcel_core.plugin._uds_bridge` to dispatch `channel.*` methods (same wire format, new method namespace)
- [ ] Add a `channels.yaml` (or extend `channel.yaml`) schema with `isolation: uds` support
- [ ] Migrate `telegram` habitat to UDS isolation (zoo PR) — bidirectional: webhook inbound, `send_message` + friends outbound
- [ ] Evaluate identity resolution latency (`resolve_user_slug` over UDS); add caching only if it matters
- [ ] Decide whether to introduce python-carrying job habitats; if so, adopt the same UDS pattern. If not, mark job-side of Phase 3 closed-as-designed.
- [ ] Update `docs/plugins.md` — UDS channel pattern section
- [ ] `make check` green; live-test a telegram webhook round-trip
- [ ] `/finish-issue` → merged close commit on main

## Non-scope

- Removing the inprocess path entirely → [[ISSUE-807a26]] (Phase 4)
- Signal/Discord channel habitats (separate feature, orthogonal to isolation)
- Per-habitat rate limiting / auth (separate concern)

## Relationships

- Follows: [[ISSUE-14b034]] (Phase 2 — integrations migration)
- Precedes: [[ISSUE-807a26]] (Phase 4 — remove inprocess)
