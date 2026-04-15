# Marcel — Developer Guide

Marcel is a self-adapting personal agent built on top of Claude Code. It can observe its own behavior, identify gaps, and rewrite the code and configuration that governs how it works — including this very file.

## Two modes, two instruction sets

- **Personal assistant mode** — governed by `MARCEL.md` files under the data root (`~/.marcel/` or `$MARCEL_DATA_DIR`), loaded in order: `<data_root>/MARCEL.md` (global), `<data_root>/users/<slug>/MARCEL.md` (per-user). These are injected by Marcel's own system prompt builder and describe how Marcel should behave as a household assistant.
- **Developer / self-modification mode** — governed by this file and the files in `project/`. These are read by the Claude Code inner loop when Marcel modifies its own codebase.

You are reading CLAUDE.md, so you are in **developer mode**.

> **Self-modification note:** Auth logic, core config, and safety rules (including these CLAUDE.md files) are off-limits unless the user explicitly grants permission for a specific change. When in doubt, ask before touching them. See [Self-Modification Safety](project/CLAUDE.md#self-modification-safety) in project/CLAUDE.md.

## When performing code changes

- Core rules and feature workflow: [project/CLAUDE.md](project/CLAUDE.md) (and the referenced `FEATURE_WORKFLOW.md`, `CODING_STANDARDS.md`).
- Issue management and git conventions: [project/issues/CLAUDE.md](project/issues/CLAUDE.md) (and the referenced `TEMPLATE.md`, `GIT_CONVENTIONS.md`).
- Documentation: [docs/CLAUDE.md](docs/CLAUDE.md) — ships in the same change as the code.

## Core Principles

- **Lightweight over bloated** — Marcel should have no unnecessary dependencies. Every skill and integration must be self-contained and removable.
- **Generic over specific** — a general extension point is better than a hardcoded one-off. Prefer strong primitives that let users build things we haven't anticipated.
- **Human-readable over clever** — error messages, logs, and responses are read by non-technical family members as often as by developers.
- **Recoverable over fast** — before any self-modification, commit current state to git. No change is worth an unrecoverable break.

## Skill system overview

Integration skills are documented in `<data_root>/skills/` (`~/.marcel/skills/`) — each skill directory has a `SKILL.md` (full integration docs) and an optional `SETUP.md` (shown when the integration isn't configured). Default skills are bundled in `src/marcel_core/defaults/skills/` and seeded to the data root on first startup. The loader is in `src/marcel_core/skills/loader.py`.

Developer workflow skills (`new-issue`, `finish-issue`) remain in `.claude/skills/` — they are Claude Code skills for this developer session, not Marcel runtime skills.
