# Rule — no model calls in Marcel's own code

Marcel v3 is a shell around the unmodified `claude` CLI on a Claude Max subscription. Tokens are only ever spent by a
`claude` process that the runner starts.

## Never

- The `anthropic` SDK, the Agent SDK, or any HTTP call to `api.anthropic.com` from `hub/`, `runner/`, `plugins/`,
  `brain/` or `ios/`.
- An API key in config, env files, code or tests.
- A test that reaches Claude or the network. Use the fake `claude` shim (S-02.3), recorded fixtures and `respx`.
  Tests that need the real thing are marked `live` and are never part of a gate.

## Why

The subscription is the budget. A stray SDK call bills a separate account, bypasses the usage guard (F10) and
breaks the "Claude Code does the thinking" principle.

## Enforcement

`code-reviewer` and `security-auditor` flag any import of `anthropic` or `claude_agent_sdk` and any
`ANTHROPIC_API_KEY`. `make check` greps for them once S-01.2 lands.
