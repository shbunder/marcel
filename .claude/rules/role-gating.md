---
paths:
  - "src/marcel_core/harness/**/*.py"
  - "src/marcel_core/tools/**/*.py"
  - "src/marcel_core/agents/**/*"
---

# Rule — role gating

Marcel's tools are split into two tiers. The split is enforced at **harness startup**, not at runtime inside tool bodies.

| Tier | Tools | Exposed to |
|---|---|---|
| **Admin** | `bash`, `read_file`, `write_file`, `edit_file`, `git_*`, `claude_code`, `delegate`, `code_exec`, `promote_extension` | Users with `role: admin` in their `profile.md` frontmatter |
| **User** | `integration`, `marcel` | Everyone (admins and non-admins) |

Non-admins must **never** see an admin tool in their tool pool. The model cannot refuse a tool it cannot see — this is the **primary defense**, and it is enforced structurally (by not registering the tool) rather than procedurally (by having the tool check and refuse).

## Two layers, both in the harness

Since FEAT-260628-2cd78e (F0) role-gating is enforced at **two** harness-level points — never inside a tool body:

1. **Structural (primary).** `create_marcel_agent` never *registers* an admin tool for a non-admin. The tool is absent from the model's pool; it cannot be called. This is unchanged and remains the real trust boundary.
2. **Event-bus `tool_call` handler (defense-in-depth).** A core handler (`marcel_core.harness.core_handlers`) subscribed to the `tool_call` lifecycle event blocks an admin-tier call made in a non-admin turn *before it executes*. This is a **harness** enforcement point (the bus denies the call), **not** a check inside the tool body and **not** model discretion. It exists to catch a tool that reaches the pool by a path that bypasses registration-time filtering — e.g. a dynamically- or extension-registered admin tool.

The admin-tier set has one source of truth: `admin_tool_names()` in `harness/agent.py`, used by both layers.

## Rules for new tools

1. **Every new tool declares its tier** at registration time, in the harness (the `_TOOL_REGISTRY` `role_required` column). Not as a runtime check inside the tool body.
2. **The structural role check happens once**, at harness startup, when building the agent's tool set for a specific user. The bus handler is an additional pre-execution gate, not a substitute — a new admin tool still declares its tier so *both* layers see it.
3. **Subagents inherit the parent's role** — but the `delegate` tool is stripped from every child's tool pool unless the child's frontmatter explicitly opts in. This prevents recursion-based role escalation.
4. **A non-admin asking for an admin operation** should receive a polite refusal from the `marcel` utility tool. They never see the admin tool and cannot trick the model into calling a tool that isn't in its pool.

## Never

- Adding a new admin-tier tool without also adding the tier declaration to the harness registration code
- Gating a tool at runtime via an `if user.role == "admin": ...` check inside the tool body — this shifts enforcement to the model's discretion, which is a trust boundary Marcel explicitly does not give the model
- Exposing `bash`, `claude_code`, `delegate`, or `git_*` to a non-admin session, even "temporarily for debugging"

## Why

Marcel runs on a shared home server. The zoo keeper (admin) trusts Marcel to run arbitrary shell commands on their behalf. The kids — who chat with Marcel on Telegram like any other contact — do not get that authority. Role-gating enforces the trust boundary at the harness level so that prompt injection, confused-deputy attacks, or the model simply being helpful cannot cross the boundary.

## Enforcement

- [.claude/agents/security-auditor.md](../agents/security-auditor.md) treats any new admin-tier tool without an explicit tier declaration as **Critical**, and any runtime role check inside a tool body as **High**.
- [.claude/agents/code-reviewer.md](../agents/code-reviewer.md) verifies that tool registrations in `harness/` are tier-explicit and that `delegate` is stripped from child tool pools.
