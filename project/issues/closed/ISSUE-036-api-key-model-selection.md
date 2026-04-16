# ISSUE-036: API Key Auth + Per-Channel Model Selection

**Status:** Closed
**Created:** 2026-04-09
**Assignee:** Shaun Bundervoet
**Priority:** High
**Labels:** feature

## Capture
**Original request:** "I added an anthropic api key and openai api key in .env.local. Let's just use this approach, and clean up all obsolute code. Allow to run marcel with both anthropic or openai models, allow the user to change the model in marcel and ask for a list of available models. a model should be set for each channel, have sonnet be the default."

**Resolved intent:** Replace the OAuth token approach with a clean API key-based auth strategy. Remove the OAuth fallback code entirely. Support both Anthropic and OpenAI models via their respective API keys. Add per-channel model persistence (each channel stores its own model preference in the user's settings file). Expose list-models and set-model commands to users via Marcel's natural language interface. Default to claude-sonnet-4-6.

## Description

The previous OAuth approach was exploratory. The user now has `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` in `.env.local`. This is simpler and more standard.

Additionally, Marcel should allow users to switch models per-channel (e.g., "use opus for telegram", "switch to gpt-4o") and persist those choices in `~/.marcel/users/{slug}/settings.json`. Models should be listed in a canonical registry so Marcel can respond to "what models are available?".

## Tasks
- [✓] Delete `src/marcel_core/harness/oauth.py` and `tests/harness/test_oauth.py`
- [✓] Clean up `agent.py` `_create_anthropic_model` — remove OAuth fallback, keep Bedrock and API key paths
- [✓] Add a canonical model registry (Anthropic + OpenAI models) to `agent.py`
- [✓] Create `src/marcel_core/storage/settings.py` with `load_channel_model` / `save_channel_model` backed by `~/.marcel/users/{slug}/settings.json`
- [✓] Create `src/marcel_core/skills/integrations/settings.py` with `settings.get_model`, `settings.set_model`, `settings.list_models` handlers
- [✓] Create `.marcel/skills/settings/SKILL.md` documenting the model commands
- [✓] Update `src/marcel_core/harness/runner.py` to load channel model from settings storage
- [✓] Write tests for `storage/settings.py` and the integration handlers
- [✓] Run `make check` and verify all tests pass

## Relationships
- Supersedes the OAuth approach implemented in ISSUE-034

## Implementation Log

### 2026-04-09 - LLM Implementation
**Action**: Replaced OAuth auth with API key approach; added per-channel model selection
**Files Modified**:
- `src/marcel_core/harness/oauth.py` — deleted (OAuth approach removed)
- `tests/harness/test_oauth.py` — deleted
- `src/marcel_core/harness/agent.py` — removed OAuth fallback; added `ANTHROPIC_MODELS`, `OPENAI_MODELS`, `DEFAULT_MODEL` registry; `_create_anthropic_model` now handles Bedrock > OpenAI+key > Anthropic+key > OpenAI fallback
- `src/marcel_core/harness/runner.py` — loads channel model from settings storage before falling back to `DEFAULT_MODEL`
- `src/marcel_core/storage/settings.py` — created; `load_channel_model` / `save_channel_model` backed by `~/.marcel/users/{slug}/settings.json`
- `src/marcel_core/skills/integrations/settings.py` — created; `settings.list_models`, `settings.get_model`, `settings.set_model` handlers
- `.marcel/skills/settings/SKILL.md` — created; documents model commands for Marcel
- `tests/core/test_settings.py` — created; 14 tests (storage roundtrip, isolation, integration handlers)
**Result**: 289 tests passing; typecheck errors are all pre-existing (count reduced from 18 → 15)

**Reflection**:
- Coverage: 9/9 requirements addressed
- Shortcuts found: none
- Scope drift: none

## Lessons Learned

### What worked well
- Stashing changes to verify pre-existing typecheck errors before blaming our code — confirmed 18 errors existed before, our changes reduced to 15
- The `_load_settings` / `_save_settings` split with `atomic_write` follows existing storage module patterns perfectly; easy to extend settings later

### What to do differently
- OAuth exploration added significant code that then had to be cleanly deleted; if API keys were available from the start the detour would have been skipped
- **The `ANTHROPIC_MODELS` / `OPENAI_MODELS` / `_resolve_model_string` registry (added here) was removed in ISSUE-073** — it duplicated provider-selection logic that pydantic-ai already does natively via `provider:model` strings. A curated registry is fine for UX, but it shouldn't double as the dispatch layer.

### Patterns to reuse
- Per-user JSON settings at `~/.marcel/users/{slug}/settings.json` via `atomic_write` is the right pattern for lightweight user preferences that don't warrant a full DB
- Integration handler pattern: `@register("settings.action")` + `async def fn(params: dict, user_slug: str) -> str` — clean, discoverable, testable in isolation
