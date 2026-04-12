# ISSUE-071: Skill auto-load should respect context, not re-inject every turn

**Status:** WIP
**Created:** 2026-04-12
**Assignee:** LLM
**Priority:** High
**Labels:** bug

## Capture

**Original request:** "My latest questions to Marcel on Telegram were not answered, can you check what went wrong?"
Follow-up after diagnosis: "yes, I expect Skills to be read if it's no longer in the context, but if it's still in the context there should not be any auto-reading of the skills"

**Follow-up Q&A:** Shaun confirmed: create an issue and fix the skill auto-load behavior plus the template noise.

**Resolved intent:** Two compounding bugs caused the Telegram agent to produce stub replies ("Let me check your calendar!") with no actual answer. (1) The `integration` tool auto-injects the full SKILL.md on every integration call in every new turn because `TurnState.read_skills` is a fresh per-turn set — even when the skill was already loaded via `marcel(read_skill)` in a previous turn (whose result is preserved in full via `_ALWAYS_KEEP_TOOLS`). (2) Every bundled SKILL.md contains the line `Help the user with: $ARGUMENTS` — an un-substituted Claude Code skill template artifact that gets injected into tool results and appears to confuse the model into ending the turn early after a bare acknowledgment. Fix both: prime `turn.read_skills` at turn start from the conversation history so the auto-load only fires when the docs are genuinely absent, and strip the template line from skill content at load time (plus delete it from the on-disk defaults so the files read cleanly).

## Description

**Observed failure** — on 2026-04-12 around 11:00 UTC, two Telegram messages ("When do I have time for a 3 hour gaming session?" and "Still busy?") were received by Marcel, streamed through the agent, and completed with only ~80 output tokens each. The model's replies were bare acknowledgments ("Let me check your calendar!" / "Sorry! Let me check your calendar now.") with no actual answer. Trace evidence in [seg-0004.jsonl](../../../../../.marcel/users/shaun/conversation/telegram/segments/seg-0004.jsonl):

1. Turn 1 (11:00): model called `marcel(read_skill, name=icloud)`, got back the SKILL.md (which starts with `Help the user with: $ARGUMENTS`), emitted "Let me check your calendar!" and ended the turn with no integration call.
2. Turn 2 (11:03): model called `integration(id=icloud.calendar, days=7)`. The integration **did return real calendar data** (6 events — visible in the paste at `~/.marcel/users/shaun/.pastes/0a285e2b…`). But the dispatcher **also prepended the entire icloud SKILL.md** as an `[Auto-loaded icloud skill docs]` prefix (because `turn.read_skills` had reset between turns). The model saw the prefix containing `Help the user with: $ARGUMENTS`, emitted "Sorry! Let me check your calendar now." and ended the turn.

**Root causes:**

1. [src/marcel_core/tools/integration.py:60-68](../../../src/marcel_core/tools/integration.py#L60-L68) auto-loads skill docs when the skill family is not in `ctx.deps.turn.read_skills`. That set is scoped to a single turn ([src/marcel_core/harness/context.py:17-29](../../../src/marcel_core/harness/context.py#L17-L29)) — by design, to isolate per-turn state — but it means the auto-load fires on every turn, even though `marcel` tool results (including `read_skill` outputs) are preserved in full across turns via `_ALWAYS_KEEP_TOOLS` in [runner.py:50](../../../src/marcel_core/harness/runner.py#L50). The auto-load is re-injecting docs that are already visible to the model.

2. Every bundled SKILL.md under [src/marcel_core/defaults/skills/](../../../src/marcel_core/defaults/skills/) (and the user data-root copies) contains the template line `Help the user with: $ARGUMENTS`, left over from the Claude Code skill format. `$ARGUMENTS` is never substituted, and the line has no meaning for Marcel — it's pure noise that the model can reasonably interpret as an unfilled instruction template.

## Tasks

- [✓] Prime `deps.turn.read_skills` at turn start from `message_history`, scanning for past `marcel(read_skill, name=X)` tool calls. Since marcel tool results are in `_ALWAYS_KEEP_TOOLS`, any skill loaded this way is guaranteed to still be in the model's context.
- [✓] Strip `Help the user with: $ARGUMENTS` lines from skill content at load time in [src/marcel_core/skills/loader.py](../../../src/marcel_core/skills/loader.py) so stale data-root copies are cleaned defensively.
- [✓] Delete `Help the user with: $ARGUMENTS` from the bundled default SKILL.md files (icloud, banking, news, docker).
- [✓] Also delete it from the user data-root copies so they don't drift.
- [✓] Add a runner test confirming `_prime_read_skills_from_history` populates the set from a past `marcel(read_skill)` call, and that an unrelated past tool call does not.
- [✓] Verify the existing `test_no_duplicate_inject_after_read_skill` still passes.
- [✓] Run `make check`.
- [ ] Document behavior in [docs/skills.md](../../../docs/skills.md) or wherever auto-load is mentioned.
- [ ] Version bump, push to `shaun`, trigger restart via `request_restart()`.

## Implementation Log

### 2026-04-12 11:40 - LLM Implementation
**Action**: Primed `turn.read_skills` from history + stripped `$ARGUMENTS` template noise.
**Files Modified**:
- `src/marcel_core/harness/runner.py` — added `_prime_read_skills_from_history(messages, read_skills)`, called from `stream_turn` right after `build_context`. Scans every `ModelResponse` for `ToolCallPart(tool_name='marcel', action='read_skill')` and adds the `name` argument to the set. Switched the signature to `Sequence[ModelMessage]` so tests can pass narrower `list[ModelResponse]` without variance errors.
- `src/marcel_core/skills/loader.py` — added `_strip_argument_template(body)` and routed both `_parse_frontmatter` branches through it. Drops any line exactly equal to `Help the user with: $ARGUMENTS` and trims leading blank lines. Preserves other mentions.
- `src/marcel_core/defaults/skills/{icloud,banking,news,docker}/SKILL.md` — deleted the `Help the user with: $ARGUMENTS` line so the bundled files read cleanly.
- `~/.marcel/skills/{icloud,banking,news,docker}/SKILL.md` — same deletion in the data-root copies so a lazy seed_defaults won't re-introduce drift.
- `tests/harness/test_runner.py` — new `TestPrimeReadSkillsFromHistory` with 5 cases (past read_skill populates set, non-read_skill marcel actions ignored, other tools ignored, multiple skills collected, existing entries preserved).
- `tests/core/test_skill_loader.py` — new `TestStripArgumentTemplate` plus two `TestParseFrontmatter` cases covering the frontmatter + no-frontmatter branches.
**Commands Run**: `make check`
**Result**: 1143 passed, 0 errors from pyright, ruff clean, total coverage 92.75%.
**Root cause recap**: the integration tool's auto-load safety net was firing every turn because `TurnState.read_skills` is constructed fresh per `MarcelDeps`, and nothing linked it to what was already visible in `message_history`. Combined with the unfilled Claude-Code `$ARGUMENTS` template bleeding into tool returns, the model was receiving what looked like a fresh-prompt preamble every turn and ending the turn after a bare acknowledgment.
**Next**: Update docs/skills.md, bump version, close the issue, push to `shaun`, `request_restart()`.
