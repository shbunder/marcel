# ISSUE-6a38cd: Turn router module with slash-prefix tier selection and skill triggers

**Status:** Open
**Created:** 2026-04-17
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature, refactor, routing

## Capture
**Original request:**
> can you add a feature that the user can select the model by starting the message with /local /fast /standard /power
>
> yes, new issue
> lets make these for tiers then
> 0. local
> 1. fast
> 2. standard
> 3. power
>
> admin can select which tier is fallback for Marcel (default = 0)
> admin can select which tier is default for Marcel (default = 1)
> user can select tier to use. if not the regular method applies (switch that tries between default or 1 or 2)
> power can only be chosen through subagent (or skill explicetly requiring that tier)
>
> we should make a very clean and understandable module that handles this logic
>
> btw, skills should also be triggerable through /skillname

**Follow-up Q&A:**
- Q: Should `/local` map to the existing FALLBACK tier, skip it, or map to the `local:` model-pin prefix? **A:** Promote local to a proper tier (index 0); admin chooses which tier is the cloud-outage fallback, default is 0.
- Q: Should `/power` be user-selectable? **A:** No — power is reserved for subagents and skills that explicitly declare `preferred_tier = power`. A user message starting with `/power` must be politely rejected.
- Q: What should happen when a message starts with `/foo` and `foo` is neither a tier nor a known skill? **A:** Fall through to normal routing with the text intact. Don't error — users might legitimately start a sentence with `/`.

**Resolved intent:**
Today's tier resolution lives inside `runner._resolve_turn_tier` and is entangled with session state and classifier lookup. We want to extract that logic into a clean, dependency-injected module (`turn_router`) that resolves *one* question per turn: "given the text, the active skill, the session tier, and admin config — what tier runs this turn, and what text goes into the model?" The same module answers the new slash-prefix questions. Tiers become a public 0–3 index so admins and users can reason about them numerically, while internal constants stay unchanged to keep the diff surgical.

## Description

### Tiers (0–3)
| Index | Name     | Notes                                                            |
|-------|----------|------------------------------------------------------------------|
| 0     | local    | Local LLM. Previously only reachable as cloud-failure fallback.  |
| 1     | fast     | Haiku-class.                                                     |
| 2     | standard | Sonnet-class.                                                    |
| 3     | power    | Opus-class. Subagent/skill-only — never user-selectable.         |

### Admin config (new keys)
- `fallback_tier: int = 0` — tier used when the chosen tier fails. Replaces today's hardcoded FALLBACK.
- `default_tier: int = 1` — tier the classifier biases toward on a fresh session.

Location: wherever existing admin/system settings live (likely `~/.marcel/settings.json` or the env-backed config). Validate: both values must be in `{0, 1, 2}` — power cannot be default or fallback.

### Tier resolution precedence (highest wins)
1. **Skill-declared tier** — active skill has `preferred_tier` set (existing mechanism). Only path to power.
2. **User prefix** — `/local`, `/fast`, `/standard` on the current turn only (not persisted to session). `/power` is rejected.
3. **Session tier** — existing per-channel persistence.
4. **Classifier** — existing heuristic, constrained to `default_tier` or `default_tier + 1`, capped below power.

### Slash-command prefixes
- `/local`, `/fast`, `/standard` → one-shot tier override, strip prefix from text.
- `/power` → rejected with a short explanatory message; do not route to the model.
- `/<skillname>` → trigger the named skill directly, remaining text becomes the skill input. If the skill name is unknown, fall through to normal routing with the original text.
- Tier prefixes take precedence over skill names (fixed set: `local`, `fast`, `standard`, `power`).

### Module shape
`src/marcel_core/harness/turn_router.py`:

```python
@dataclass(frozen=True)
class TurnPlan:
    tier: Tier                      # resolved tier
    cleaned_text: str               # text to feed downstream (prefix stripped)
    skill_override: str | None      # if a /skillname triggered a skill
    reject_reason: str | None       # if turn should be short-circuited (e.g. /power)
    source: TierSource              # enum for logging: SKILL | USER_PREFIX | SESSION | CLASSIFIER

def resolve_turn(
    user_text: str,
    active_skill: Skill | None,
    session_tier: Tier | None,
    admin_config: AdminTierConfig,
    known_skills: Iterable[str],
) -> TurnPlan: ...
```

Pure function, no I/O, no globals. All tests hit this single surface.

### Entry points to wire up
- `src/marcel_core/channels/telegram/webhook.py` (~line 362) — call `resolve_turn` before `_process_assistant_message`. On rejection, respond via Telegram and return early. On skill override, route to the skill dispatcher.
- `src/marcel_core/api/chat.py` (~line 80) — same shape for the WebSocket path.
- `src/marcel_core/harness/runner.py` (~lines 453–510, 579) — replace `_resolve_turn_tier` with a thin wrapper that delegates; `stream_turn` accepts an optional `turn_plan` param so the channel can pass its pre-resolved plan without re-work.

### Scope boundaries
- Not changing the classifier heuristic.
- Not renaming internal `FAST`/`STANDARD`/`POWER` constants — only adding a LOCAL constant and numeric mapping at the public edge.
- No new user-facing power path, under any circumstances.

## Tasks
- [ ] Add `LOCAL` to the `Tier` enum (or equivalent) and a bidirectional numeric mapping (`0–3 ↔ Tier`)
- [ ] Add `AdminTierConfig` with `fallback_tier` and `default_tier`, defaulting to `0` and `1`; validate ∈ {0,1,2}
- [ ] Thread admin config through startup so it's available to `resolve_turn`
- [ ] Create `src/marcel_core/harness/turn_router.py` with `resolve_turn()` and `TurnPlan`
- [ ] Write unit tests for `turn_router`: each precedence level, each slash prefix, `/power` rejection, unknown skill fall-through, leading/trailing whitespace, empty message, prefix-only message
- [ ] Replace `runner._resolve_turn_tier` with delegation to `turn_router`
- [ ] Wire `turn_router` into Telegram webhook; handle `reject_reason` by sending the reject message and short-circuiting
- [ ] Wire `turn_router` into WebSocket `/api/chat`; handle `reject_reason` via the normal stream with a single assistant message
- [ ] Wire skill override into the existing skill dispatch path (Telegram + WebSocket)
- [ ] Replace hardcoded FALLBACK path in model_chain with `admin_config.fallback_tier`
- [ ] Integration test: Telegram webhook receives `/fast hello` → fast tier, text `hello`
- [ ] Integration test: WebSocket receives `/power anything` → rejection message, no model call
- [ ] Integration test: `/weather tomorrow` with a weather skill present triggers the skill with input `tomorrow`
- [ ] Update `docs/` page on model routing (tier table, precedence, prefixes)
- [ ] Document `fallback_tier` / `default_tier` admin keys in SETUP or admin guide
- [ ] Document user-facing slash prefixes (likely in a user-facing help doc and in the `/start` or help response)
- [ ] Run `make check` — all green

## Relationships
- Builds on: ISSUE-e0db47 (three-tier model routing with per-session classifier)

## Comments

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. Three subsections below — delete any that have nothing useful to say. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
