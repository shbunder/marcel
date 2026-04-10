# ISSUE-046: Track Tool Calls in Conversation History

**Status:** Open
**Created:** 2026-04-10
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, architecture

## Capture
**Original request:** "I don't see any tool_calls in the history.jsonl, is that correctly stored? (last telegram message used the browser). For question 3, take inspiration from ~/repos/clawcode if possible how to include longer history with toolcalls in the context (what to keep and what to ignore)"

**Follow-up Q&A:** None

**Resolved intent:** The v2 harness (`harness/runner.py`) currently only captures text deltas from `agent.run_stream()` — tool calls and their results are silently discarded. This means history lacks context about what tools were used and what they returned, breaking continuity across turns. The fix requires: (1) capturing tool calls/results during streaming, (2) storing them in the JSONL history, and (3) intelligently loading them back into context with size management inspired by clawcode's approach.

## Description

### Current gap

`runner.py` uses `result.stream_text(delta=True)` which only yields text tokens. The assistant message saved to history contains only the final text — no record of tool calls or results. The code explicitly acknowledges this:

> "ToolCallStarted/Completed are reserved for future implementation via agent.iter()." — `runner.py:128`
> "tool and system messages are skipped since we don't track full tool call/response round-trips in the JSONL history yet." — `runner.py:40-42`

### What to capture

After a turn completes, extract the full message list from the pydantic-ai result (via `result.all_messages()`) and persist tool use + tool result entries alongside the assistant text.

### Context loading strategy (inspired by clawcode)

When loading history back into context, not all tool call data is equally valuable. clawcode uses a multi-tier approach:

1. **Tool inputs** — always kept in full (small, show intent)
2. **Tool results** — tiered by size and age:
   - **Recent turns** (last 3-5): keep tool results in full (up to a threshold)
   - **Older turns**: replace large tool results with a short preview/summary
   - **Very old turns**: drop tool results entirely, keep only tool name + "called with X"
3. **Persistence threshold** — tool results above a size threshold (e.g., 50KB) are offloaded to the paste store and replaced with a preview (first ~2000 chars)
4. **Non-compactable tools** — certain tools (like user questions, task management) are always kept in full

### Tools to always keep results for
- `memory_search` — the retrieved context matters for understanding decisions
- `notify` — shows what the user was told

### Tools where results can be trimmed
- `bash` — output can be huge (ls, docker, logs)
- `read_file` — file contents are re-readable
- `integration` — API responses can be large (e.g., full web page scrape)

### History → ModelMessage conversion

The current `history_to_messages()` only converts user/assistant text. It needs to produce full pydantic-ai `ModelRequest`/`ModelResponse` objects that include `ToolCallPart`, `ToolReturnPart`, and `RetryPromptPart` so the agent sees the full conversation structure.

## Tasks

- [ ] Extract full message list from pydantic-ai result after turn completion (`result.all_messages()`)
- [ ] Store tool call entries (role=`assistant` with `tool_calls` field) and tool result entries (role=`tool` with `tool_call_id` + content) in JSONL history
- [ ] Implement result size management: offload large results (>50KB) to paste store with preview
- [ ] Update `history_to_messages()` to reconstruct full pydantic-ai message objects including `ToolCallPart` and `ToolReturnPart`
- [ ] Implement tiered result loading: full results for recent turns, previews for older turns, names-only for very old turns
- [ ] Define compactable vs non-compactable tool list (bash/read_file/integration = compactable; memory_search/notify = keep)
- [ ] Update compactor to handle tool call messages during summarization
- [ ] Yield `ToolCallStarted` / `ToolCallCompleted` events from `stream_turn()` (switch to `agent.iter()` or post-hoc extraction)
- [ ] Add tests: tool call roundtrip (store → load → convert), large result offloading, tiered loading
- [ ] Verify end-to-end: send a Telegram message that triggers a tool, confirm tool call appears in history and is loaded in next turn's context

## Subtasks

## Relationships
- Depends on: [[ISSUE-044-telegram-session-history]]
- Related to: [[ISSUE-045-per-session-history]]

## Comments
### 2026-04-10 - Design notes
clawcode reference patterns (from `~/repos/clawcode`):
- `toolResultStorage.ts`: persistence threshold of 50KB, 2000-byte preview, SHA-based paste storage
- `compact/microCompact.ts`: in-place clearing of old tool results without full summarization
- `compact/compact.ts`: full compaction strips images, uses forked agent for summary, restores top 5 files/skills post-compact
- Key principle: once a tool result is replaced with a preview, the preview is frozen (never changes) for cache prefix stability

## Implementation Log
