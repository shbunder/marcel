# ISSUE-023: Redesign Skill System

**Status:** Closed
**Created:** 2026-04-02
**Assignee:** Shaun
**Priority:** High
**Labels:** feature, architecture

## Capture
**Original request:** Redesign skill system: merge chat/coder into single assistant mode, rename cmd to integration, make integrations pluggable python modules with @register decorator, move skill docs to .claude/skills/ SKILL.md format, remove separate iCloud MCP server

**Follow-up Q&A:**
- Single mode: generous turn limit for all channels, no chat/coder split
- Tool name: rename `cmd` → `integration`
- Python integrations: modular design with `@register` decorator, each module self-contained
- Skill docs: use `.claude/skills/` SKILL.md format (same as Claude Code native skills) so integration skills and pure-prompt skills coexist seamlessly
- Each SKILL.md teaches the agent how to call `integration(skill=..., params=...)` with inline examples

**Resolved intent:** Unify Marcel's two assistant modes (chat and coder) into a single agent and redesign the skill/integration system so that every integration is a pluggable Python module registered via decorator, exposed through a single `integration` tool, and documented via Claude Code-native `.claude/skills/` SKILL.md files that teach the agent how to use each integration.

## Description

The current architecture has two problems:

1. **Artificial mode split.** Chat mode (runner.py, 10 turns) and coder mode (coder.py, 75 turns) use the same `claude_code` tools preset. The only real differences are turn limit and system prompt framing. A single agent with generous turns can handle both.

2. **Inconsistent integration patterns.** Skills in `skills.json` go through the `cmd` tool (data-driven), but iCloud has its own MCP server with hardcoded tools (code-driven). Adding a new integration requires understanding which pattern to use and wiring it differently.

The redesign introduces:
- One assistant mode with generous turn limits everywhere
- A single `integration` tool (renamed from `cmd`) as the universal dispatcher
- Pluggable Python integration modules using `@register("name")` decorator
- `.claude/skills/` SKILL.md files that teach the agent how to call each integration
- Auto-discovery of integration modules at startup

## Tasks
- [✓] ISSUE-023-a: Create the integration module framework (`skills/integrations/`) with `@register` decorator and auto-discovery
- [✓] ISSUE-023-b: Migrate iCloud from separate MCP server to a python integration module (`integrations/icloud.py`)
- [✓] ISSUE-023-c: Rename `cmd` → `integration` in tool.py, update tool description and schema
- [✓] ISSUE-023-d: Update executor.py to support `type: "python"` skills that dispatch to registered functions
- [✓] ISSUE-023-e: Create `.claude/skills/icloud/SKILL.md` teaching the agent how to use iCloud via `integration`
- [✓] ISSUE-023-f: Merge chat/coder into single assistant mode — remove coder.py, unify runner.py with generous turn limit
- [✓] ISSUE-023-g: Remove `icloud/tool.py` (separate MCP server) and update runner.py to drop the icloud MCP server
- [✓] ISSUE-023-h: Update existing `.claude/skills/` and CLAUDE.md files to reflect new architecture
- [✓] ISSUE-023-i: Update docs (architecture.md, skills.md) to document new system
- [✓] ISSUE-023-j: Run `make check` — all tests pass, lint/format/typecheck clean

## Subtasks

## Relationships
- Related to: [[ISSUE-021-claude-agent-sdk-migration]]

## Comments

## Implementation Log
<!-- Append entries here when performing development work on this issue -->
