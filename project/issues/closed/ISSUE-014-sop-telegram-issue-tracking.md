# ISSUE-014: SOP — Issue Tracking and Commit Response for Telegram-Initiated Changes

**Status:** Closed
**Created:** 2026-03-29
**Assignee:** Claude Code
**Priority:** Medium
**Labels:** process, telegram, docs

## Capture
**Original request:** "make it SOP that if I ask for changes through Marcel, proper issues are created!! once they are finished I want Marcel to respond with the git commit message and issue log"

**Resolved intent:** Establish a binding standard operating procedure: any code change requested via Telegram must go through the full issue lifecycle (open → wip → closed), and after shipping the developer must respond to the user via Telegram with the exact git commit message and a summary of the issue log. Documented in `project/CLAUDE.md` so it governs all future coder-mode work regardless of how the request arrived.

## Description

Without an explicit SOP, Telegram-initiated requests could be acted on without an issue trail, making work invisible in the project history. This issue documents the rule in the developer guide so it applies automatically whenever Claude Code operates in coder mode.

## Tasks
- [✓] Add "Telegram-Initiated Changes" section to `project/CLAUDE.md`

## Implementation Log

### 2026-03-29 - Claude Code
**Action**: Added SOP section to developer guide
**Files Modified**:
- `project/CLAUDE.md` — added "Telegram-Initiated Changes" section describing the issue-first rule and the commit-response requirement
**Result**: Rule is now part of the coder-mode guide and will be applied in all future conversations
