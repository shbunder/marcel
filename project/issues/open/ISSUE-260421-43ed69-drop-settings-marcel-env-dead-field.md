# ISSUE-43ed69: Drop dead `Settings.marcel_env` field — `flags._env()` is the only live reader

**Status:** Open
**Created:** 2026-04-21
**Assignee:** Unassigned
**Priority:** Low
**Labels:** cleanup, config, self-modification

## Capture

**Original request (code-review finding on ISSUE-6b02d0):**

> `Settings.marcel_env` was added in ISSUE-6b02d0 as the typed entry point for
> `MARCEL_ENV`, but nothing reads it. The only live reader is
> `watchdog/flags._env()`, which reads `os.environ['MARCEL_ENV']` directly and
> applies its own `{dev, prod}` whitelist. That's two parallel sources of truth
> for the same environment variable — one of which is decorative — and a
> maintenance trap: if someone adds a new reader pointing at `settings.marcel_env`,
> they'll get a value that might disagree with what `flags._env()` returns
> (different defaulting, different time-of-read semantics).

**Resolved intent:** Remove the unused `Settings.marcel_env` field from
`src/marcel_core/config.py`. Keep `flags._env()` as the single source of truth
— it correctly reads `MARCEL_ENV` at call time with the right `{dev, prod}`
whitelist and the "invalid falls back to prod" safety default. Add a brief
comment in `flags.py` explaining *why* this function reads `os.environ`
directly instead of going through `settings` (settings reads are cached at
startup; `MARCEL_ENV` must be read at call time because the watchdog may
consult it after a restart, and tests override it per-test). Scope is minimal:
one field removed, one docstring phrase dropped, one clarifying comment added.

## Description

### The duplication

[src/marcel_core/config.py:37](../../src/marcel_core/config.py#L37) declares:

```python
marcel_env: Literal['dev', 'prod'] = 'prod'
```

A repo-wide grep for `settings.marcel_env` or `\.marcel_env` returns zero hits
outside `config.py` itself and one docstring reference in `flags.py:58` that
reads "Validated against the same ``{dev, prod}`` set as ``Settings.marcel_env``."
Nothing actually *reads* the field.

[src/marcel_core/watchdog/flags.py:55-64](../../src/marcel_core/watchdog/flags.py#L55-L64) is the live reader:

```python
def _env() -> str:
    val = os.environ.get('MARCEL_ENV', 'prod')
    return val if val in ('dev', 'prod') else 'prod'
```

### Why `flags._env()` reads os.environ directly (not going through settings)

This is deliberate and the fix must preserve it:

1. **Call-time semantics.** `settings` is a module-level singleton initialized
   once when `marcel_core.config` is imported. Some tests override
   `MARCEL_ENV` per-test via `monkeypatch.setenv` and expect the next
   `request_restart()` or `read_restart_result()` call to reflect that change.
   A cached `settings.marcel_env` would fail those tests.
2. **Safety default on garbage input.** `flags._env()` falls back to `'prod'`
   on any non-`{dev, prod}` value. pydantic-settings would raise
   `ValidationError` on boot, which is worse behavior for a compose file with
   a typo — the process wouldn't even start.
3. **No import-cycle risk.** `watchdog/flags.py` sits below `config.py` in
   the dep graph; reading `os.environ` keeps it that way.

### The fix

1. Delete the `marcel_env` field + its three-line comment from `Settings`.
2. Drop the "same as `Settings.marcel_env`" phrase from `flags._env()`'s
   docstring — there's nothing to reference anymore.
3. Add a `# Why not settings.marcel_env?` comment above `_env()` covering the
   three reasons above, so the next reader doesn't try to "consolidate" them
   back together.

### What this does not change

- **Runtime behavior.** `MARCEL_ENV` is still read by `flags._env()` exactly as
  before. `docker-compose.yml` and `docker-compose.dev.yml` still set
  `MARCEL_ENV=prod` / `MARCEL_ENV=dev` via the `environment:` block.
- **Tests.** No test currently asserts `settings.marcel_env`. `make check`
  should remain green at the same numbers.
- **Docs.** `docs/self-modification.md`'s "Environment variables" table lists
  `MARCEL_ENV`; that entry stays — it describes the env var, not the settings
  field.

## Tasks

- [ ] Delete the `marcel_env` field + comment block from
  [src/marcel_core/config.py](../../src/marcel_core/config.py) (lines 30-37
  today). pydantic-settings `extra='ignore'` means a stray `MARCEL_ENV` in
  `.env` stays harmless — the setting just isn't bound anywhere.
- [ ] Drop the phrase "Validated against the same ``{dev, prod}`` set as
  ``Settings.marcel_env``" from
  [src/marcel_core/watchdog/flags.py](../../src/marcel_core/watchdog/flags.py)
  `_env()`'s docstring.
- [ ] Add a "# Why not `settings.marcel_env`?" comment above `_env()`
  capturing the three call-time / safety-default / no-import-cycle reasons.
- [ ] Repo-wide grep for any other reference to `Settings.marcel_env` or
  `settings.marcel_env` — expected zero hits outside the removals above.
- [ ] `make check` green.

## Relationships

- Follow-up to [[ISSUE-6b02d0]] (the issue that introduced the now-dead field).
- Related: [[ISSUE-5ca6dc]] (same ISSUE-6b02d0 code-review sweep that caught
  this one).

## Implementation Log
<!-- Append entries here when performing development work on this issue -->

## Lessons Learned
<!-- Filled in at close time. -->

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
