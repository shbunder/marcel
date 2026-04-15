# docs/ Guidelines

Developer documentation only — contributors extending Marcel, integrating skills, or building on top of the codebase. NOT end-user documentation for family members. If family-facing docs are ever needed, put them in `docs/user/` with their own conventions.

## Rules

- **Document public APIs, skill interfaces, integration patterns, config options.** Do NOT document internal mechanics or framework abstractions.
- **Every new feature ships with its doc.** New page or section update in the same change as the code. Undocumented features do not exist.
- **Register new pages in `mkdocs.yml`** under `nav:`. A page missing from nav is invisible — treat it as a bug.
- **All examples must be runnable and type-correct.** No `# type: ignore` or `# noqa` unless demonstrating an error. Use realistic values, not `"your-api-key"` placeholders.
- **Link rather than duplicate.** Explanations drift apart when copied.

## Scope

1. **Dedicated page** for substantial features (new skill type, integration pattern, major config option).
2. **Section update** on an existing page for smaller additions (new parameter, new option).
3. **Docstrings** on all public classes and functions, rendered by `mkdocstrings`.

## Writing style

- **Explain before showing.** Context and intent before code examples.
- **Show only the relevant part.** Strip scaffolding from examples.
- **Write from the user's perspective.** What the developer can do, not how internals work.
- **Mark optional parameters explicitly.**

## Keeping docs in sync

When modifying a feature, update affected doc pages in the same change, remove sections that describe behavior that was removed or changed, and link to authoritative external sources rather than copying content that will go stale.
