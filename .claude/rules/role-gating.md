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
| **Admin** | `bash`, `read_file`, `write_file`, `edit_file`, `git_*`, `claude_code`, `delegate` | Users with `role: admin` in their `profile.md` frontmatter |
| **User** | `integration`, `marcel` | Everyone (admins and non-admins) |

Non-admins must **never** see an admin tool in their tool pool. The model cannot refuse a tool it cannot see — this is the primary defense, and it is enforced structurally (by not registering the tool) rather than procedurally (by having the tool check and refuse).

## Rules for new tools

1. **Every new tool declares its tier** at registration time, in the harness. Not as a runtime check inside the tool body.
2. **The role check happens once**, at harness startup, when building the agent's tool set for a specific user. Not per-call.
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
