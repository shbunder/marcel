# ISSUE-931b3f: Extend UDS isolation to channel and job habitats (Phase 3 of f60b09)

**Status:** WIP
**Created:** 2026-04-22
**Assignee:** Claude
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

## Implementation Approach

**Scope revision up front.** The original task list conflates two very
different-sized pieces of work: (a) deciding what to do with job
habitats, which is trivial, and (b) designing + implementing +
migrating the channel UDS proxy, which is a multi-session effort. This
issue lands the design work and the trivial decisions; implementation
of the channel UDS proxy moves to a new carved-out follow-up.

### Jobs half — close-as-designed

A filesystem survey of the zoo (`find /home/shbunder/projects/marcel-zoo/jobs -type f`)
shows exactly four files: `check/template.yaml`, `digest/template.yaml`,
`scrape/template.yaml`, `sync/template.yaml`. Zero Python. Every job
habitat is a declarative YAML consumed by the scheduler; there is no
Python code to isolate.

The issue's own capture explicitly accounts for this:

> If no python-carrying job habitats land before Phase 4, this half of
> Phase 3 is a no-op — jobs stay YAML-only, and Phase 4 focuses on
> integrations + channels only.

That condition holds. The jobs-side of Phase 3 closes as designed.
ISSUE-807a26 (Phase 4) already scopes around integrations + channels
only, so no upstream change is required.

### Channels half — design shipped, implementation carved out

The channel UDS migration is architecturally the most complex piece
of the five-habitat isolation story. Honest sizing:

1. **Kernel-side HTTP→UDS proxy route.** Generic
   `POST /channels/{name}/{path:path}` that serialises the full
   FastAPI `Request` (headers + query + body + path) into a JSON-RPC
   `channel.webhook` call, forwards it over UDS, and reconstructs a
   FastAPI `Response` from the RPC return (status + headers + body).
   Requires careful handling of binary payloads (Telegram photo
   uploads), HMAC-validated headers
   (`X-Telegram-Bot-Api-Secret-Token`), and content-type preservation.
2. **Streaming.** A telegram webhook triggers a full agent turn,
   which can run for up to `_ASSISTANT_TIMEOUT = 120s` and
   mid-run may issue `marcel(action="notify")` calls that push
   progress messages back. Over UDS this needs either a long-lived
   RPC call (fine — the bridge's per-call connection already supports
   this) plus a **separate reverse push channel** for the notify
   calls, because the notify goes from habitat → kernel → habitat
   again (the habitat's own `send_message`). Two RPC shapes, not one.
3. **Telegram migration.** 1290 lines across `bot.py`, `formatting.py`,
   `webhook.py`, `sessions.py`, `__init__.py`. Webhook handler
   imports deep into `marcel_core.harness` — `stream_turn`,
   `resolve_turn_for_user`, `Tier`, memory helpers — so the habitat
   subprocess needs the full kernel surface installed. That's an
   `uv pip install -e $REPO_ROOT` in `toolkit/telegram/.venv` just
   like Phase 2, but the Python deps are larger than any toolkit.
4. **Live validation.** Telegram webhook requires a real Bot API
   token + Cloudflare Tunnel. Simulating locally with `curl` against
   the proxy route can validate the wire-level behaviour but won't
   exercise the HMAC validation or the Mini-App artifact path.

Each of those four bullets is a session's work. Bundling them into
one session would produce shallow work that fails integration.

**This session ships the design in `docs/channels.md`.** A new
"Channel UDS isolation — design" section documents:
- The `POST /channels/{name}/{path:path}` proxy route shape.
- The request/response serialisation format (JSON-RPC envelope,
  base64 for binary bodies, headers as `dict[str, str]`).
- How `channel.webhook`, `channel.send_message`, `channel.send_photo`,
  `channel.send_artifact_link`, and `channel.resolve_user_slug` sit
  as distinct method names in the existing JSON-RPC wire format.
- The mid-run notify push path (habitat → kernel → habitat) and why
  it needs its own reverse RPC.
- Identity resolution — start with every-call RPC; defer caching.
- Telegram-specific constraints (HMAC header, binary photo bodies,
  120-s agent turns, Mini-App artifact delivery).

The implementation lands in a new **ISSUE-xxxxx (Phase 3a: channel UDS
implementation)**, carved out at close time. Phase 4 (ISSUE-807a26)
already depends on "every Python habitat is UDS", so the dependency
order is: 3a ships the telegram migration → 807a26 removes the
inprocess path.

### Files touched (this session)

- `docs/channels.md` — new "Channel UDS isolation — design" section.
- `docs/plugins.md` — brief pointer to the channels design (the
  isolation-modes section currently says "channels in Phase 3"; this
  edit anchors it to `docs/channels.md`).
- New issue file in `project/issues/open/` for the channel UDS
  implementation follow-up.

No code changes on the kernel side; no zoo changes. This is a
design-only issue that unblocks the real implementation.

### Verification

- `mkdocs build --strict` green (design section is docs-only).
- `make check` green.
- Follow-up issue file present in `project/issues/open/` with a
  complete Implementation Approach so `/new-issue` isn't needed later
  when someone picks it up.
