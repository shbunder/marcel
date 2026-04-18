# ISSUE-7985aa: Local-tier warm-up timeout + "model warming up" ack

**Status:** WIP
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
- [✓] Audit [src/marcel_core/api/chat.py](../../../src/marcel_core/api/chat.py) for an equivalent timeout/ack path — no `asyncio.wait_for` and no delayed-ack; tokens stream directly, nothing to branch
- [✓] Add `marcel_local_llm_timeout: float = 300.0` to [src/marcel_core/config.py](../../../src/marcel_core/config.py)
- [✓] Branch [src/marcel_core/channels/telegram/webhook.py](../../../src/marcel_core/channels/telegram/webhook.py) on `turn_plan.tier == Tier.LOCAL` for both the timeout and the ack message
- [✓] Add / update tests in [tests/core/test_telegram_webhook.py](../../../tests/core/test_telegram_webhook.py) covering: local-tier ack text, cloud-tier ack text, local-tier timeout reads `marcel_local_llm_timeout`, cloud-tier timeout unchanged, delayed-ack uses warmup text for `/local` and generic text for `/fast`
- [✓] Update [docs/local-llm.md](../../../docs/local-llm.md) with the new env var + warm-up behavior
- [✓] `make check` green (coverage 91.95%, 1513 tests pass)

## Relationships
<!-- none known -->

## Comments

## Implementation Log

### 2026-04-18 - LLM Implementation
**Action**: Branched Telegram assistant timeout + delayed-ack text on `turn_plan.tier == Tier.LOCAL`. Introduced `MARCEL_LOCAL_LLM_TIMEOUT` env var (default 300s) so deployments can tune the warm-up budget per hardware.

**Files Modified**:
- `src/marcel_core/config.py` — added `marcel_local_llm_timeout: float = 300.0` (unlock-safety gated; unlock/relock bracketed the single edit)
- `src/marcel_core/channels/telegram/webhook.py` — added `_ACK_CLOUD` / `_ACK_LOCAL_WARMUP` constants, `_ack_text_for()` + `_timeout_for()` helpers, moved `resolve_turn_for_user()` call into `_process_with_delayed_ack` so the ack task sees the tier, and passed the pre-resolved `turn_plan` down into `_process_assistant_message` (new `turn_plan=None` kwarg — internal resolution is preserved when the caller is a test)
- `tests/core/test_telegram_webhook.py` — added `TestLocalWarmup` with 8 cases: pure `_ack_text_for`/`_timeout_for` tables over all four tiers, integration checks that `/local hello` fires the local timeout while `/fast hello` keeps `_ASSISTANT_TIMEOUT`, and that the delayed-ack picks the tier-appropriate text
- `docs/local-llm.md` — documented the new env var and the warm-up behavior; noted that `/ws/chat` has no overall wait_for so the branch doesn't apply there

**Commands Run**: `make check` → 1513 passed, coverage 91.95%

**Design note — why `/ws/chat` wasn't touched**: Unlike the Telegram webhook, [src/marcel_core/api/chat.py](../../../src/marcel_core/api/chat.py) streams tokens out as they arrive with no `asyncio.wait_for` wrapper and no delayed-ack — there's nothing to branch. The OpenAI SDK's own 10-minute default HTTP timeout already bounds the client-side wait, well above any cold-start.

**Design note — tier resolution moved up**: The ack task has to know the tier before it fires, so `resolve_turn_for_user` is now called in `_process_with_delayed_ack` and the resolved plan is threaded through. The `turn_plan` kwarg on `_process_assistant_message` defaults to `None` so the existing test entry points (which call that function directly) still work without resolving the plan themselves.

**Result**: Success. `/local` turns get a 300s budget and "Warming up the local model…" ack; cloud turns keep the 120s budget and "Working on it…" ack.

## Lessons Learned
<!-- Filled in at close time -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
