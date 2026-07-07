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

**Prefer `make` targets over bare commands** (`uv sync`, `pytest`, `docker …`) whenever a target exists — the targets encode the full environment contract. Concretely: a bare `uv sync` leaves zoo park dep-venvs stale or missing; `make env-install` / `make env-sync` provision them too (`make zoo-deps` on its own re-provisions). The same applies in marcel-zoo (`make test`, `make deps`) and odile (`make check`).

## Core principles

These four are Marcel's constitution — the standard every change is held to and the name every review cites. Refer to one by its **handle** (e.g. "Core principle: Recoverable"); the [`plan-verifier`](.claude/agents/plan-verifier.md) and [`pre-close-verifier`](.claude/agents/pre-close-verifier.md) name the relevant handle in their findings, and the [rules](.claude/rules/) enforce them mechanically.

- **Lightweight** *(over bloated).* Marcel has no unnecessary dependencies. Every skill and integration must be self-contained and removable — the [toolkit-skill-pairs](.claude/rules/toolkit-skill-pairs.md) rule keeps each habitat shipping complete, never half.
- **Generic** *(over specific).* A general extension point beats a hardcoded one-off. Prefer strong primitives.
- **Human-readable** *(over clever).* Error messages, logs, and responses are read by non-technical family members as often as by developers.
- **Recoverable** *(over fast).* Before any self-modification, commit current state to git. No change is worth an unrecoverable break — enforced by [self-modification](.claude/rules/self-modification.md) (the one legal restart path) and [debugging](.claude/rules/debugging.md) (a regression test ships with every fix).

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

- Feature workflow, coding standards, and versioning live on the **marcel-admin board** under `$MARCEL_ADMIN_DIR/WIKI/docs/` (`feature-workflow.md`, `coding-standards.md`, `versioning.md`). The enforceable single-concept rules live in [.claude/rules/](.claude/rules/) and auto-load each session.
- Work tracking + knowledge: the **marcel-admin board** — a separate OKF-bundle repo at `$MARCEL_ADMIN_DIR` (default `~/projects/marcel-admin`). Conventions live in `$MARCEL_ADMIN_DIR/JIRA/CLAUDE.md`. Work is **Feature → Story → Subtask**; each feature links to a requirements page and ADRs. (The retired in-repo `project/issues/` is archived read-only at `$MARCEL_ADMIN_DIR/JIRA/archive/`.)
- Documentation: [docs/CLAUDE.md](docs/CLAUDE.md) — the developer/SDK reference (mkdocs); docs ship in the same change as the code. Working docs (requirements, ADRs) live on the board, not here.

## Subagents and skills

- **Workflow skills** in [.claude/skills/](.claude/skills/): `/new-feature`, `/new-story`, `/new-adr`, `/finish-feature`, `/parallel-feature` — cross-repo wrappers around the marcel-admin board.
- **Subagents** in [.claude/agents/](.claude/agents/): `pre-close-verifier` (invoked by `/finish-feature` before merge), `plan-verifier` (invoked by `/new-feature` to check the requirements + ADR + story breakdown are concrete), `code-reviewer` (5-axis review with Marcel context), `security-auditor` (scoped to Marcel's real attack surface). Delegate file-heavy investigation to these rather than reading in the main context.

Runtime skills (what Marcel can *do* as an assistant — calendar, banking, news, …) live under `~/.marcel/skills/` and are unrelated to developer-mode work. See [docs/skills.md](docs/skills.md) if you need to touch them.
