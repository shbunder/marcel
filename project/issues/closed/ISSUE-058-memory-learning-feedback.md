# ISSUE-058: Improve memory system and learning from feedback

**Status:** Closed
**Created:** 2026-04-11
**Assignee:** Claude
**Priority:** Medium
**Labels:** feature, memory

## Capture
**Original request:** "let's investigate how Marcel can learn from my input... let's look how we can improve and streamline this, maybe again get inspired from ~/repos/openclaw and ~/repos/clawcode... I noticed some part of the system prompt suddenly saying 'this information is more than 15 days old and might be stale', but without clear context what fragment this was talking about"

**Follow-up Q&A:**
- Researched clawcode's autoDream consolidation system (3 gates, 4-phase prompt) and feedback memory type (rule + Why + How to apply)
- Researched openclaw's feedback reflection system (thumbs-down → background reflection → JSON learnings)
- Traced the stale warning issue to `selector.py` appending freshness notes to raw memory content with no per-memory header

**Resolved intent:** Marcel's memory system needs five improvements to better learn from user feedback: (1) label each memory block in the system prompt with name/type/age so stale warnings aren't confusing, (2) add a "feedback" memory type for behavioral guidance, (3) add a `save_memory` tool action for deliberate persistence, (4) inject user preferences into job agents so they adapt without hardcoding, (5) add periodic memory consolidation to prune and rebuild the index.

## Description

Marcel currently learns from user input via a background Haiku extractor that fires after each conversation turn. This works for capturing facts, but falls short for behavioral feedback, and the memory presentation in the system prompt is confusing (unlabeled blocks with dangling stale warnings).

Inspired by clawcode's memory type taxonomy (user/feedback/project/reference) and autoDream consolidation, and openclaw's structured feedback reflection system.

## Tasks
- [✓] ISSUE-058-a: Label memory blocks — add `###` headers with name, type, age to each memory in the system prompt (`selector.py`, `context.py`)
- [✓] ISSUE-058-b: Add `FEEDBACK` memory type to `MemoryType` enum, update extractor prompt with feedback structure (rule + Why + How to apply) (`storage/memory.py`, `agent/memory_extract.py`)
- [✓] ISSUE-058-c: Add `save_memory` action to `marcel()` tool for deliberate persistence (`tools/marcel.py`)
- [✓] ISSUE-058-d: Inject preference + feedback memories into job agent system prompts (`jobs/executor.py`)
- [✓] ISSUE-058-e: Add periodic memory consolidation — prune expired, rebuild index (`jobs/scheduler.py`, `storage/memory.py`)
- [✓] Run `make check` — all passes (690 passed)

## Subtasks
- [✓] ISSUE-058-a: Label memory blocks in system prompt
- [✓] ISSUE-058-b: Add feedback memory type
- [✓] ISSUE-058-c: Add save_memory tool action
- [✓] ISSUE-058-d: Inject preferences into job agents
- [✓] ISSUE-058-e: Periodic memory consolidation

## Relationships
- Related to: [[ISSUE-056-rss-browser-tools-news-scraper]] (job preferences came up during news digest work)
- Implements: clawcode autoDream patterns (adapted for Marcel's simpler architecture)

## Comments
### 2026-04-11 — Research
Explored clawcode and openclaw repos for inspiration. Key patterns adopted:
- clawcode's 4-type memory taxonomy with "feedback" type (rule + Why + How to apply)
- clawcode's memory block labeling with `[type] name (age)` headers
- clawcode's consolidation approach (simplified: prune + rebuild index, no LLM merge phase initially)
- openclaw's structured feedback extraction concept

### 2026-04-11 — Stale warning diagnosis
The confusing "X days old" warning comes from `memory_freshness_note()` in `storage/memory.py`, appended to memory content in `selector.py:92-93`. The `## Memory` section in `context.py:168-169` joins all memories with `---` under one heading with no per-memory labels. Fix: move freshness into a `###` header per memory block.

## Implementation Log

### 2026-04-11 — Implementation
**Action**: Implemented all 5 subtasks
**Files Modified**:
- `src/marcel_core/memory/selector.py` — Added `_format_memory_label()` helper, prepend `### [type] name (age)` header to each memory block
- `src/marcel_core/storage/memory.py` — Added `FEEDBACK` to `MemoryType` enum, renamed `_human_age` → `human_age` (public), added `rebuild_memory_index()` function
- `src/marcel_core/storage/__init__.py` — Exported `human_age` and `rebuild_memory_index`
- `src/marcel_core/agent/memory_extract.py` — Updated extractor prompt with feedback type guidance and structured example (rule + Why + How to apply)
- `src/marcel_core/tools/marcel.py` — Added `save_memory` action for deliberate memory persistence
- `src/marcel_core/jobs/executor.py` — Added `_load_job_memories()` to inject preference + feedback memories into job agent system prompts
- `src/marcel_core/jobs/scheduler.py` — Added `_consolidate_memories()` to daily cleanup loop (prune expired + rebuild index)
- `tests/memory/test_selector.py` — Updated test assertion to account for new memory labels
**Commands Run**: `make check`
**Result**: Success — 690 tests passed
**Reflection**:
- Coverage: 5/5 requirements addressed
- Shortcuts found: none
- Scope drift: none — docs updated for new API and feedback type
