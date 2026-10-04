# Rule — Marcel's own code never calls a model

Every model token is spent by the unmodified `claude` CLI that the runner starts, on the owner's
Claude subscription. That is what Anthropic permits for individual use, and it is the design.

## Never
- Import `anthropic`, `claude_agent_sdk` / `@anthropic-ai/claude-agent-sdk`, `openai` or any model SDK
- Add an `ANTHROPIC_API_KEY`, a `CLAUDE_CODE_OAUTH_TOKEN` setup-token, or any model provider key
- Call a hosted decision model (Jev, Clef on Workers AI). The token economy is handled by the
  wake policy (S-06.5). A decision model needs a measured case (S-16.2) and the owner's OK

## Where judgement is needed
Ask the brain: it is a `claude` session. Hub code decides only what can be written as a rule.
