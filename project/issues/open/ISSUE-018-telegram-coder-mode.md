# ISSUE-018: Telegram Coder Mode — Self-Modification via Claude Code SDK

**Status:** Open
**Created:** 2026-03-29
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature, telegram, agent, self-modification

## Capture
**Original request:** "Telegram coder mode — enable self-modification via Telegram using Claude Code SDK preset"

**Follow-up Q&A:** Preceded by a design discussion exploring four architecture options. The Claude Code SDK's `tools={"type": "preset", "preset": "claude_code"}` preset was identified as the best approach — it gives a spawned agent the full Claude Code toolset without needing a raw shell tool or CLI subprocess.

**Resolved intent:** When a user asks Marcel via Telegram to implement a feature, fix a bug, or modify its own code, Marcel should detect the coder-mode intent, spawn a dedicated Claude Code agent with full file/shell/git capabilities, follow the complete issue lifecycle (create → implement → test → ship), and respond via Telegram with the commit message and implementation summary. Currently the Telegram agent has no tools for code changes and silently fails or gives up.

## Description

The Telegram agent only has `cmd` (HTTP skills), `notify`, and iCloud tools. It cannot write files, run shell commands, or interact with git. This makes it unable to act on any code-change request.

The fix is a separate coder-mode path: an intent classifier detects coder requests, a dedicated agent is spawned with the `claude_code` tool preset (full Claude Code capabilities), and the result is delivered back to the user via Telegram. The existing watchdog handles restart and rollback after code changes.

## Tasks
- [ ] ISSUE-018-a: Create intent classifier (`src/marcel_core/agent/classifier.py`) — single-turn LLM call returning YES/NO for coder-mode detection
- [ ] ISSUE-018-b: Create coder agent runner (`src/marcel_core/agent/coder.py`) — spawns `claude_agent_sdk.query()` with `claude_code` preset, coder system prompt, progress callbacks, pre-change SHA capture
- [ ] ISSUE-018-c: Add safety guardrails — `can_use_tool` callback for restricted files, `asyncio.Lock` for concurrency, dirty-tree check, cost/turn limits
- [ ] ISSUE-018-d: Integrate into webhook (`src/marcel_core/telegram/webhook.py`) — route coder requests to `run_coder_task`, 600s timeout, deliver commit + summary via Telegram
- [ ] ISSUE-018-e: Export new functions from `src/marcel_core/agent/__init__.py`
- [ ] ISSUE-018-f: Verify `claude_code` preset works at runtime — spike test confirming CLAUDE.md auto-discovery and tool availability
- [ ] ISSUE-018-g: Write tests (`tests/core/test_coder.py`) — classifier, coder runner (mocked SDK), webhook routing, restricted-file guard
- [ ] ISSUE-018-h: Run `make check`, update docs, version bump

## Relationships
- Related to: [[ISSUE-013-fix-telegram-agent-hang]] (same root cause — Telegram agent limitations)
- Related to: [[ISSUE-014-sop-telegram-issue-tracking]] (coder mode must follow the Telegram SOP)

## Comments
### 2026-03-29 - Design
Architecture decision: Option B (Claude Code SDK with `claude_code` preset) chosen over raw shell tool (fragile), CLI subprocess (overhead), or SDK sub-agent mechanism (`AgentDefinition.tools` doesn't support presets). See conversation for full trade-off analysis.

Key open questions to verify early:
1. Does `tools={"type": "preset", "preset": "claude_code"}` work at runtime?
2. Does the coder agent auto-discover CLAUDE.md when `cwd` is set?
3. Concurrent git safety (assistant memory extraction doesn't touch git, so likely fine)

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
