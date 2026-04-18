# ISSUE-7985aa: Local-tier warm-up timeout + "model warming up" ack

**Status:** Closed
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

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 6/6 tasks addressed (config, tier branch, ack swap, `/ws/chat` audit confirming no change needed, docs, tests)
- Shortcuts found: none
- Scope drift: none
- Stragglers: none — `"Working on it"`, `MARCEL_LOCAL_LLM_TIMEOUT`, and `_ASSISTANT_TIMEOUT` grep return only the intended callsites (plus one unrelated legacy test string in `tests/tools/test_integration_tools.py:164` under ISSUE-026)
- Safety bracket: `.claude/.unlock-safety` created/removed cleanly, not in the diff
- Note: the implementation dropped the 🔥 emoji and shortened "up to a minute" → "a minute" from the Design section. Code and docs agree; deviation captured in Lessons Learned rather than rewriting the Design block.

## Lessons Learned

### What worked well
- Delegating verification to `pre-close-verifier` caught the *absence* of the `🔥` / "up to a minute" wording that the Design section specified. Not a bug, but a useful paper-trail moment — the subagent flagged it as a note rather than a blocker, which is exactly the right temperature for wording drift between design and impl.
- Threading a pre-resolved `turn_plan` through `_process_assistant_message` via a `turn_plan: TurnPlan | None = None` kwarg kept every existing test entry point working while letting the delayed-ack task see the tier. Cheap concession, no test churn.
- Flipping **both** timeout knobs in opposite directions inside the integration tests (set the tier-under-test's budget to 0.01 and the other to 100.0) makes a leaky tier branch fail the test loudly, instead of silently picking the wrong constant.

### What to do differently
- When the Design section proposes user-visible copy, either commit to that copy verbatim in impl or update Design before the close commit. I ended up with a three-way reconciliation (Design vs. code vs. docs) that only the verifier caught. Next time: pick the final wording once, after the first implementation pass, and back-port it into the issue before close.
- The safety-restricted hook briefly blocked the config edit and required an unlock/relock bracket. For single-setting additions, that's the right friction. But if I'd batched this change with a second config setting later in the same branch, I'd have needed another unlock cycle — so plan config changes in a single edit per unlock window.

### Patterns to reuse
- `_ack_text_for(turn_plan)` / `_timeout_for(turn_plan)` — tiny pure helpers exported from the webhook module. Keeps the branching logic testable at unit-level without firing up the full `_process_assistant_message` harness. Use the same shape any time channel behavior depends on `turn_plan.tier`.
- When a feature only affects one of multiple channels, document *why* the other channels weren't touched right next to the code (see the ack-text constant's docstring referencing `/ws/chat`'s lack of `wait_for`). Saves the next reader a diff hunt.
