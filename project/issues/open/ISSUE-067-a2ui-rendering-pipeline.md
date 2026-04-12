# ISSUE-067: A2UI Rendering Pipeline — End-to-End Wiring

**Status:** Open
**Created:** 2026-04-12
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature, architecture

## Capture
**Original request:** "I asked for my latest transaction to Marcel on telegram, I expected to see a rich transaction list using the laster A2UI setup (I see the components listed in the skill-folder), why did this not happen? Am I missing something?"

**Follow-up Q&A:** Investigated the codebase and confirmed that A2UI rendering is declared (components.yaml under the banking skill) but is not wired up end-to-end. Four independent gaps identified across agent prompt, runner, channel, and renderer. User reviewed the diagnosis and asked to open a dedicated issue to scope the work.

**Resolved intent:** Build the missing plumbing so that A2UI component schemas declared in `components.yaml` (today: `transaction_list`, `balance_card`) can actually flow from the agent's reasoning, through the runner's event stream, out a channel that advertises rich-UI capabilities, and render in a client. The canonical test case is "show my latest transaction" on Telegram rendering a `transaction_list` component in the Mini App instead of a plain-text summary.

## Description

Component schemas are co-located with skills (e.g. [src/marcel_core/defaults/skills/banking/components.yaml](../../src/marcel_core/defaults/skills/banking/components.yaml)), but nothing carries them through the stack. The agent never sees them, there's no tool to emit them, the runner never yields an `A2UIComponent` event, Telegram only consumes `TextDelta`, and no client-side path renders them. Each gap is independently necessary; closing only some of them produces zero user-visible improvement.

See [/home/shbunder/.claude/plans/glistening-knitting-wombat.md](../../.claude/plans/glistening-knitting-wombat.md) for the full diagnosis with file paths and line references.

### The four gaps

1. **Component schemas are not exposed to the agent.** The system prompt builder at [src/marcel_core/harness/context.py:157-236](../../src/marcel_core/harness/context.py#L157-L236) loads MARCEL.md, user profile, skill index, memories, and channel prompt — but never walks `components.yaml` for loaded skills. The model literally doesn't know `transaction_list` exists. Neither [src/marcel_core/defaults/skills/banking/SKILL.md](../../src/marcel_core/defaults/skills/banking/SKILL.md) nor [src/marcel_core/defaults/channels/telegram.md](../../src/marcel_core/defaults/channels/telegram.md) mentions A2UI components.

2. **No emission path.** [src/marcel_core/storage/artifacts.py:17-31](../../src/marcel_core/storage/artifacts.py#L17-L31) defines `ContentType` with `'a2ui'` and an optional `component_name` — but nothing creates such an artifact. There is no `marcel` tool action (analogous to `generate_chart`) that takes a component name + props. The runner defines an `A2UIComponent` event type at [src/marcel_core/harness/runner.py](../../src/marcel_core/harness/runner.py) but never yields one.

3. **Telegram is text-only and bypasses the capability system.** [src/marcel_core/channels/telegram/webhook.py:114-117](../../src/marcel_core/channels/telegram/webhook.py#L114-L117) consumes only `TextDelta` events. Telegram does not go through the `ChannelAdapter` protocol, so `ChannelCapabilities.rich_ui` at [src/marcel_core/channels/adapter.py:23-40](../../src/marcel_core/channels/adapter.py#L23-L40) is dead code on the Telegram path. Rich-content detection at [src/marcel_core/channels/telegram/bot.py:268-294](../../src/marcel_core/channels/telegram/bot.py#L268-L294) is regex-only (markdown tables, checklists, calendars) with no A2UI awareness.

4. **No client-side renderer for A2UI components on Telegram.** Even if an `A2UIComponent` event reached Telegram, nothing maps `transaction_list` + props → a Mini App URL, inline keyboard, or HTML message. Mini App support today is limited to checklists.

## Tasks
- [ ] **Schema exposure** — Walk `components.yaml` for loaded skills at system-prompt build time and inject a concise "available A2UI components" section. Gate on the active channel's `rich_ui` capability so text-only channels don't get prompted to emit unrenderable components.
- [ ] **Route Telegram through `ChannelAdapter`** — Make Telegram use the adapter protocol so `ChannelCapabilities.rich_ui` is a single source of truth consulted by the prompt builder. Today Telegram bypasses this entirely.
- [ ] **Add a `marcel` action to emit A2UI artifacts** — e.g. `marcel(action="render", component="transaction_list", props={...})`. Validate `component` against the loaded `components.yaml` schemas; write a `content_type='a2ui'` artifact via `storage/artifacts.py`; yield an `A2UIComponent` event through the runner.
- [ ] **Yield `A2UIComponent` from the runner** — wire the event type that already exists in `harness/runner.py` so it actually flows through `stream_turn`.
- [ ] **Teach the Telegram webhook to consume `A2UIComponent` events** — at minimum, store the artifact and send a "View transactions" Mini App button; fall back to a text representation if Mini App rendering is unavailable for that component.
- [ ] **Telegram Mini App renderer for `transaction_list` and `balance_card`** — extend the Mini App beyond checklists so these two components render natively. This is the client-side piece that closes the loop.
- [ ] **Update `banking/SKILL.md`** — instruct the model to prefer `transaction_list` / `balance_card` over plain text when the channel advertises `rich_ui`.
- [ ] **Update `channels/telegram.md`** — document the A2UI delivery mode alongside the existing plain-text, `generate_chart`, and Mini App checklist modes.
- [ ] **End-to-end test** — manual Telegram test of "show my latest transaction" should render a `transaction_list` component in the Mini App. Document the verification in the Implementation Log.
- [ ] **Websocket channel parity check** — confirm the websocket channel (which already has `rich_ui=True`) also works end-to-end with the same emission path, so the feature is not Telegram-specific.

## Relationships
- Related to: [[ISSUE-066-post-065-audit-cleanup]] — architecture tightening; there may be overlap on the channel adapter / prompt builder cleanup.

## Comments

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
