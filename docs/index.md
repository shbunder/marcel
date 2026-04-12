# Marcel — Developer Documentation

Marcel is a self-adapting personal agent for families and small households. A technically inclined person sets it up once on a home server; everyone else — partners, kids, parents — uses it through Telegram on their phone or a terminal on their laptop.

Built on Claude (Anthropic), Marcel understands natural language, reads calendars, tracks household facts, runs scheduled jobs, and — because it has access to its own codebase — can modify and redeploy itself at the user's request.

These docs are written for **developers** extending Marcel, integrating new skills, or building on the codebase. If you are looking for end-user help, chat with Marcel directly.

## Where to start

| If you are… | Read this |
|-------------|-----------|
| Setting Marcel up for the first time | [README](https://github.com/shbunder/marcel/blob/main/README.md) → [SETUP](https://github.com/shbunder/marcel/blob/main/SETUP.md) |
| Trying to understand the codebase | [Architecture](architecture.md) |
| Adding a new skill or integration | [Skills](skills.md), then [Banking](integration-banking.md) or [News](integration-news.md) as reference |
| Storing or querying user data | [Storage](storage.md) |
| Writing a background job | [Jobs](jobs.md) |
| Building a native frontend | [A2UI Components](a2ui-components.md), [Artifacts](artifacts.md) |
| Modifying how Marcel rewrites itself | [Self-Modification](self-modification.md) |
| Working on the CLI | [CLI](cli.md) |

## Core concepts

**Two instruction sets.** Marcel has two "modes" governed by two different sets of instruction files:

- `MARCEL.md` files under `~/.marcel/` describe how Marcel behaves as a **personal assistant**. These are loaded into every conversation's system prompt.
- `CLAUDE.md` files in the repo describe how Marcel behaves when it is **rewriting its own code** (coder mode). These guide the inner Claude Code loop.

The two never mix — personal assistant context doesn't leak into coder mode, and vice versa.

**Flat-file storage.** Everything Marcel remembers lives on disk as plain text or markdown — no database. User data is under `~/.marcel/users/{slug}/`, skills are under `~/.marcel/skills/`, conversations are append-only JSONL segments. See [Storage](storage.md) for the full layout.

**Skills are pluggable.** Every integration (banking, calendar, news, etc.) is a self-contained skill: a Python module registered with `@register("name.action")` plus a `SKILL.md` doc file. Skills can be added or removed without touching core code. See [Skills](skills.md).

**One agent tool per capability.** Instead of advertising dozens of tools to the LLM, Marcel uses a small set of dispatcher tools (`marcel`, `integration`) that route to many actions. This keeps prompt token usage low and reduces tool-selection confusion.

**Recoverable self-modification.** Before Marcel rewrites its own code, it commits the current state to git. A watchdog health-checks every restart and rolls back on failure. See [Self-Modification](self-modification.md).

## Four principles

Every change to Marcel must uphold these principles (see [CLAUDE.md](https://github.com/shbunder/marcel/blob/main/CLAUDE.md)):

1. **Lightweight over bloated.** No unnecessary dependencies. Every skill and integration is self-contained and removable.
2. **Generic over specific.** Strong extension points beat hardcoded one-offs. Build primitives, not special cases.
3. **Human-readable over clever.** Error messages, logs, and responses are read by non-technical family members as often as by developers.
4. **Recoverable over fast.** Every self-modification is committed before running. No change is worth an unrecoverable break.

## Contributing

Contributions follow the feature development procedure in [project/CLAUDE.md](https://github.com/shbunder/marcel/blob/main/project/CLAUDE.md): capture the request, create an issue, implement, test, document, ship. Coding conventions are in [project/CODING_STANDARDS.md](https://github.com/shbunder/marcel/blob/main/project/CODING_STANDARDS.md).

Documentation is non-optional — every shipped feature gets a doc page or section update in the same change. See [docs/CLAUDE.md](https://github.com/shbunder/marcel/blob/main/docs/CLAUDE.md) for documentation standards.
