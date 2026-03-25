# Marcel Developer Guide

This file governs coder mode — when Marcel is being extended, debugged, or rewriting its own code. For a definition of when coder mode applies, see [CLAUDE.md](../CLAUDE.md#2-coder--self-rewriting-agent).

The **how** is as important as the **what**. A working feature that breaks the architecture or makes the next change harder is not a good outcome. Take the time to do it right.

## Philosophy

Core principles are defined in [CLAUDE.md](../CLAUDE.md#core-principles). All development work must follow them.

## Standards

Detailed coding guidelines — style, API design, type system, and error handling — are in [CODING_STANDARDS.md](./CODING_STANDARDS.md).

## Project Management

Issues and feature requests are tracked as markdown files under `./project/issues/`. See [./issues/CLAUDE.md](./issues/CLAUDE.md) for the full conventions: file naming, lifecycle (open → wip → closed), git commit format, linking, and implementation logging.

When a user requests a new feature or reports a bug, create an issue in `./project/issues/open/` before starting implementation. This keeps work discoverable and the project history readable.

## Feature Development Procedure

Every feature or bug fix follows this procedure. Some steps are skippable for small changes — see the note at each step.

**A small change** is one that: touches at most one existing file, introduces no new public interface, and can be described in one sentence. If in doubt, treat it as substantial.

## Step 1 — Capture

Record the original request verbatim, all follow-up questions asked, and the user's answers. End with a one-paragraph **resolved intent**: what the feature actually is, in your own words.

Record these in the issue file you'll create in Step 3. Hold them in working memory until then — the important thing is nothing is lost before it's written down.

> Always do this. Even for small requests, the resolved intent prevents silent misunderstandings.

## Step 2 — Requirements

Derive clear, testable requirements from the capture. Each requirement should state an observable behavior, not an implementation detail. This is what "done" looks like.

Before continuing:
- Read existing related code to understand current patterns
- Check whether a similar skill or integration already exists
- Identify where the change belongs (skill, integration, config, core)

If the request is vague or conflicts with existing architecture, ask rather than guess. A wrong implementation is worse than a delayed one.

> Always do this.

## Step 3 — Create an issue

Create an issue in `./project/issues/open/` per the conventions in [./issues/CLAUDE.md](./issues/CLAUDE.md). Move it to `wip/` when work begins.

> Always do this for anything beyond a small change.

## Step 4 — Design *(skip for small changes)*

For substantial features, sketch the approach before writing code: which files change, what the public interface looks like (`cmd()` signature, skill contract, config shape). Confirm with the user before proceeding.

> Skip when the change is confined to one file and the interface is obvious.

## Step 5 — Scaffold *(skip for small changes)*

Create the file structure and function/class signatures with no logic — just enough shape for tests to compile against.

> Skip when there is no new file structure or interface to define.

## Step 6 — Tests

Write tests derived from the requirements, not from the implementation. Tests go in `tests/` and should cover all reachable code paths.

Use `make test` to run them. They should fail at this point (red) — that's expected.

> For small changes: write tests alongside the implementation instead of before.

## Step 7 — Implement

Fill in the logic. Keep changes minimal and focused — do not refactor unrelated code while implementing a feature.

Run `make test` regularly. The goal is to go green.

## Step 8 — Ship

Run `make check` — this runs format, lint, typecheck, and tests with coverage. All must pass before the work is considered done.

```
make check
```

Update or add documentation in `docs/` per [docs/CLAUDE.md](../docs/CLAUDE.md). Documentation ships in the same change as the code.

Log implementation work in the issue file and close or update it per [./issues/CLAUDE.md](./issues/CLAUDE.md).

## Self-Modification Safety

When rewriting Marcel's own code:

- Commit before restarting the service — every change must be recoverable
- Confirm with the user before restarting unless they explicitly asked for an auto-restart
- Keep changes minimal and focused — don't refactor unrelated code while implementing a feature
- **Restricted files:** Auth logic, core config, and safety rules (including CLAUDE.md files) are off-limits. If a change touches one of these areas, confirm with the user before proceeding even if they did not explicitly request confirmation.

## Integration Pattern

New integrations follow this pattern:

1. A new **skill** that describes what the integration does and how to invoke it
2. A `cmd("some_string")` entry that maps a user-facing command to the skill. `cmd()` is Marcel's dispatch mechanism: when a user issues a command, the router matches the input to a registered `cmd()` entry and hands off to the corresponding skill.
3. A **JSON config entry** that configures the underlying request/behavior (endpoint, auth, params)

Integrations must be self-contained — they should not require changes to core Marcel code. When adding an integration, verify the pattern works end-to-end before committing.
