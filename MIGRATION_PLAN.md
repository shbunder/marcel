# Marcel V2 Harness - Migration Plan (NUC Continuation)

**Status as of 2026-04-09**: Phase 1-3 complete on SageMaker. Ready for Phase 4 testing & migration on NUC.

## What's Complete ✅

### Phase 1: Foundation (Week 1-2) ✅
- JSONL conversation history (`src/marcel_core/memory/history.py`)
- External paste store for large content (`src/marcel_core/memory/pastes.py`)
- MarcelAgent wrapper around pydantic-ai (`src/marcel_core/harness/agent.py`)
- Stream turn runner (`src/marcel_core/harness/runner.py`)
- MarcelDeps context (`src/marcel_core/harness/context.py`)
- Core tools: bash, read_file, write_file, edit_file, git_* (`src/marcel_core/tools/core.py`)
- All unit tests passing (39 tests)

### Phase 2: Memory & Tools (Week 3) ✅
- AI-driven memory selector ported (`src/marcel_core/memory/selector.py`)
- Auto-compaction at 75k tokens (`src/marcel_core/memory/compactor.py`)
- Integration dispatcher tool (`src/marcel_core/tools/integration.py`)
- Claude Code delegation tool (`src/marcel_core/tools/claude_code.py`)
- Memory search & notify tools integrated

### Phase 3: Channels (Week 4) ✅
- Channel adapter protocol (`src/marcel_core/channels/adapter.py`)
- WebSocket adapter with tool streaming (`src/marcel_core/channels/websocket.py`)
- V2 API endpoint at `/v2/chat` (`src/marcel_core/api/chat_v2.py`)
- **Tool call streaming working** - FunctionToolCallEvent/ResultEvent properly queued and yielded
- Both v1 (`/ws`) and v2 (`/v2/chat`) running in parallel

### Key Technical Wins 🎉

**AWS Bedrock Integration:**
- Native pydantic-ai Bedrock support via boto3 (no proxy needed!)
- Format: `bedrock:{model_id}` (AWS_REGION from env)
- Model mapping: `claude-sonnet-4-6` → `eu.anthropic.claude-sonnet-4-5-20250929-v1:0`
- Set `AWS_REGION=eu-west-1` in `.env.local`

**Tool Streaming:**
- `agent.run_stream()` with `event_stream_handler` captures tool events
- Queue events and yield during text streaming for proper interleaving
- Extract from `event.part` (ToolCallPart) and `event.result` (ToolReturnPart)

**Channel Architecture:**
- Single WebSocket adapter handles ALL channels (thin client pattern)
- Telegram/CLI/Web all connect to `/v2/chat` with `channel` parameter
- No separate adapter implementations needed!

## Commits to Pull

```bash
# On NUC, pull these commits:
git pull origin main

# Key commits:
# 7182164 - Fix system_prompt parameter
# 8898c96 - Remove invalid result_type
# d0f8d18 - Fix git_commit syntax, add Bedrock support
# cbea3c6 - Phase 3: WebSocket adapter, v2 endpoint
# 0a68ab3 - Bedrock authentication fix
# c1649fc - Tool call streaming
# 6b88b23 - Phase 1-3 completion docs
```

## What's NOT Done ❌

### Phase 4: Testing & Migration (Week 5) - START HERE ON NUC

#### 4.1 Environment Setup on NUC
```bash
# 1. Pull latest code
cd ~/projects/marcel
git pull origin main

# 2. Set up AWS credentials for Bedrock
# Add to .env.local:
export AWS_REGION=eu-west-1
export ANTHROPIC_API_KEY=dummy-key-not-used-with-bedrock

# 3. Test v2 endpoint works
make serve
# In another terminal:
make test-v2 MSG="Hello Marcel!"
# Should see: clean response + tool calls if triggered

# 4. Verify integrations still work
make test-v2 MSG="Check my banking balance"
# Should see: 🔧 Tool: integration ✓
```

#### 4.2 Feature Flag Implementation
**File:** `src/marcel_core/main.py` or new `src/marcel_core/config.py`

```python
# Add to .env
MARCEL_USE_V2=false  # default to v1 for safety

# In main.py, route based on flag:
if os.getenv('MARCEL_USE_V2', 'false').lower() == 'true':
    # Use v2 endpoint
    app.include_router(chat_v2_router, prefix="")  # Make /v2/chat the default
else:
    # Use v1 endpoint
    app.include_router(chat_router, prefix="")
```

#### 4.3 Conversation Migration Script
**File:** `scripts/migrate_conversations.py`

**Purpose:** Migrate existing Markdown conversations to JSONL format.

**Algorithm:**
```python
# For each user in ~/.marcel/users/*/
# For each conversation/*.md file:
#   1. Parse Markdown: **User:** ... and **Marcel:** ...
#   2. Extract timestamps from filename or use file mtime
#   3. Generate JSONL entries:
#      {"role": "user", "text": "...", "timestamp": "...", "conversation_id": "..."}
#      {"role": "assistant", "text": "...", "timestamp": "...", "conversation_id": "..."}
#   4. Write to history.jsonl
#   5. Move original .md to conversations/archive/ (preserve for rollback)

# Dry-run mode: write to /tmp/marcel-migration/ for validation
# Production mode: write to ~/.marcel/users/{slug}/history.jsonl
```

**Run:**
```bash
# Dry run first
uv run python scripts/migrate_conversations.py --dry-run

# Check output
ls -lah /tmp/marcel-migration/*/history.jsonl

# If looks good, run for real
uv run python scripts/migrate_conversations.py --execute
```

#### 4.4 Integration Tests
**File:** `tests/integration/test_v2_turn_flow.py`

Test scenarios:
- Simple text-only turn
- Turn with tool calls (integration, bash)
- Multi-turn conversation with context
- Error handling (bad tool args, tool failure)
- Memory selection and compaction triggers

```bash
uv run pytest tests/integration/ -v
```

#### 4.5 Manual Testing Checklist

Create: `tests/manual/v2_testing_checklist.md`

```markdown
## V2 Harness Manual Testing

- [ ] WebSocket connection and streaming
- [ ] Text-only responses (no tools)
- [ ] Integration tool: banking.balance
- [ ] Integration tool: icloud.calendar
- [ ] Core tool: bash execution
- [ ] Core tool: git operations
- [ ] Memory search tool
- [ ] Notify tool (progress updates)
- [ ] Multi-turn conversation maintains context
- [ ] Auto-compaction at 75k tokens
- [ ] Error handling (bad input, tool failure)
- [ ] JSONL history persisted correctly
- [ ] Large tool results go to paste store
- [ ] Channel parameter works (telegram, cli, websocket)
```

#### 4.6 Performance Comparison
**File:** `scripts/compare_v1_v2_performance.py`

Metrics to track:
- Token usage (input, output, cache hits)
- Latency (time to first token, total duration)
- Response quality (subjective - compare outputs)

Run same queries through both v1 and v2, log results to CSV.

### Phase 5: Cutover (Week 6) - AFTER V2 VALIDATED

#### 5.1 Make V2 Default
```bash
# In .env or systemd unit file:
MARCEL_USE_V2=true
```

#### 5.2 Deprecation Period
- Keep v1 endpoint active for 1 week grace period
- Log warning when v1 endpoint is used
- Monitor for any issues

#### 5.3 Remove Old Code
After grace period, delete:
- `src/marcel_core/agent/sessions.py`
- `src/marcel_core/agent/runner.py`
- `src/marcel_core/agent/context.py`
- `src/marcel_core/skills/tool.py` (MCP tool builder)
- `pyproject.toml`: remove `claude-agent-sdk` dependency

#### 5.4 Update Documentation
- `CLAUDE.md`: Update architecture description
- `docs/architecture.md`: Document new harness design
- `docs/self-modification.md`: Update agent initialization examples
- Create migration announcement for users (if any external users exist)

## Testing Strategy on NUC

### Day 1: Validation
1. Pull code, set up environment
2. Run `make test-v2` - verify basic functionality
3. Test each integration manually
4. Check JSONL history files are being created

### Day 2: Migration Script
1. Write `scripts/migrate_conversations.py`
2. Test dry-run mode on a few conversations
3. Validate JSONL output matches expected format
4. Run full migration in test environment

### Day 3: Integration Tests
1. Write comprehensive integration tests
2. Test error scenarios
3. Test memory selection and compaction
4. Verify tool call tracking in history

### Day 4-5: Production Testing
1. Enable v2 for your own user only (feature flag per-user?)
2. Use Marcel normally for 2 days via Telegram
3. Compare quality, speed, reliability vs v1
4. Check logs for any errors or warnings

### Day 6: Performance Analysis
1. Run comparison script
2. Analyze token usage patterns
3. Check if prompt caching is effective
4. Measure latency differences

### Day 7: Decision Point
- If v2 is equal or better → proceed to Phase 5 cutover
- If v2 has issues → document and fix before cutover
- If v2 is worse → investigate root cause, may need architecture tweaks

## Critical Files Reference

### V2 Harness Core
- `src/marcel_core/harness/agent.py` - Agent creation with Bedrock
- `src/marcel_core/harness/runner.py` - Turn execution with tool streaming
- `src/marcel_core/harness/context.py` - MarcelDeps, system prompt builder

### Memory Layer
- `src/marcel_core/memory/history.py` - JSONL append/read/compact
- `src/marcel_core/memory/pastes.py` - Content-addressed paste store
- `src/marcel_core/memory/selector.py` - AI memory selection (Haiku)
- `src/marcel_core/memory/compactor.py` - Auto-compaction at 75k tokens

### Tools
- `src/marcel_core/tools/core.py` - bash, git, files
- `src/marcel_core/tools/integration.py` - Integration dispatcher + notify
- `src/marcel_core/tools/claude_code.py` - Claude Code delegation

### API & Channels
- `src/marcel_core/api/chat_v2.py` - WebSocket endpoint `/v2/chat`
- `src/marcel_core/channels/websocket.py` - WebSocket adapter
- `src/marcel_core/channels/adapter.py` - ChannelAdapter protocol

### Configuration
- `.env.local` - AWS_REGION=eu-west-1 (add this on NUC!)
- `pyproject.toml` - pydantic-ai dependencies already added

## Known Issues / Notes

1. **Cargo not installed on SageMaker** - Pre-commit hook fails on Rust fmt. On NUC (with cargo), this won't be an issue.

2. **Bedrock model IDs** - Current mapping in `agent.py`:
   ```python
   'claude-sonnet-4-6': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0'
   'claude-opus-4-6': 'eu.anthropic.claude-opus-4-6-v1'
   'claude-haiku-4-5-20251001': 'eu.anthropic.claude-haiku-4-5-20251001-v1:0'
   ```
   Verify these are correct for your AWS region.

3. **Test script** - `test_v2.sh` is very handy for quick testing. Use it!

4. **Dual-write** - V2 currently writes to BOTH JSONL and Markdown (in `chat_v2.py`). This is for migration compatibility. After cutover, remove Markdown write.

5. **Tool call tracking** - JSONL history tracks tool calls with `{id, name, arguments}`. This is more structured than v1 Markdown logs.

6. **Memory files** - No changes needed! V2 uses the same memory file format as v1.

## Success Criteria

V2 migration is successful when:

1. ✅ Feature parity - All v1 capabilities work in v2
2. ✅ Quality maintained - Response quality equal or better
3. ✅ Performance acceptable - Latency within 20%, token usage within 30%
4. ✅ Multi-provider works - Can switch Claude models via Bedrock
5. ✅ Data preserved - All conversations migrated, zero data loss
6. ✅ Tests pass - All unit and integration tests green
7. ✅ Production stable - 1 week of production use without major issues

## Questions to Answer on NUC

1. Does Bedrock authentication work correctly on NUC? (AWS credentials)
2. Are there any performance differences between SageMaker and NUC?
3. Do existing Telegram integrations still work with v2?
4. Is the JSONL history format efficient for large conversation histories?
5. Does auto-compaction trigger correctly at 75k tokens?

## Rollback Plan

If v2 has critical issues:

1. Set `MARCEL_USE_V2=false` (instant rollback to v1)
2. Original Markdown conversations still intact in `conversations/archive/`
3. Can restore from archive if needed
4. V1 code still present during grace period

## Contact / References

- Issue: `project/issues/wip/ISSUE-031-migrate-to-pydantic-ai-harness.md`
- Architecture plan: `/home/sagemaker-user/.claude/plans/polished-humming-pixel.md`
- Test script: `./test_v2.sh` or `make test-v2 MSG="your message"`
- Test endpoint: `http://localhost:7421/v2/chat` (WebSocket)

---

**Ready to continue on NUC!** Start with Phase 4.1 (environment setup) and work through the testing checklist.
