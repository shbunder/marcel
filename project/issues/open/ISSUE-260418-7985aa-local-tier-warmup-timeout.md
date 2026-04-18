# ISSUE-7985aa: Local-tier warm-up timeout + "model warming up" ack

**Status:** Open
**Created:** 2026-04-18
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** bug, ux

## Capture
**Original request:** I noticed that when I do /local and ask it questions it fails. this probably has something to do with the warm-up time of local models. Can we take this into account for the time-out window? also can we notify the user that the model is warming up

**Follow-up Q&A:**
- Q: Tunable per deployment, or a hardcoded bump? → A: New `MARCEL_LOCAL_LLM_TIMEOUT` env var, default 300s.

**Resolved intent:** The Telegram channel has a 120s assistant timeout and a generic 10s "Working on it..." ack — neither accounts for Ollama's cold-start (30–60s to first token on a 14B) plus ~3–5 tok/s generation on CPU. When a turn resolves to the LOCAL tier, bump the overall timeout to `MARCEL_LOCAL_LLM_TIMEOUT` (default 300s) and swap the delayed-ack text for a warm-up message so the user understands why it's slow. Cloud-tier behavior is unchanged.

## Description

### Where it breaks today
- [src/marcel_core/channels/telegram/webhook.py:43-46](../../../src/marcel_core/channels/telegram/webhook.py#L43-L46): `_ASSISTANT_TIMEOUT=120.0`, `_ACK_DELAY=10.0`, ack text `'Working on it...'`.
- Cold-start behavior for `ministral-3:14b` on a Core Ultra 5 125H: first token after 30–60s, generation 3–5 tok/s — documented in [docs/local-llm.md:105](../../../docs/local-llm.md#L105).
- `/api/chat` WebSocket at [src/marcel_core/api/chat.py](../../../src/marcel_core/api/chat.py) — needs audit for an equivalent knob.

### Design
- **One new setting** in [src/marcel_core/config.py](../../../src/marcel_core/config.py): `marcel_local_llm_timeout: float = 300.0`.
- **Branch on tier** in the channel adapters: when `turn_plan.tier == Tier.LOCAL`, use the local timeout and the warm-up ack text; otherwise use the existing constants unchanged.
- **Warm-up ack text:** "🔥 Warming up the local model — this can take up to a minute..." Shown via the same delayed-ack mechanism already in place.
- **Cloud tier unchanged.** A hung cloud turn should still fail at 120s.

### Out of scope
- Changing the cold-start itself (Ollama `OLLAMA_KEEP_ALIVE` tuning is runtime, covered by [docs/local-llm.md:160](../../../docs/local-llm.md#L160)).
- Progress indicators during streaming — the existing delayed-ack is the only touch-point.

## Tasks
- [ ] Audit [src/marcel_core/api/chat.py](../../../src/marcel_core/api/chat.py) for an equivalent timeout/ack path and mirror the branch there if one exists
- [ ] Add `marcel_local_llm_timeout: float = 300.0` to [src/marcel_core/config.py](../../../src/marcel_core/config.py)
- [ ] Branch [src/marcel_core/channels/telegram/webhook.py](../../../src/marcel_core/channels/telegram/webhook.py) on `turn_plan.tier == Tier.LOCAL` for both the timeout and the ack message
- [ ] Add / update tests in [tests/core/test_telegram_webhook.py](../../../tests/core/test_telegram_webhook.py) (or a new scenarios file) covering: local-tier ack text, local-tier timeout is `marcel_local_llm_timeout` not `_ASSISTANT_TIMEOUT`, cloud-tier path unchanged
- [ ] Update [docs/local-llm.md](../../../docs/local-llm.md) with the new env var + warm-up behavior
- [ ] `make check` green (90% coverage floor holds)

## Relationships
<!-- none known -->

## Comments

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
