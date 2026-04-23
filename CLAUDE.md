# Marcel — Developer Guide

You are in **developer mode**: modifying Marcel's codebase. (Personal-assistant mode, where Marcel helps a family, is governed by `MARCEL.md` files under `~/.marcel/` and never reaches this file.)

Marcel is a self-adapting personal agent built on Claude Code — it can observe itself, identify gaps, and rewrite the code and configuration that governs how it works. A PreToolUse hook ([`.claude/hooks/guard-restricted.py`](.claude/hooks/guard-restricted.py)) enforces the restricted-path rule automatically — you do not need to memorize which paths are off-limits, the hook will tell you and give you the unlock procedure. See [docs/claude-code-setup.md](docs/claude-code-setup.md) for the setup overview.

## Commands

```bash
make serve          # dev container (Docker, uvicorn --reload on :7421, separate from prod :7420)
make serve-logs     # tail the dev container logs
make serve-down     # stop the dev container
make check          # format + lint + typecheck + tests with 90% coverage (also runs as pre-commit hook)
make test           # tests only
make cli-dev        # build + run the Rust CLI in debug mode
make docker-logs    # tail the prod container logs
```

Dev and prod both run as Docker containers on different ports: `make serve` brings up `marcel-dev` on `:7421` via `docker-compose.dev.yml`; `make docker-up` brings up `marcel` on `:7420` via `docker-compose.yml`. Both can run simultaneously and share one restart mechanism (env-aware flag files — see [docs/self-modification.md](docs/self-modification.md)).

## Core principles

- **Lightweight over bloated.** Marcel has no unnecessary dependencies. Every skill and integration must be self-contained and removable.
- **Generic over specific.** A general extension point beats a hardcoded one-off. Prefer strong primitives.
- **Human-readable over clever.** Error messages, logs, and responses are read by non-technical family members as often as by developers.
- **Recoverable over fast.** Before any self-modification, commit current state to git. No change is worth an unrecoverable break.

## Habitat taxonomy (summary)

The kernel ships no behaviour. Everything Marcel can *do* lives in one of five kinds of habitat under `$MARCEL_ZOO_DIR`:

| Kind | Directory | Shape | Teaches / runs |
|---|---|---|---|
| **Toolkit** | `toolkit/<name>/` | `@marcel_tool` handlers + `toolkit.yaml` | Python code the agent can call |
| **Skill** | `skills/<name>/` | `SKILL.md` (+ `SETUP.md`) | *When* to reach for a toolkit |
| **Subagent** | `agents/<name>.md` | single Markdown | Scoped sub-pass the main agent can `delegate()` to |
| **Channel** | `channels/<name>/` | router + `channel.yaml` | Inbound webhooks + outbound push (Telegram, …) |
| **Job** | `jobs/<name>/template.yaml` | YAML template | Scheduled work; `dispatch_type` picks tool / subagent / agent shape |

Full taxonomy + decision flowchart + minimal examples: [docs/habitats.md](docs/habitats.md).

## When performing code changes

- Feature workflow and core rules: [project/CLAUDE.md](project/CLAUDE.md) (→ [FEATURE_WORKFLOW.md](project/FEATURE_WORKFLOW.md), [CODING_STANDARDS.md](project/CODING_STANDARDS.md))
- Issue management and git conventions: [project/issues/CLAUDE.md](project/issues/CLAUDE.md) (→ [TEMPLATE.md](project/issues/TEMPLATE.md), [GIT_CONVENTIONS.md](project/issues/GIT_CONVENTIONS.md))
- Documentation: [docs/CLAUDE.md](docs/CLAUDE.md) — docs ship in the same change as the code

## Subagents and skills

- **Workflow skills** in [.claude/skills/](.claude/skills/): `/new-issue`, `/parallel-issue`, `/finish-issue`.
- **Subagents** in [.claude/agents/](.claude/agents/): `pre-close-verifier` (invoked automatically by `/finish-issue`), `plan-verifier` (invoked by `/new-issue` at open→wip for non-trivial issues), `code-reviewer` (5-axis review with Marcel context), `security-auditor` (scoped to Marcel's real attack surface). Delegate file-heavy investigation to these rather than reading in the main context.

Runtime skills (what Marcel can *do* as an assistant — calendar, banking, news, …) live under `~/.marcel/skills/` and are unrelated to developer-mode work. See [docs/skills.md](docs/skills.md) if you need to touch them.
