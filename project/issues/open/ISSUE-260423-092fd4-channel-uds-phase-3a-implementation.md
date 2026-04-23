# ISSUE-092fd4: Channel UDS isolation — Phase 3a implementation

**Status:** Open
**Created:** 2026-04-23
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, plugin-system, isolation, channels, marcel-zoo

## Capture

**Implementation follow-up to [[ISSUE-931b3f]] (Phase 3 design).** That issue landed the full channel UDS design in [`docs/channels.md`](../../../docs/channels.md#uds-isolation--design) — HTTP-to-UDS proxy route shape, JSON-RPC method namespace, binary-payload handling, mid-turn reverse notify path, identity resolution approach, Telegram-specific constraints. This issue lands the **kernel implementation + Telegram habitat migration**.

**Resolved intent:** Make `isolation: uds` work for channel habitats end-to-end. Generic `POST /channels/{name}/{path:path}` proxy route in the kernel, `channel.*` RPC handlers in `_uds_bridge`, `channel.yaml` schema extension, and the first real migration — Telegram — complete with live-webhook validation against a real bot.

## Description

### Kernel implementation (this repo)

1. **Generic HTTP-to-UDS proxy route.** New module (probably `src/marcel_core/plugin/_channel_proxy.py`) that mounts `POST|GET|PUT|DELETE|PATCH /channels/{name}/{path:path}` for every UDS-isolated channel detected at startup. Each handler:
   - Reads the full FastAPI `Request` (method, path, query, headers, body bytes).
   - Serialises into the `channel.webhook` JSON-RPC shape defined in the design.
   - Forwards over the habitat's UDS socket using the existing `_uds_bridge` framing.
   - Reconstructs a FastAPI `Response` from the RPC return and returns it to the caller.

2. **`_uds_bridge` channel method namespace.** Extend `marcel_core.plugin._uds_bridge` to dispatch `channel.webhook`, `channel.send_message`, `channel.send_photo`, `channel.send_artifact_link`, `channel.resolve_user_slug`. The same wire format, new method names. The bridge needs a minimal ASGI-scope-builder for `channel.webhook` so habitat code can dispatch through its own `APIRouter` unchanged.

3. **Channel proxy registration.** `marcel_core.plugin.channels.discover()` branches on `isolation: uds`:
   - `inprocess` (default) → import `__init__.py` as today, `register_channel(plugin)` fires.
   - `uds` → spawn habitat subprocess via `_uds_supervisor.spawn_habitat` (reuse `habitat_python` / `_bridge_command` from the toolkit path), register a **proxy ChannelPlugin** whose `send_*` methods are RPC coroutines. Mount the generic proxy route at `/channels/{name}/*`.

4. **`channel.yaml` schema.** Add `isolation:` key (default `inprocess`, accepts `uds`). Validate at discovery; malformed value rolls back just that habitat.

5. **Per-habitat venv for channels.** `scripts/zoo-setup.sh` already walks both `toolkit/` and `integrations/`; extend it to also walk `channels/` with the same `isolation: uds` gate. Channel habitats need the full kernel surface installed because the Telegram webhook imports `marcel_core.harness.*` — same `uv pip install -e $REPO_ROOT` pattern as Phase 2.

### Zoo-side (cross-repo)

1. **`toolkit/telegram/pyproject.toml`** — wait, telegram is a channel habitat at `channels/telegram/`, not a toolkit. Declare its deps (httpx is kernel-transitive; no Telegram-specific non-kernel deps today). Empty-deps pyproject is fine — this migration is pure failure-isolation + concurrency-per-habitat.
2. **`channels/telegram/channel.yaml`** — add `isolation: uds`.
3. Verify the habitat's imports (`bot.py`, `webhook.py`, `sessions.py`, `formatting.py`) resolve correctly from inside the per-habitat venv.

### Live validation

This is the part that can't be skipped. The migration is not shipped until:

- A real Telegram webhook round-trip works end-to-end through the UDS proxy.
- HMAC validation via `X-Telegram-Bot-Api-Secret-Token` still rejects forged payloads.
- A mid-turn `marcel(action="notify")` call from inside a Telegram webhook-triggered agent turn pushes back through `channel.send_message` and lands in the user's chat.
- A `generate_chart` call delivers via `channel.send_photo` with the binary payload round-tripping correctly through base64.
- A `send_artifact_link` call opens the Mini App in Telegram.
- The supervisor restarts the telegram bridge after a `kill -9` and the next webhook lands normally.

## Tasks

- [ ] Implement `_channel_proxy.py` HTTP-to-UDS proxy route module
- [ ] Extend `_uds_bridge` with `channel.*` method dispatch (including ASGI scope builder for `channel.webhook`)
- [ ] Extend `marcel_core.plugin.channels.discover()` with the `isolation: uds` branch (spawn habitat + mount proxy + register RPC-backed `ChannelPlugin`)
- [ ] Add `isolation:` key to `channel.yaml` schema + validation
- [ ] Extend `scripts/zoo-setup.sh` to walk `<zoo>/channels/*/` for `isolation: uds` venv provisioning
- [ ] Unit tests: proxy request/response serialisation round-trip, binary payload base64 fidelity, header preservation (including HMAC)
- [ ] Zoo: migrate `channels/telegram/` (add `isolation: uds`, add `pyproject.toml`, verify imports)
- [ ] Live webhook test: real bot + Cloudflare Tunnel + HMAC check + mid-turn notify + chart delivery + Mini App artifact
- [ ] Cold-start measurement — if `spawn_habitat`'s 5-s socket timeout is insufficient for telegram's full import graph, bump it per-habitat
- [ ] Update `docs/channels.md` — flip "Phase 3a carved out" wording to "shipped", add a "Migrating a channel habitat to UDS" recipe mirroring the toolkit one in `docs/plugins.md`
- [ ] `make check` green
- [ ] `/finish-issue` → merged close commit on main

## Non-scope

- Removing the inprocess path entirely → [[ISSUE-807a26]] (Phase 4). This issue leaves inprocess channels working so operators can stay on them until they opt in.
- Signal/Discord channel habitats — orthogonal; any future channel adopts `isolation: uds` by default once this ships.
- Per-call connection pooling in the UDS proxy — evaluate after live-traffic measurement, not before.

## Relationships

- Follows: [[ISSUE-931b3f]] (Phase 3 design).
- Precedes: [[ISSUE-807a26]] (Phase 4 — remove inprocess). Phase 4 depends on this shipping so every Python habitat is UDS-isolated before the code path is removed.

## Implementation Approach

Fill in at open→wip transition. The design section in `docs/channels.md#uds-isolation--design` is the starting point — that section already names every method, serialisation rule, and constraint.
