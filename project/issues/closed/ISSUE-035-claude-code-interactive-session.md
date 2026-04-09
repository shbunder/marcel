# ISSUE-035: Upgrade claude_code tool to interactive stream-json session

**Status:** Closed
**Created:** 2026-04-09
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature

## Capture
**Original request:** claude_code tool: upgrade to interactive stream-json session so Marcel can relay questions from Claude Code to the user mid-task

**Follow-up Q&A:**
- Observed during documentation review of `.marcel/MARCEL.md` — MARCEL.md describes Marcel as a "session shell" around Claude Code, but the current tool is one-shot and cannot relay questions mid-task.

**Resolved intent:** The current `claude_code` tool in `src/marcel_core/tools/claude_code.py` invokes Claude Code as a one-shot subprocess (`claude-code --message task`), capturing stdout/stderr and returning the result. This means Claude Code cannot ask clarifying questions or request user confirmation mid-task. The intent is to upgrade the tool to use Claude Code's stream-json protocol (`--output-format stream-json --input-format stream-json`), so Marcel can parse mid-task events, relay any questions or permission prompts to the user over the active channel, receive the user's answer, and pass it back to the Claude Code process — making Marcel a true interactive session shell around Claude Code.

## Description

Claude Code supports a bidirectional stream-json protocol where it emits structured events (tool use, questions, results) on stdout and accepts responses on stdin. This is the correct mechanism for embedding Claude Code inside another agent loop.

The previous implementation (`subprocess` + `asyncio.wait_for`) only handled the one-shot case: fire, wait, collect output. It could not:
- Parse events as they arrive
- Detect when Claude Code needs user input
- Route a question from Claude Code back to the Marcel user
- Feed the user's answer back to the Claude Code process

The new implementation uses `claude -p --output-format stream-json --verbose --dangerously-skip-permissions`:
1. Streams events from Claude Code stdout as a line-delimited JSON event loop
2. On `system/init` events — captures `session_id` needed for resume
3. On `assistant` text blocks — accumulates and sends progress via `notify`
4. On `AskUserQuestion` tool use — kills the process, returns `PAUSED:{session_id}:{question}` so Marcel's agent can relay to user
5. On `result` event — returns final result text

Resume flow: when Marcel's agent calls `claude_code(task=answer, resume_session=session_id)`, the `--resume` flag continues the session from where it paused.

Permission prompts (tool use approval) are bypassed via `--dangerously-skip-permissions` since Marcel always operates on its own trusted codebase. Genuine questions (the `AskUserQuestion` tool) are still relayed.

## Tasks
- [✓] Research the Claude Code stream-json protocol: what events are emitted, what the input format looks like, how to signal an answer
- [✓] Design the new `claude_code` tool interface: same external signature (`task`, `timeout`), but internally event-driven
- [✓] Implement the streaming event loop in `src/marcel_core/tools/claude_code.py`
- [✓] Wire mid-task questions through the `notify` tool / active channel so the user sees them in the chat UI
- [✓] Implement answer capture: Marcel's agent pauses the claude_code call and waits for user reply, then resumes
- [✓] Handle timeout correctly across the entire interactive session (not just a single wait)
- [✓] Write tests covering: normal completion, mid-task question + answer, timeout, Claude Code not installed
- [✓] Update docstring and inline comments in `claude_code.py` to reflect the new protocol
- [✓] Run `make check` — all checks must pass

## Relationships
- Related to: [[ISSUE-033-marcel-md-system]] (MARCEL.md update that prompted this issue)

## Implementation Log

### 2026-04-09 - LLM Implementation
**Action**: Upgraded claude_code tool from one-shot subprocess to streaming stream-json session with question relay
**Files Modified**:
- `src/marcel_core/tools/claude_code.py` — rewrote to use `claude -p --output-format stream-json --verbose --dangerously-skip-permissions`; added streaming event loop, `notify` progress forwarding, `AskUserQuestion` interception, `PAUSED:` return protocol, `--resume` support, `resume_session` parameter, and proper process cleanup in `finally` block
- `tests/tools/test_claude_code.py` — created; 8 tests covering normal completion, question relay, resume flag, timeout, CLI not found, non-zero exit, empty output, notify call count
- `.marcel/MARCEL.md` — updated Self-modification section to explain developer mode and session-shell pattern
**Commands Run**: `.venv/bin/pytest tests/tools/test_claude_code.py -v` → 8 passed; `make check` → 0 new errors
**Result**: All 8 tests pass; no regressions

**Reflection**:
- Coverage: 9/9 requirements addressed
- Shortcuts found: missing `proc.wait()` in `finally` block (PAUSED: early return left zombie process) — fixed before close
- Scope drift: none; implementation matches requirements exactly
