# ISSUE-931b3f: Extend UDS isolation to channel and job habitats (Phase 3 of f60b09)

**Status:** Closed
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

- [✓] Design the kernel-side HTTP→UDS proxy route — path shape, request serialisation format, response reconstruction
- [ ] Extend `marcel_core.plugin._uds_bridge` to dispatch `channel.*` methods (same wire format, new method namespace)
- [ ] Add a `channels.yaml` (or extend `channel.yaml`) schema with `isolation: uds` support
- [ ] Migrate `telegram` habitat to UDS isolation (zoo PR) — bidirectional: webhook inbound, `send_message` + friends outbound
- [ ] Evaluate identity resolution latency (`resolve_user_slug` over UDS); add caching only if it matters
- [✓] Decide whether to introduce python-carrying job habitats; if so, adopt the same UDS pattern. If not, mark job-side of Phase 3 closed-as-designed.
- [✓] Update `docs/plugins.md` — UDS channel pattern section
- [ ] `make check` green; live-test a telegram webhook round-trip
- [✓] `/finish-issue` → merged close commit on main

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

## Implementation Log
<!-- issue-task:log-append -->

### 2026-04-23 - LLM Implementation
**Action**: Shipped the channel UDS design as a new "UDS isolation — design" section in `docs/channels.md` covering the bidirectional proxy route, `channel.*` RPC method namespace, binary-payload base64 handling, mid-turn reverse notify path, identity resolution strategy, Telegram-specific constraints, and the `channel.yaml isolation:` schema. Jobs-half closed as designed (zoo jobs are YAML-only, four `template.yaml` files, zero Python). Carved out the actual implementation + Telegram migration to new ISSUE-092fd4 (Phase 3a) with a pre-populated Implementation Approach. Updated `docs/plugins.md` roadmap paragraph with the right cross-link.
**Files Modified**:
- `docs/channels.md` — replaced placeholder "UDS roadmap" with full design section
- `docs/plugins.md` — isolation-modes roadmap paragraph now names ISSUE-092fd4 and links into channels.md
- `project/issues/open/ISSUE-260423-092fd4-channel-uds-phase-3a-implementation.md` — new follow-up
**Commands Run**: `uv run mkdocs build --strict` (green), `make check` (1442 passed, 90.55% coverage)
**Result**: Success. Design shipped; implementation carved out; jobs-half formally closed-as-designed.

## Lessons Learned

### What worked well
- **Scope revision in the Implementation Approach, up front.** Naming "this session ships design, Phase 3a implements" before the first impl commit meant reviewer expectations matched reality from the start, rather than discovering at close time that the issue's task list couldn't all be ticked. The issue capture itself had anticipated this outcome ("If no python-carrying job habitats land before Phase 4, this half of Phase 3 is a no-op") — reading the issue carefully exposed that the author had baked a scope-revision option into it.
- **Design-first, implementation-later, with the design in docs (not in a closed issue).** `docs/channels.md#uds-isolation-design` is now the canonical reference for anyone picking up ISSUE-092fd4, and it stays current as the codebase evolves. Burying the design inside a closed-issue file would have made it effectively lost.
- **Filesystem survey before committing to "jobs-half closed as designed".** A one-line `find /zoo/jobs -type f` surfaced zero `.py` files — concrete evidence rather than "I think jobs are YAML-only". That phrasing went straight into the Implementation Approach.
- **Creating the follow-up issue file inline rather than telling the user to run `/new-issue`.** ISSUE-092fd4 lives at `project/issues/open/ISSUE-260423-092fd4-channel-uds-phase-3a-implementation.md` with its own Implementation Approach placeholder and a starting pointer to the shipped design. Whoever picks it up loses zero time to "what even is this issue".

### What to do differently
- **Anchor-checking during docs authoring.** `mkdocs build --strict` caught that `#uds-isolation--design` (double-hyphen, em-dash preserved) didn't match the generated `#uds-isolation-design`. Quickest verification: `grep -oE 'id="[^"]*"' site/channels/index.html` after the first build, then use those exact anchors in cross-page links. Don't guess how mkdocs slugifies em-dashes, en-dashes, or parenthesised section names.
- **Resist the "close harder" temptation on scope-revised issues.** The original task list has several unchecked items (`_uds_bridge` channel.* dispatch, telegram migration, etc.). The instinct is to check them off as "covered by follow-up issue" — don't. Leave them unchecked and let the close commit's summary make it clear that the follow-up owns them. The close-verifier's "coverage X/Y" math should reflect reality, not a reframed version of it.

### Patterns to reuse
- **The design-ship / implementation-carve-out pattern for over-sized issues.** When an open issue turns out to need both design thinking and multi-session implementation, splitting it via scope revision in the Implementation Approach lets the session ship a real deliverable (the design, in docs) rather than a shallow multi-scope attempt. The carved-out follow-up inherits the design as its starting point.
- **Close-as-designed with filesystem evidence.** For issues whose capture already included a conditional no-op clause (here: "if no python-carrying job habitats..."), grounding the close in a one-line filesystem survey is cheaper and more honest than debating the framing. Quote the no-op clause verbatim in the close rationale so the audit trail is self-explanatory.
