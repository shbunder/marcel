# Coding Standards

These guidelines apply whenever working on Marcel's codebase in coder mode. See [CLAUDE.md](./CLAUDE.md) for the full development procedure.

## Code Style

- Extract helpers at 2-3+ call sites, inline single-use helpers unless they reduce significant complexity
- Remove comments that restate the code — explain WHY, not WHAT
- Simplify nested conditionals: use `and`/`or` for compound conditions, `elif` for mutual exclusion
- Extract shared logic into helper methods instead of duplicating inline — especially between method variants like sync/async or streaming/non-streaming
- Remove unreachable code branches — let impossible cases fail explicitly rather than silently handle them
- Use tuple syntax for `isinstance()` checks, not `|` union — tuples are faster at runtime
- Prefer list comprehensions over empty list + loop append
- Use `set` for unique collections; convert to `list` only at API boundaries
- Eliminate duplicate validation logic — extract repeated checks into shared helpers
- Use walrus operator (`:=`) to combine assignment with conditional checks
- Use `else` instead of `elif` when remaining cases are exhaustively covered
- Use `any()` with generator expressions instead of `for` loops with `break` for existence checks

## API Design

- Prefix internal helpers with `_` — prevents accidental public API surface expansion
- Use `*` to make optional params keyword-only — keeps 1-2 essential args positional, rest keyword-only
- Use dedicated typed fields for settings, not generic dicts like `extra_body`
- Don't pass data separately if it's already in a context object — reduces redundancy and prevents sync issues
- Use integration-agnostic terminology in public APIs and messages — reserve vendor-specific terms only for direct API interactions

## Type System

- Use `assert_never()` in `else` clause when handling union types — catches unhandled variants at type-check time
- Use `TypedDict` instead of `dict[str, Any]` for structured data with known fields
- Remove `# type: ignore` comments once underlying type issues are fixed
- Use `TYPE_CHECKING` imports for optional deps instead of `Any` — preserves type safety without runtime import errors

## Error Handling

- Raise explicit errors for unsupported inputs/parameters — prevents silent failures and makes contract violations obvious
- Catch specific exception types, not broad `Exception` — identifies actual failure modes and prevents masking unexpected errors

## General

- Place imports at module level; use inline imports only for circular dependencies or optional deps wrapped in `try`/`except ImportError` with install instructions
- Write tests for reachable code; reserve `pragma: no cover` only for untestable branches (platform-specific, type-constrained, defensive unreachable)
- Omit redundant context from names when clear from class/module/types/call site — `Service._connect()` is clearer than `Service._connect_service()`
