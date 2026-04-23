# ISSUE-f5796b: Extract kernel logic from channel habitats — thin-channel refactor

**Status:** Open
**Created:** 2026-04-23
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, plugin-system, channels, architecture

## Capture

**Original request (user, 2026-04-23):**

> I'd go for B) […] channel habitat should just be thin layer that prompts the agents how to use the channel, and does the necessary conversion logic that is necessary for the channel (but nothing more).
>
> For example I notice this logic in telegram channel:
> ```
> if text == '/start':
>     escaped_id = escape_html(str(chat_id))
>     await bot.send_message(
>         chat_id,
>         f'Hi! Your Telegram chat ID is <code>{escaped_id}</code>.\n\n'
>         f'Share this with your Marcel admin to link your account.',
>     )
>     return {'status': 'ok'}
> ```
> I'm confused why its there, this seems logic that should work across every channel? (how to handle slash-commands). The only logic here should be how to translate marcel event / agui output to the api of the telegram channel.

**Follow-up Q&A:**

During the architectural review that preceded this issue, Path B was chosen over three alternatives:

- **Path A** — drop UDS-for-channels entirely, leave the inprocess status quo (rejected: doesn't address the kernel-in-channel coupling).
- **Path B** (chosen) — invert the goal: pull kernel logic *out of* channel habitats back into the kernel so channels are genuinely thin transports.
- **Path C** — keep UDS as opt-in for future exotic channels (rejected as lazy: doesn't fix the root cause, only defers it).
- **Path D** — proceed with UDS-for-channels but measure cold start first (rejected: pays subprocess cost to confirm what evidence already suggests won't pay off).

**Resolved intent:**

Today's Telegram channel habitat runs *full agent turns* — [`webhook.py`](../../../../marcel-zoo/channels/telegram/webhook.py) imports `stream_turn`, `resolve_turn_for_user`, `extract_and_save_memories`, `create_artifact` and handles `/start` user-registration. That's kernel logic wearing a channel costume. This issue shrinks channel habitats to what is genuinely per-channel (webhook URL + provider auth, payload parsing, AG-UI/outbound-event translation, capability declaration) and moves everything else into a kernel-owned webhook processor. Telegram is the proving ground; Signal/Discord inherit the pattern.

## Description

### What changes, where

**Kernel gains a `ChannelProcessor` surface.** Probably `src/marcel_core/channels/processor.py` (confirm at wip). It receives a parsed `IncomingChannelEvent` from any channel and drives:

- Platform-command routing (`/start`, `/help`, `/reset`, `/whoami`) — channel-agnostic because every chat-style channel has this shape.
- Unknown-user / linking flow (today's Telegram `/start` hand-back-your-ID is the generic shape).
- `ChannelIdentity` lookup: `(channel_name, external_id) → user_slug`. Currently lives in Telegram's [`sessions.py`](../../../../marcel-zoo/channels/telegram/sessions.py); moves to kernel.
- `stream_turn` dispatch + delayed-ack ("typing" indicator) + memory extraction + artifact creation.
- Streams `OutboundChannelEvent`s back to the channel for translation to the provider API.

**Universal event shapes (new kernel types).**

- `IncomingChannelEvent` — `{channel, external_user_id, text, attachments, platform_command?, raw}`. The channel produces one per webhook.
- `OutboundChannelEvent` — text, text-delta (for streaming), photo, artifact-link, typing/progress. The kernel emits these; the channel translates to the provider's API shape.

**`ChannelPlugin` contract tightens.** Current surface (`router`, `send_message`, `send_photo`, `send_artifact_link`, `resolve_user_slug`) becomes:

- `router` — still channel-owned (webhook URL, provider auth header names).
- `parse_incoming(request) -> IncomingChannelEvent` — the only inbound translation the channel does.
- `emit(event: OutboundChannelEvent) -> None` — dispatches on event type internally.
- `capabilities` — unchanged; still how the agent learns what this channel supports.

**Telegram habitat shrinks.**

- [`webhook.py`](../../../../marcel-zoo/channels/telegram/webhook.py) (~423 lines today) → ~60 lines: validate HMAC, parse Telegram `Update` → `IncomingChannelEvent`, hand to `ChannelProcessor.handle()`, stream `OutboundChannelEvent`s back via `bot.send_*`.
- [`bot.py`](../../../../marcel-zoo/channels/telegram/bot.py) — unchanged (already pure Telegram Bot API wrapper).
- [`formatting.py`](../../../../marcel-zoo/channels/telegram/formatting.py) — unchanged (channel-specific markup).
- [`sessions.py`](../../../../marcel-zoo/channels/telegram/sessions.py) — shrinks dramatically; identity storage moves to kernel. Whatever remains must be genuinely Telegram-specific.
- All imports from `marcel_core.harness.*`, `marcel_core.memory.*`, `marcel_core.storage.artifacts` disappear from the habitat.

### Guardrail

New rule [`.claude/rules/channel-boundary.md`](../../../.claude/rules/channel-boundary.md): *channel habitats may not import from `marcel_core.harness` or `marcel_core.memory`*. Enforced by the `pre-close-verifier` and `code-reviewer` subagents. Prevents the next contributor from re-introducing the coupling this refactor removes.

### Docs

- `docs/channels.md` — replace the "UDS isolation — design" section with the thin-channel contract. Document the channel/kernel line explicitly; cite the new event types.
- `docs/habitats.md` — update the channel row: transport + translation, *not* agent-turn hosting.
- `docs/plugins.md` — if the `ChannelPlugin` Protocol is documented there, update it.

## Non-scope

- **Signal, Discord, email channels.** The refactor proves out on Telegram; other channels adopt the thin pattern when they're built. No speculative work here.
- **Reviving UDS for channels.** Settled — see cancelled [[ISSUE-092fd4]].
- **Removing the inprocess loader path.** Cancelled — see [[ISSUE-807a26]]. Inprocess *is* the right mode for channels.
- **Changing the AG-UI event stream shape inside the kernel.** Orthogonal. If the existing AG-UI events already map cleanly to `OutboundChannelEvent`, reuse them; don't invent a parallel shape.
- **Job habitats.** Jobs (scheduled work) are a separate question. This issue does not touch them; the cancelled [[ISSUE-931b3f]] Phase 3b (jobs) remains unresolved.

## Relationships

- Supersedes: [[ISSUE-092fd4]] (channel UDS Phase 3a — cancelled 2026-04-23)
- Supersedes: [[ISSUE-807a26]] (remove inprocess Phase 4 — cancelled 2026-04-23)
- Related to: [[ISSUE-931b3f]] (Phase 3 UDS design — its channel portion is invalidated by this refactor; the jobs portion is untouched)

## Tasks

- [ ] Define `IncomingChannelEvent` + `OutboundChannelEvent` shapes (names, fields, where they live)
- [ ] Create `marcel_core.channels.processor` module — `ChannelProcessor.handle(event)` drives platform-command routing, unknown-user flow, `stream_turn` dispatch, delayed-ack, memory extraction, artifact creation
- [ ] Move `ChannelIdentity` storage `(channel, external_id) → user_slug` from Telegram's `sessions.py` into a kernel-owned table
- [ ] Update `ChannelPlugin` Protocol: drop `send_message`/`send_photo`/`send_artifact_link`/`resolve_user_slug`, add `parse_incoming` + `emit`
- [ ] Update all kernel call sites that still use the old `ChannelPlugin` surface (grep `get_channel`, `channel.send_`, `channel.resolve_user_slug`)
- [ ] Refactor `channels/telegram/webhook.py` in the zoo — delete kernel imports, reduce to ~60 lines
- [ ] Shrink `channels/telegram/sessions.py` — identity lookup via kernel; keep only Telegram-specific session state
- [ ] Add `.claude/rules/channel-boundary.md` — channel habitats cannot import `marcel_core.harness` or `marcel_core.memory`
- [ ] Extend `pre-close-verifier` and `code-reviewer` subagent prompts to enforce the boundary rule
- [ ] Update `docs/channels.md` — replace UDS design section with thin-channel contract
- [ ] Update `docs/habitats.md` channel row (transport + translation, not agent-turn hosting)
- [ ] Update `docs/plugins.md` if `ChannelPlugin` Protocol is documented there
- [ ] Unit tests for `ChannelProcessor` — platform-command routing, unknown-user flow, identity lookup, stream_turn dispatch
- [ ] Unit tests for Telegram's `parse_incoming` — HMAC, command extraction, attachment handling
- [ ] End-to-end: real Telegram webhook → agent turn → reply lands in chat (same validation set the cancelled ISSUE-092fd4 called for, minus the UDS-specific checks)
- [ ] `make check` green
- [ ] `/finish-issue` → merged close commit on main

## Implementation Approach

Fill in at open→wip transition.

## Implementation Log
<!-- issue-task:log-append -->
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Delete any subsection with nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
