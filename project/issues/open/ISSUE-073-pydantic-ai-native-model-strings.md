# ISSUE-073: Simplify Model Routing via Pydantic-AI Native `provider:model` Strings

**Status:** Open
**Created:** 2026-04-13
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** refactor, simplification

## Capture
**Original request:** "can we simplify and use the pydantic-ai native system to switch models. by just saying anthropic/claude-sonnet-4-6 or openai/gpt5.2-mini the provider automatically choses the correct model? Is this possible (this would simplify the model routing logic at our end and natively support all models."

**Follow-up Q&A:**
- Q: Should `set_model` whitelist against a registry or accept any `provider:model` string?
  A: Shaun asked what pydantic-ai actually supports — confirmed it natively accepts any `provider:model` string (colon separator, not slash). Decision: **accept any qualified string**, registry is advisory only.
- Q: Preserve `AWS_REGION` → Bedrock auto-routing?
  A: **Drop entirely.** Users who want Bedrock store `bedrock:<id>` explicitly in settings.

**Resolved intent:** Marcel's `_resolve_model_string()` layer in [src/marcel_core/harness/agent.py](../../../src/marcel_core/harness/agent.py#L58-L99) predates pydantic-ai's native `provider:model` support. It maintains hardcoded registries (`ANTHROPIC_MODELS`, `OPENAI_MODELS`, `_BEDROCK_MODEL_MAP`), duplicates provider auto-selection logic, and causes the same short name to resolve to different providers depending on deployment env vars. This issue replaces the whole layer with direct pass-through: qualified strings like `"anthropic:claude-sonnet-4-6"` are stored in settings and passed verbatim to `Agent()`. Any pydantic-ai-supported model becomes usable without code changes. Memory agents (selector/extract/summarizer) currently pass bare short names to `Agent()` which is malformed — they get fixed as part of this change.

## Description

**What:** Delete Marcel's model routing layer and let pydantic-ai handle provider dispatch natively.

**Why:** Three concrete problems with the current system:
1. Adding a new model (e.g. `openai:gpt-5`) requires editing the hardcoded registries in `agent.py`.
2. The same stored name can resolve to Bedrock, Anthropic, or OpenAI depending on which env vars exist — surprising and hard to debug.
3. Memory agents currently pass bare `'claude-haiku-4-5-20251001'` to `Agent()`, which is not a valid pydantic-ai model string — they work only by accident.

**Approach:** See approved plan at `/home/shbunder/.claude/plans/curious-skipping-leaf.md`. Summary:
- `DEFAULT_MODEL` → `'anthropic:claude-sonnet-4-6'`; pass directly to `Agent()`
- Delete `_resolve_model_string()`, `_BEDROCK_MODEL_MAP`, `ANTHROPIC_MODELS`, `OPENAI_MODELS`
- Keep a slim `KNOWN_MODELS` dict as **display-only** suggestions for `list_models`
- `set_model` validation becomes shape-only: must contain `:` with non-empty halves
- Self-healing migration in `_load_settings()`: unqualified stored names get `anthropic:` prepended transparently on next load
- Fix memory agents to use qualified strings

**Not changing:** WebSocket API surface, `stream_turn()` priority logic, tool/skill action surface.

## Tasks
- [ ] Update [src/marcel_core/harness/agent.py](../../../src/marcel_core/harness/agent.py): delete `_resolve_model_string`, `_BEDROCK_MODEL_MAP`; collapse `ANTHROPIC_MODELS`/`OPENAI_MODELS` into `KNOWN_MODELS` with qualified keys; set `DEFAULT_MODEL = 'anthropic:claude-sonnet-4-6'`; simplify `create_marcel_agent` to pass `model` straight to `Agent()`
- [ ] Add self-healing migration in [src/marcel_core/storage/settings.py](../../../src/marcel_core/storage/settings.py) `_load_settings()` — prepend `anthropic:` to any unqualified stored values and write back
- [ ] Update `set_model` validation in [src/marcel_core/tools/marcel/settings.py](../../../src/marcel_core/tools/marcel/settings.py) to shape-only (`:` with non-empty halves)
- [ ] Update `set_model` validation in [src/marcel_core/skills/integrations/settings.py](../../../src/marcel_core/skills/integrations/settings.py) to match
- [ ] Fix [src/marcel_core/memory/selector.py](../../../src/marcel_core/memory/selector.py#L42): `_SELECTOR_MODEL = 'anthropic:claude-haiku-4-5-20251001'`
- [ ] Fix [src/marcel_core/memory/extract.py](../../../src/marcel_core/memory/extract.py#L25): `_EXTRACTOR_MODEL = 'anthropic:claude-haiku-4-5-20251001'`
- [ ] Fix [src/marcel_core/memory/summarizer.py](../../../src/marcel_core/memory/summarizer.py#L39): `SUMMARIZATION_MODEL = 'anthropic:claude-haiku-4-5-20251001'`
- [ ] Update any tests referencing unqualified short model names to use qualified form
- [ ] Run `make check` — format, lint, typecheck, tests all green
- [ ] Manual smoke test: redeploy via `request_restart()`, verify default model turn succeeds, set an off-registry qualified model and verify it works without code changes, verify old unqualified `settings.json` self-heals

## Relationships
_(none)_

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
