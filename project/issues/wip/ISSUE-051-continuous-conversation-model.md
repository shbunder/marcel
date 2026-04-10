# ISSUE-051: Continuous Conversation Model

**Status:** WIP
**Created:** 2026-04-10
**Assignee:** Claude
**Priority:** High
**Labels:** feature, architecture

## Capture
**Original request:**
> I want to completely redo session management for Marcel. After thinking about it Marcel should be a butler, like a real human, that you chat to on (multiple) channels. But each conversation with humans is typically just one very long conversation. Also with humans, we forget things from earlier in the conversation, but we can look them up an remember them.
> I want the same kind of behaviour for Marcel, one long conversation per channel.
> 1) when having an active conversation Marcel should remember as much as possible of the current active part of the conversation. At least the conversational events.
> 2) tools calls should at most be remembered the current turn and possible the previous turn
> 3) if the conversation is discontinued for more then an hour, a clever summarization routine should run to summarize that part of the conversation as briefly as possible
> 4) Marcel should have a skill to manage its memory; search for old conversations, search in facts, compact
> 5) the user should be able to say "/forget" this resets the conversation as if Marcel was inactive for more than an hour
> 6) probably we should be clever in how the history is stored, one big-file will become too much to handle

**Follow-up Q&A:**
- Q: Segment size cap? A: 500KB is fine, can be bigger than 100KB
- Q: How will Marcel search/access older summaries not given from the start? A: Rolling summary chain provides gist; conversation_search tool provides recall. Summary directory is browsable.
- Q: Should there be a memory management skill? A: Yes — a SKILL.md that explains how the tools work together.

**Resolved intent:** Replace Marcel's session-based conversation model with a continuous, single-conversation-per-channel architecture inspired by how a real butler operates. Active conversation keeps full context; tool results are aggressively trimmed (current + previous turn only); idle periods (>1 hour) trigger automatic Haiku-powered summarization that compresses history into rolling summaries. A search index and conversation_search tool let Marcel "look up" specifics from the past. `/forget` triggers manual summarization. Storage is segmented to avoid unbounded file growth.

## Description

See full plan: `.claude/plans/piped-tinkering-clock.md`

Key changes:
- Segment-based storage replacing per-session JSONL files
- Rolling summary chain (each summary absorbs predecessor)
- Aggressive tool result lifecycle (2 turns, not 8)
- Idle summarization via Haiku (1-hour threshold)
- conversation_search + compact_now tools
- Memory management skill (SKILL.md)
- /forget command, /new repurposed
- Bash output cap reduced 50K→30K
- Migration script from old sessions to segments

## Tasks
- [✓] ISSUE-051-a: Create `memory/conversation.py` — segment storage
- [✓] ISSUE-051-b: Create `memory/summarizer.py` — idle summarization
- [✓] ISSUE-051-c: Add config settings
- [✓] ISSUE-051-d: Update `runner.py` — build_context() + tool lifecycle
- [✓] ISSUE-051-e: Update `tools/core.py` — bash output cap
- [✓] ISSUE-051-f: Add conversation_search and compact_now tools
- [✓] ISSUE-051-g: Update telegram webhook — /forget, /new
- [✓] ISSUE-051-h: Simplify telegram sessions
- [✓] ISSUE-051-i: Register background summarization task
- [✓] ISSUE-051-j: Create memory management skill
- [✓] ISSUE-051-k: Migration script
- [✓] ISSUE-051-l: Tests + make check

## Relationships
- Related to: [[ISSUE-044-telegram-session-history]]
- Related to: [[ISSUE-045-per-session-history-storage]]
- Supersedes: [[ISSUE-046-tool-call-history]] (tool lifecycle now part of this)

## Implementation Log

### 2026-04-10 — LLM Implementation
**Action**: Full implementation of continuous conversation model
**Files Created**:
- `src/marcel_core/memory/conversation.py` — Segment-based storage (ChannelMeta, SegmentSummary, SearchEntry, append/read/seal/strip/search operations)
- `src/marcel_core/memory/summarizer.py` — Idle summarization via Haiku (rolling summary chain, circuit breaker)
- `.marcel/skills/memory/SKILL.md` — Memory management skill teaching Marcel how to use memory tools
- `scripts/migrate_to_continuous.py` — Migration script from session-based to segment-based storage
- `tests/memory/test_conversation.py` — 21 tests for conversation module
**Files Modified**:
- `src/marcel_core/config.py` — Added marcel_idle_summarize_minutes (60) and marcel_bash_max_output (30000)
- `src/marcel_core/harness/runner.py` — Replaced read_recent_turns with build_context(); aggressive tool lifecycle (2 turns vs 8); messages now written to segments
- `src/marcel_core/tools/core.py` — Reduced MAX_OUTPUT_LENGTH from 50K to 30K; added paste-store offload at 15K
- `src/marcel_core/tools/integration.py` — Added conversation_search and compact_now tools
- `src/marcel_core/harness/agent.py` — Registered conversation_search and compact_now tools
- `src/marcel_core/channels/telegram/webhook.py` — Added /forget command, repurposed /new as alias; removed session creation; uses stable conversation_id
- `src/marcel_core/channels/telegram/sessions.py` — Simplified: removed conversation_id tracking, auto-new, clear_all_sessions; kept only touch_last_message
- `src/marcel_core/main.py` — Removed clear_all_sessions on startup; added background summarization loop (15-min interval)
- `src/marcel_core/api/chat.py` — Updated to use ensure_channel instead of create_session
- `src/marcel_core/api/chat_v2.py` — Removed check_and_compact; updated to use ensure_channel
- `tests/core/test_telegram.py` — Rewrote for simplified session model
- `tests/core/test_telegram_webhook.py` — Updated for continuous conversation model
- `tests/core/test_chat_v2.py` — Removed check_and_compact mocks
- `tests/harness/test_runner.py` — Updated for segment-based storage and aggressive tool lifecycle
**Commands Run**: `make check`
**Result**: 679 tests pass, all linting/typecheck clean
