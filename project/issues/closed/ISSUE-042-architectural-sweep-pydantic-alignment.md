# ISSUE-042: Architectural Sweep — Pydantic Alignment & Responsibility Cleanup

**Status:** Closed
**Created:** 2026-04-10
**Assignee:** Shaun
**Priority:** Medium
**Labels:** refactor, architecture

## Capture
**Original request:** "can you please do one last sweep of the code base to ensure everything is architecturally sound and in line with the philosophy of this project and pydantic. Check if each component has its correct responsibility and is implemented consistently."

**Follow-up Q&A:** User confirmed: "fix 1-9, 10 we will leave for now" (item 10 = dual v1/v2 tool systems, expected during migration).

**Resolved intent:** Comprehensive architectural review of the Marcel codebase to ensure consistency with project philosophy (lightweight, generic, human-readable, recoverable) and Pydantic best practices. Fix all identified issues except the expected v1/v2 tool duplication which is a transitional state during the pydantic-ai migration.

## Description

A full sweep of the codebase identified 10 architectural issues. Nine were fixed:

1. **Deduplicate memory selectors** — `agent/memory_select.py` was a full copy of `memory/selector.py` using claude_agent_sdk. Replaced with thin re-export wrapper.
2. **Extract channel format hints** — Triple-defined format hints consolidated into `CHANNEL_FORMAT_HINTS` in `harness/context.py`.
3. **Wire up `build_instructions_async`** — Was dead code; now called in v2 runner to activate AI-selected memories.
4. **Rename `_create_anthropic_model`** — Misleading name; renamed to `_resolve_model_string` (handles Anthropic, OpenAI, Bedrock).
5. **Make `_conv_dir` public** — Used across modules; renamed to `conv_dir` and exported.
6. **Cache skills registry** — `_load()` re-read `skills.json` on every call; added module-level cache + `reload()`.
7. **Pydantic models for storage** — `UserSettings`, `UserMeta`, and `SessionState` converted from raw dicts/TypedDicts to Pydantic BaseModels.
8. **Convert `MarcelDeps` to Pydantic dataclass** — Was a stdlib dataclass; now uses `pydantic.dataclasses` for validation consistency.
9. **Align runner events with channel adapter protocol** — Extracted `dispatch_event()` to replace manual isinstance chains in `chat_v2.py`.

Item 10 (dual v1/v2 tool systems) left as-is — expected transitional state per [[ISSUE-031-migrate-to-pydantic-ai-harness]].

## Tasks
- [✓] Audit codebase for architectural issues
- [✓] Fix #1: Deduplicate memory selectors
- [✓] Fix #2: Extract channel format hints
- [✓] Fix #3: Wire up build_instructions_async
- [✓] Fix #4: Rename _create_anthropic_model
- [✓] Fix #5: Make _conv_dir public
- [✓] Fix #6: Cache skills registry
- [✓] Fix #7: Pydantic models for storage
- [✓] Fix #8: Convert MarcelDeps to Pydantic dataclass
- [✓] Fix #9: Align runner events with channel adapter
- [✓] Update all affected tests
- [✓] make check passes (675 tests, 95% coverage)

## Relationships
- Related to: [[ISSUE-031-migrate-to-pydantic-ai-harness]]
- Related to: [[ISSUE-038-pydantic-settings-config]]

## Implementation Log
### 2026-04-10 - LLM Implementation
**Action**: Full architectural sweep and fixes for items 1-9
**Files Modified**:
- `src/marcel_core/agent/memory_select.py` — replaced with re-export wrapper
- `src/marcel_core/agent/context.py` — import CHANNEL_FORMAT_HINTS from harness
- `src/marcel_core/harness/context.py` — added CHANNEL_FORMAT_HINTS, MarcelDeps → pydantic dataclass
- `src/marcel_core/harness/runner.py` — wired up build_instructions_async
- `src/marcel_core/harness/agent.py` — renamed _create_anthropic_model → _resolve_model_string
- `src/marcel_core/storage/conversations.py` — _conv_dir → conv_dir
- `src/marcel_core/storage/__init__.py` — export conv_dir
- `src/marcel_core/api/conversations.py` — updated import, fixed name shadowing
- `src/marcel_core/skills/registry.py` — added cache + reload()
- `src/marcel_core/storage/settings.py` — UserSettings BaseModel
- `src/marcel_core/storage/users.py` — UserMeta BaseModel, UserRole Literal
- `src/marcel_core/channels/telegram/sessions.py` — SessionState → BaseModel
- `src/marcel_core/channels/adapter.py` — added dispatch_event()
- `src/marcel_core/api/chat_v2.py` — use dispatch_event()
- `tests/harness/test_agent.py` — updated for renames
- `tests/core/test_agent_memory_select.py` — target canonical impl
- `tests/core/test_agent.py` — updated import
- `tests/channels/test_websocket_adapter.py` — added protocol methods to MinimalAdapter
- `tests/core/test_chat_v2.py` — updated event ordering expectations
- `tests/core/test_skills.py` — added registry.reload() in test setup
- `tests/core/test_storage.py` — updated validation error match pattern
**Commands Run**: `make check`
**Result**: Success — 675 passed, 0 errors, 95% coverage
