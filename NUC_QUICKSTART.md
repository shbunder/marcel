# Marcel V2 - NUC Quick Start

## 🚀 First Steps on NUC

### 1. Pull Latest Code
```bash
cd ~/projects/marcel
git pull origin main  # 13 commits ahead (Phase 1-3 complete)
```

### 2. Configure Bedrock
Add to `.env.local` (create if doesn't exist):
```bash
# AWS Bedrock for pydantic-ai
export AWS_REGION=eu-west-1
export ANTHROPIC_API_KEY=dummy-key-not-used-with-bedrock
```

### 3. Test V2 Works
```bash
# Terminal 1: Start server
make serve

# Terminal 2: Test v2 endpoint
make test-v2 MSG="Hello Marcel!"
# Expected: Clean response with no errors

make test-v2 MSG="What's today's date?"
# Expected: Response + "🔧 Tool: bash ✓"

make test-v2 MSG="Check my banking balance"  
# Expected: Response + "🔧 Tool: integration ✓"
```

### 4. Check What Changed
```bash
# Review key commits
git log --oneline -13

# Key files to check:
cat src/marcel_core/harness/agent.py        # Bedrock model mapping
cat src/marcel_core/harness/runner.py       # Tool streaming
cat src/marcel_core/api/chat_v2.py          # V2 endpoint
```

### 5. Read the Full Plan
```bash
cat MIGRATION_PLAN.md  # Complete Phase 4-5 roadmap
```

## ✅ What's Complete (Phase 1-3)

- V2 harness with pydantic-ai (multi-provider support)
- AWS Bedrock integration (native boto3, no proxy!)
- JSONL conversation history
- Tool call streaming (FunctionToolCallEvent/ResultEvent)
- All tools working: bash, git, files, integrations
- WebSocket adapter (single adapter for all channels!)
- V2 endpoint at `/v2/chat` running in parallel with v1

## 📋 Next: Phase 4 Testing & Migration

See `MIGRATION_PLAN.md` for detailed instructions.

Start with:
1. Environment setup (add AWS_REGION to .env.local)
2. Verify v2 works on NUC
3. Test all integrations
4. Write migration script (Markdown → JSONL)

## 🆘 If Issues

1. **V2 doesn't respond:** Check AWS credentials, verify AWS_REGION in .env.local
2. **Tools not streaming:** Check server logs for errors in event_stream_handler
3. **Import errors:** Run `uv sync` to ensure all dependencies installed
4. **Test script fails:** Ensure server is running (`make serve`)

## 📞 References

- Full plan: `MIGRATION_PLAN.md`
- Issue tracker: `project/issues/wip/ISSUE-031-migrate-to-pydantic-ai-harness.md`
- Test script: `./test_v2.sh MSG="your message"`
- V2 endpoint: `ws://localhost:7421/v2/chat`
