# Versioning

Marcel uses **Pride Versioning** — `PROUD.DEFAULT.SHAME`.

> Given a version number `PROUD.DEFAULT.SHAME`, increment the:
>
> - **PROUD** version when you make changes you are really proud of
> - **DEFAULT** version when you make a release that's okay
> - **SHAME** version when you are fixing things too embarrassing to admit

## When to bump what

| Segment | When to bump | Examples |
|---------|-------------|---------|
| **PROUD** | A landmark feature, architectural leap, or something you'd genuinely show off. Resets DEFAULT and SHAME to 0. | Self-modification pipeline, first working voice interface, full household integration |
| **DEFAULT** | A normal useful release — new feature, meaningful improvement, or any non-trivial change that ships. | CLI installer, new slash command, new integration, model selection |
| **SHAME** | A fix for something embarrassing — a bug that shouldn't have shipped, a typo in a prompt, a broken default. | Wrong port default, broken header rendering, off-by-one in conversation index |

**Rule: every commit that changes behaviour must bump a version segment.** Pure refactors, test additions, and doc-only changes are the only exceptions.

When in doubt: if you would tell someone about the change, it's DEFAULT. If you'd rather not mention it, it's SHAME.

## Where versions live

There are two version strings in the codebase — one per deployable unit:

| File | What it versions |
|------|-----------------|
| `src/marcel_cli/Cargo.toml` | The CLI (`marcel` binary) |
| `src/marcel_core/__init__.py` | The backend server |

`pyproject.toml` carries the package version and should match `marcel_core.__version__` (the server is the heart of the project).

## How to bump

1. Decide which segment to increment based on the table above.
2. Update **both** version strings if the change touches both CLI and backend. Update only the relevant one if the change is isolated.
3. Update `pyproject.toml` to match `marcel_core.__version__` when the backend version changes.
4. Include the version bump in the same commit as the change — not in a separate cleanup commit.

## Examples

| Change | Bump | `0.2.0` → |
|--------|------|-----------|
| Add voice interface | PROUD | `1.0.0` |
| Add calendar integration | DEFAULT | `0.3.0` |
| Fix crash on empty message | SHAME | `0.2.1` |
| Fix typo in `/help` text | SHAME | `0.2.1` |
| Refactor storage layer (no behaviour change) | none | `0.2.0` |
