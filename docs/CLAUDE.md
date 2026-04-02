# docs/ Guidelines

Documentation in this directory is written for **developers** — contributors extending Marcel, integrating skills, or building on top of the codebase. It is not end-user documentation for family members who interact with Marcel conversationally. Keep the two audiences strictly separate: if family-facing docs are ever needed, put them in `docs/user/` with their own conventions, not here.

## What belongs here

Document public APIs, skill interfaces, integration patterns, and configuration options. Do not document internal mechanics, framework abstractions, or implementation details that users cannot control or configure.

Every new feature shipped must have a corresponding doc page or section update **in the same change**. Undocumented features do not exist to the next developer (or agent) working in this codebase.

## Directory structure

New doc pages live directly under `docs/` — e.g., `docs/skills-calendar.md`, `docs/integration-home-assistant.md`. There are no enforced subdirectories; keep it flat unless a clear grouping emerges with three or more related pages.

**Every new page must be registered in `mkdocs.yml`** under the `nav:` key. A page that exists but isn't in the nav is invisible to anyone browsing the site — treat a missing nav entry as a bug.

## Documentation requirements for new features

When [project/CLAUDE.md](../project/CLAUDE.md) asks you to document a feature, that means:

1. **A dedicated page** for any substantial feature (new skill type, integration pattern, major config option). Add it to `docs/` and register it in `mkdocs.yml`.
2. **A section update** on an existing page for smaller additions (a new parameter, a new option on an existing skill).
3. **Docstrings** on all public classes and functions, written so `mkdocstrings` can render them cleanly.

If a feature is too small to warrant its own page, it still needs at least one code example and a clear prose description wherever it naturally belongs.

## Writing style

- **Explain before showing** — place context and intent before code examples, not after. Readers need to understand what they're looking at before they see it.
- **Show only the relevant part** — strip scaffolding from examples. If the point is a skill's `integration()` call, don't pad the example with unrelated setup.
- **Write from the user's perspective** — describe what a developer can do, not how the internals work.
- **Mark optional parameters explicitly** — don't make readers parse type signatures to understand optionality.
- **Link rather than duplicate** — if a concept is already explained elsewhere, link to it. Duplicated explanations drift apart and mislead.

## Code examples

- All examples in docs must be runnable and type-correct — no `# type: ignore` or `# noqa` unless the example is intentionally demonstrating an error.
- Use realistic values, not placeholder strings like `"your-api-key"` or `"TODO"`.
- When showing a skill or integration, include the full invocation pattern so a developer can copy and adapt it immediately.

## Keeping docs in sync

Docs that describe a feature that no longer exists, or omit a parameter that was added, are worse than no docs. When modifying a feature:

- Update all affected doc pages in the same change as the code.
- Remove sections that explain behavior that was removed or changed.
- If a page references external APIs or services, link to the authoritative external source rather than copying content that will go stale.
