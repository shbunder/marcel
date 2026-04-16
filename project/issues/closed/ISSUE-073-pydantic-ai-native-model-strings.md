# ISSUE-073: Simplify Model Routing via Pydantic-AI Native `provider:model` Strings

**Status:** Closed
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
- [✓] Update [src/marcel_core/harness/agent.py](../../../src/marcel_core/harness/agent.py): delete `_resolve_model_string`, `_BEDROCK_MODEL_MAP`; collapse `ANTHROPIC_MODELS`/`OPENAI_MODELS` into `KNOWN_MODELS` with qualified keys; set `DEFAULT_MODEL = 'anthropic:claude-sonnet-4-6'`; simplify `create_marcel_agent` to pass `model` straight to `Agent()`
- [✓] Add self-healing migration in [src/marcel_core/storage/settings.py](../../../src/marcel_core/storage/settings.py) `_load_settings()` — prepend `anthropic:` to any unqualified stored values and write back
- [✓] Update `set_model` validation in [src/marcel_core/tools/marcel/settings.py](../../../src/marcel_core/tools/marcel/settings.py) to shape-only (`:` with non-empty halves)
- [✓] Update `set_model` validation in [src/marcel_core/skills/integrations/settings.py](../../../src/marcel_core/skills/integrations/settings.py) to match
- [✓] Fix [src/marcel_core/memory/selector.py](../../../src/marcel_core/memory/selector.py#L42): `_SELECTOR_MODEL = 'anthropic:claude-haiku-4-5-20251001'`
- [✓] Fix [src/marcel_core/memory/extract.py](../../../src/marcel_core/memory/extract.py#L25): `_EXTRACTOR_MODEL = 'anthropic:claude-haiku-4-5-20251001'`
- [✓] Fix [src/marcel_core/memory/summarizer.py](../../../src/marcel_core/memory/summarizer.py#L39): `SUMMARIZATION_MODEL = 'anthropic:claude-haiku-4-5-20251001'`
- [✓] Update any tests referencing unqualified short model names to use qualified form
- [✓] Run `make check` — format, lint, typecheck, tests all green (1226 passed)
- [ ] Manual smoke test: redeploy via `request_restart()`, verify default model turn succeeds, set an off-registry qualified model and verify it works without code changes, verify old unqualified `settings.json` self-heals — **deferred to user**: requires live deploy with real credentials; cannot be executed from the planning environment

## Relationships
_(none)_

## Implementation Log

### 2026-04-13 14:10 - LLM Implementation

**Action:** Removed Marcel's custom model routing layer and migrated all call sites to pydantic-ai native `provider:model` strings.

**Files Modified:**
- `src/marcel_core/harness/agent.py` — Deleted `_resolve_model_string`, `_BEDROCK_MODEL_MAP`, `ANTHROPIC_MODELS`, `OPENAI_MODELS`. Added unified `KNOWN_MODELS` dict (display labels only, not a whitelist). `DEFAULT_MODEL` is now `'anthropic:claude-sonnet-4-6'`. `create_marcel_agent` passes the model string straight to `Agent()`.
- `src/marcel_core/storage/settings.py` — Added self-healing migration in `_load_settings`: stored values without a `:` get `anthropic:` prepended and the file is rewritten. Docstring on `save_channel_model` updated.
- `src/marcel_core/tools/marcel/settings.py` — `set_model` now validates shape only (must be `channel:provider:model` with first `:` separating channel from model). Any qualified `provider:model` accepted. Off-registry models show `(off-registry)` as the display label.
- `src/marcel_core/skills/integrations/settings.py` — Same shape-only validation for the integration-handler variant.
- `src/marcel_core/tools/marcel/dispatcher.py` — Help text updated to reflect `channel:provider:model` format.
- `src/marcel_core/harness/context.py` — Docstring example on `MarcelDeps.model` updated.
- `src/marcel_core/memory/selector.py`, `extract.py`, `summarizer.py` — Qualified all three bare `claude-haiku-4-5-20251001` strings to `anthropic:claude-haiku-4-5-20251001` (these were previously malformed pydantic-ai model strings).
- `src/marcel_core/jobs/tool.py`, `models.py`, `scheduler.py`, `templates.py` — Qualified all job defaults (`claude-haiku-4-5-20251001` → `anthropic:claude-haiku-4-5-20251001`, `claude-sonnet-4-6` → `anthropic:claude-sonnet-4-6`).
- `src/marcel_cli/src/config.rs` — Rust CLI `default_model()` updated to `"anthropic:claude-sonnet-4-6"`.
- `src/marcel_core/defaults/skills/settings/SKILL.md`, `defaults/skills/jobs/SKILL.md` — Examples rewritten to use qualified strings; added note that any pydantic-ai-supported model works.
- `tests/harness/test_agent.py` — Rewritten: removed `_resolve_model_string` / `ANTHROPIC_MODELS` / `OPENAI_MODELS` tests; added `all_models()` is-a-copy test, `DEFAULT_MODEL` is-qualified test, `KNOWN_MODELS` are-all-qualified test, and a simple `create_marcel_agent` sanity test.
- `tests/core/test_settings.py` — Updated fixtures to qualified form; replaced "rejects unknown model" test with "rejects unqualified" and "accepts off-registry qualified" tests; added self-healing migration test.
- `tests/tools/test_integration_tools.py` — Updated `test_set_model_valid` to `telegram:anthropic:claude-sonnet-4-6`; replaced whitelist test with qualification-shape test; added off-registry acceptance test.
- `tests/harness/test_runner.py` — `gpt-4o` → `openai:gpt-4o`.
- `tests/jobs/test_tool_scenarios.py` — `claude-opus-4-6` → `anthropic:claude-opus-4-6`.
- `README.md`, `SETUP.md`, `docs/jobs.md`, `docs/cli.md` — Model references and examples qualified.
- `project/lessons-learned.md` — Retired the old registry-as-dispatch pattern; added ISSUE-073 retrospective.
- `project/issues/open/ISSUE-070-local-llm-fallback.md` — Task list rewritten to match the post-073 architecture (no more `_resolve_model_string` hook; `create_marcel_agent` intercept for `local:*` strings instead).

**Commands Run:** `make check` → 1226 passed, 0 failures, 92.89% coverage. `uv lock` → marcel v2.7.0 → v2.8.0.

**Result:** Code change complete, tests green. Manual smoke test deferred to user (requires live deploy with real API credentials).

**Reflection:**
- **Coverage:** 8/9 requirements fully addressed. The one `[ ]` item is the manual smoke test, which can only run against a live deployment — left for the user to exercise after `request_restart()`.
- **Shortcuts found:** None. No `# TODO` / `# FIXME` / `# HACK` introduced. No bare `except:`. No hardcoded magic values — defaults are exported constants.
- **Scope drift:** Slightly positive. Discovered and fixed three pre-existing silent bugs (memory agents passing bare `claude-haiku-4-5-20251001` strings to pydantic-ai `Agent()` — they were malformed and only worked by accident). Fix was in-scope per the original "memory agents become valid" line in the resolved intent, so this is intentional, not drift.
- **Interface change:** The user-facing `marcel(action="set_model", name=...)` format went from `"channel:model"` to `"channel:provider:model"`. This is a break in the human-facing command syntax, but the help text, SKILL.md examples, and error messages all guide users to the new form. Old `settings.json` files auto-heal on load, so no data migration is needed.

### 2026-04-13 14:25 - Closing

- Versions bumped: `marcel_core.__version__` 2.7.0 → 2.8.0, `pyproject.toml` 2.7.0 → 2.8.0, `src/marcel_cli/Cargo.toml` 0.2.0 → 0.3.0, `uv.lock` synced.
- Docs updated: `README.md` (supported-models table rewritten to qualified strings + "any pydantic-ai model works" note), `SETUP.md`, `docs/cli.md`, `docs/jobs.md`.
- Lessons captured in `project/lessons-learned.md` under the ISSUE-073 heading.

## Lessons Learned

### What worked well
- Deleting code beats maintaining it: `_resolve_model_string` + `_BEDROCK_MODEL_MAP` + dual `ANTHROPIC_MODELS` / `OPENAI_MODELS` registries (~60 loc) collapsed to one `KNOWN_MODELS` dict used only for display labels.
- **Self-healing settings migration** in `_load_settings`: detect unqualified legacy values (`no ':' in model`), prepend `anthropic:`, rewrite the file transparently. No migration script, no version flag, no cutover window.
- Shape-only validation (`':' in value`) turns "add a new model" from a code change into a zero-touch config change — any pydantic-ai-supported `provider:model` works immediately.

### What to do differently
- Memory agents (`selector.py`, `extract.py`, `summarizer.py`) were passing **unqualified** model names directly to `Agent()` for months — they only worked because pydantic-ai tolerated the legacy short form. If we'd had a test that instantiated them against a known-strict pydantic-ai version, we'd have caught this earlier. Lesson: mock-free integration-shape tests on model string validity are cheap and catch silent drift.

### Patterns to reuse
- **Trust the framework**: before writing an abstraction layer on top of a library, check whether the library already does what you need. Pydantic-ai's `provider:model` dispatch predated the routing layer we built; we just hadn't used it.
- **Shape validation > whitelist validation** when the whitelist is the thing preventing extensibility. Save the registry for UX, use shape-only checks at the enforcement boundary.
