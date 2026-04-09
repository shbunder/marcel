# ISSUE-031: Migrate to Pydantic-AI Harness

**Status:** Open
**Created:** 2026-04-09
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, architecture, breaking-change

## Capture

**Original request:** "Perform the task to create a new harness. Be very conservative and ask critical questions before going into implementation mode, this is a huge change for Marcel"

**Follow-up Q&A:**

Q: Should Marcel keep the MCP (Model Context Protocol) tool interface, or switch to a different tool system?
A: Pydantic AI tools

Q: What should happen to existing user data and memory files during the migration?
A: Migrate to new format

Q: How should the migration be executed?
A: Incremental (Recommended) — Build new harness alongside current system, migrate features one-by-one, run both in parallel during transition.

Q: What's the timeline expectation for this rewrite?
A: Multi-week project — Thorough design, comprehensive testing, gradual rollout.

Q: For the skill/integration system, how should the @integration decorator pattern evolve?
A: Use pydantic-ai tools as much as possible, but create a marcel specific system that reads skills and uses the (pydantic-ai) tool "integrations", where the decorator pattern + registry ensures that the new integration is now working in this one tool. This allows to minimize an overflow of tools to Marcel.

Q: Should Marcel keep the claude-code skill (ability to delegate to Claude Code CLI for self-modification)?
A: Give Marcel direct bash/git/read/write/edit tools like Claude Code, this is useful to do server management tasks. However for real code writing tasks it should use claude-code cli (almost as a subagent it can call and put to work).

Q: What AI models should the new harness support at launch?
A: Anthropic Claude, OpenAI GPT

Q: For channels (Telegram, CLI, iOS), should the harness handle channel-specific formatting, or should channels be thin clients?
A: Thin clients (Recommended) — Channels are dumb pipes that send/receive messages. Harness knows channel capabilities and formats responses accordingly.

**Resolved intent:** 

Replace Marcel's claude-agent-sdk harness with a custom pydantic-ai-based harness to gain multi-provider AI support, explicit context management, and clean tool architecture. The migration will be incremental over 6 weeks, running old and new systems in parallel. The new harness uses JSONL conversation history with clawcode-inspired memory intelligence, pydantic-ai native tools (bash, git, file ops, integration dispatcher, claude-code delegation), and openclaw-inspired thin channel adapters. Existing user data (conversations, memories, profiles) will be migrated to new formats with zero data loss.

## Description

Marcel currently uses `claude-agent-sdk` which provides persistent ClaudeSDKClient sessions with SDK-managed context compaction and MCP tools. This creates vendor lock-in, context opacity (SDK manages compaction internally), limited multi-provider support, and tool system complexity (MCP indirection).

The new pydantic-ai harness will provide:
- **Multi-provider support**: Anthropic Claude and OpenAI GPT (with future extensibility)
- **Explicit context management**: JSONL conversation history, external paste store for large content, AI-driven memory selection, observable auto-compaction
- **Clean tool system**: Direct pydantic-ai tools (no MCP layer), single integration dispatcher tool preserving @register pattern
- **Channel abstraction**: Protocol-based adapters with capability declarations
- **Self-modification capabilities**: Direct bash/git/file tools + claude-code CLI delegation

The migration follows a 6-week phased approach with both systems running in parallel during transition.

## Tasks

### Phase 1: Foundation (Week 1-2)
- [ ] Add pydantic-ai dependency to `pyproject.toml`
- [ ] Implement JSONL history module (`src/marcel_core/memory/history.py`)
- [ ] Implement external paste store (`src/marcel_core/memory/pastes.py`)
- [ ] Create MarcelAgent wrapper (`src/marcel_core/harness/agent.py`)
- [ ] Create stream_turn runner (`src/marcel_core/harness/runner.py`)
- [ ] Build MarcelDeps context (`src/marcel_core/harness/context.py`)
- [ ] Implement core tools module (`src/marcel_core/tools/core.py`): bash, read_file, write_file, edit_file
- [ ] Implement git tools: git_status, git_diff, git_commit, git_push
- [ ] Write unit tests for Phase 1 components
- [ ] Milestone: New harness can handle simple tasks (no integrations yet)

### Phase 2: Memory & Tools (Week 3)
- [ ] Port memory selector to new system (`src/marcel_core/memory/selector.py`)
- [ ] Implement auto-compaction logic (`src/marcel_core/memory/compactor.py`)
- [ ] Build integration dispatcher tool (`src/marcel_core/tools/integration.py`)
- [ ] Create claude-code delegation tool (`src/marcel_core/tools/claude_code.py`)
- [ ] Add memory_search tool (port from current MCP tool)
- [ ] Add notify tool for progress updates
- [ ] Write unit tests for Phase 2 components
- [ ] Milestone: New harness feature-complete for tools

### Phase 3: Channels (Week 4)
- [ ] Implement channel adapter protocol (`src/marcel_core/channels/adapter.py`)
- [ ] Build WebSocket adapter (`src/marcel_core/channels/websocket.py`)
- [ ] Build Telegram adapter (`src/marcel_core/channels/telegram.py`)
- [ ] Build CLI adapter (`src/marcel_core/channels/cli.py`)
- [ ] Create v2 API endpoint (`src/marcel_core/api/chat_v2.py`)
- [ ] Update `main.py` to include v2 router with feature flag
- [ ] Write unit tests for channel adapters
- [ ] Milestone: Both harnesses running in parallel

### Phase 4: Testing & Migration (Week 5)
- [ ] Add feature flag env var `MARCEL_USE_V2` (default: false)
- [ ] Create conversation migration script (`scripts/migrate_conversations.py`)
- [ ] Run migration script in dry-run mode and validate
- [ ] Execute production migration (Markdown → JSONL)
- [ ] Run integration tests (test_turn_flow, test_compaction, test_migration)
- [ ] Manual testing checklist (WebSocket, Telegram, integrations, compaction, claude-code tool)
- [ ] Performance comparison (token usage, latency, quality vs v1)
- [ ] Milestone: V2 validated, ready for default

### Phase 5: Cutover (Week 6)
- [ ] Flip feature flag: `MARCEL_USE_V2=true` becomes default
- [ ] Deprecate old endpoints (keep for 1 week grace period)
- [ ] Remove claude-agent-sdk from `pyproject.toml`
- [ ] Delete old harness code (`agent/sessions.py`, `agent/runner.py`, `agent/context.py`, `skills/tool.py`)
- [ ] Update `CLAUDE.md` to reflect new architecture
- [ ] Update `docs/architecture.md` with new harness documentation
- [ ] Create migration announcement for users
- [ ] Milestone: Migration complete

## Subtasks

- [ ] ISSUE-031-a: Phase 1 - Foundation (JSONL history, core tools, MarcelAgent)
- [ ] ISSUE-031-b: Phase 2 - Memory & Tools (selector, compaction, integration dispatcher)
- [ ] ISSUE-031-c: Phase 3 - Channels (adapters, v2 endpoints)
- [ ] ISSUE-031-d: Phase 4 - Testing & Data Migration
- [ ] ISSUE-031-e: Phase 5 - Cutover & Cleanup

## Relationships

None (foundational architecture change)

## Comments

### 2026-04-09 - Claude
This is the largest architectural change in Marcel's history. The migration plan includes:
- Full exploration of pydantic-ai, clawcode (memory patterns), and openclaw (channel patterns)
- Incremental migration with both systems running in parallel
- Comprehensive testing before cutover
- Zero data loss via migration scripts with dry-run validation
- Performance benchmarking to ensure quality maintained

Detailed architecture plan saved at: `/home/sagemaker-user/.claude/plans/polished-humming-pixel.md`

## Implementation Log

<!-- Append entries here when performing development work on this issue -->
