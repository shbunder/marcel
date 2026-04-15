# Coding standards (Marcel-specific)

Most style rules are enforced by `ruff` and `mypy` — run `make check` and trust it. This file holds only rules Marcel wants that those tools don't check. If a rule here becomes redundant because a linter starts enforcing it, delete it.

## API design

- **Prefix internal helpers with `_`.** Prevents accidental public-API surface expansion. Linters won't catch this for you.
- **Make optional params keyword-only.** Use `*` to split positional (1–2 essentials) from keyword-only (everything else). Keeps call sites readable and prevents silent breakage when adding options.
- **Dedicated typed fields over generic dicts** for configuration knobs. Use `pydantic-settings` fields, not `extra_body`-style dicts.
- **Integration-agnostic terminology in public APIs.** Say "integration" and "credential", not "icloud" or "apple_id". Vendor names are fine inside the specific integration module, never in `integration(...)` callers.

## Type system

- **`assert_never()` in the `else` of exhaustive union handling.** Catches unhandled variants at type-check time.
- **`TypedDict` over `dict[str, Any]`** for anything with known keys.
- **`TYPE_CHECKING` imports for optional deps.** Keeps import cost out of runtime but preserves type checking.
- **Delete `# type: ignore` comments** the moment the underlying issue is fixed — they are TODOs that rot.

## Tests

- **Coverage minimum is 90%** — enforced by the pre-commit hook via `make check`.
- **`pragma: no cover` is reserved** for genuinely untestable branches (platform-specific paths, type-narrowing no-ops, defensive unreachable code). Never use it to paper over missing tests.
- **Bug fixes start with a failing test.** Before changing the production code, write a test that reproduces the bug and fails for the same reason the user hit it. Then fix the code until the test goes green. The test stays in the suite as the regression guard — that is how we know the bug won't come back. If the bug is hard to reproduce in a unit test, reproduce it at the nearest integration layer you can (harness, storage, channel) rather than skipping the step.

## Not here

Generic Python style (tuple vs `|` in `isinstance`, walrus operator, list comprehensions, import ordering, catching broad `Exception`) is ruff's job. If you catch yourself writing a rule that a linter enforces, add the rule to `pyproject.toml` instead of this file.
