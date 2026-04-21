# ISSUE-43ed69: Drop dead `Settings.marcel_env` field — `flags._env()` is the only live reader

**Status:** Closed
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

- [✓] Delete the `marcel_env` field + comment block from
  [src/marcel_core/config.py](../../src/marcel_core/config.py) (lines 30-37
  today). pydantic-settings `extra='ignore'` means a stray `MARCEL_ENV` in
  `.env` stays harmless — the setting just isn't bound anywhere. Also dropped
  the now-unused `typing.Literal` import.
- [✓] Drop the phrase "Validated against the same ``{dev, prod}`` set as
  ``Settings.marcel_env``" from
  [src/marcel_core/watchdog/flags.py](../../src/marcel_core/watchdog/flags.py)
  `_env()`'s docstring.
- [✓] Add a "# Why not `settings.marcel_env`?" comment above `_env()`
  capturing the three call-time / safety-default / no-import-cycle reasons.
- [✓] Repo-wide grep for any other reference to `Settings.marcel_env` or
  `settings.marcel_env` — clean, only the two comment references that
  document the intentional absence.
- [✓] `make check` green at 91.30%, 1344 tests pass (no new tests — this is a
  pure removal with no behavior change).

## Relationships

- Follow-up to [[ISSUE-6b02d0]] (the issue that introduced the now-dead field).
- Related: [[ISSUE-5ca6dc]] (same ISSUE-6b02d0 code-review sweep that caught
  this one).

## Implementation Log

### 2026-04-21 — dead-field removal + single-source-of-truth comment

- **`src/marcel_core/config.py`**: deleted the seven-line "Runtime environment" comment block + `marcel_env: Literal['dev', 'prod'] = 'prod'` field. Replaced with a three-line note pointing readers to `watchdog.flags._env()` as the single live source of truth. Also dropped the now-unused `from typing import Literal` import.
- **`src/marcel_core/watchdog/flags.py`**: dropped the dangling "same as `Settings.marcel_env`" phrase from `_env()`'s docstring. Added a twelve-line "Why not `settings.marcel_env`?" comment above `_env()` capturing the three reasons for reading `os.environ` directly — call-time semantics (tests monkeypatch per-test), safety default on garbage input (fall back to `prod` vs. pydantic's boot-time `ValidationError`), and no import cycle (flags.py sits below config.py in the dep graph).
- **Restricted-path unlock**: `src/marcel_core/config.py` is guarded by `.claude/hooks/guard-restricted.py` as "core config — self-modification safety rule." Unlocked via `touch .claude/.unlock-safety` with explicit user permission, made the edits, then `rm .claude/.unlock-safety` before committing. Issue explicitly capture the user-granted scope: remove the dead field only, no behavior changes.
- **Verification**: repo-wide grep for `marcel_env` / `settings.marcel_env` / `Settings.marcel_env` returns only the two comment references that document the intentional absence. No test references. `make check` green: 1344 pass (same count as Issue A close), 91.30% coverage (same).

**Reflection** (via pre-close-verifier):
- Verdict: APPROVE
- Coverage: 5/5 tasks addressed
- Shortcuts found: none
- Scope drift: none — diff touches exactly `config.py`, `flags.py`, and the issue-file rename
- Stragglers: none — repo-wide grep clean; only references are the two intentional comment anchors and the append-only closed-issue audit trail
- Restricted-path unlock hygiene: `.claude/.unlock-safety` removed before commit, not in diff

## Lessons Learned

### What worked well

- **Empirical verification of a "finding" before filing the issue.** Issue B (the MARCEL_DEV_PORT export claim) turned out to be a false positive — `make -p` plus a faked docker binary proved `.EXPORT_ALL_VARIABLES:` was doing its job. Ten minutes spent reproducing saved the hour-plus of filing + implementing + verifying an issue that wouldn't have changed runtime behavior. The reviewer's report is a hypothesis, not a fact.
- **Restricted-path unlock with explicit user permission.** The guard-restricted hook caught the `config.py` edit and halted me. Asking the user before `touch .unlock-safety` (rather than assuming authorization from the broader "pick up all three" instruction) was correct — the safety file for `os.execv`-adjacent code deserves a per-change confirmation.
- **Explaining the negative space.** The "Why not `settings.marcel_env`?" comment is pure prevention-of-future-mistakes: it exists so that a future reader looking at the codebase doesn't re-introduce the dead field to "consolidate" two parallel sources of truth. Worth three lines of comment to save a future issue.

### What to do differently

- **Filing order matters.** Filing Issue B before verifying it cost nothing here (I never committed the issue file — I detected it was a false positive before `/new-issue`), but the rule generalizes: for findings that look like trivial one-line fixes, run a five-minute reproduction before filing. Code-review hypotheses are cheap to state and expensive to implement.

### Patterns to reuse

- **Faked-binary subprocess verification.** `PATH=/tmp/fake-bin:$PATH make <target>` with a small shell script standing in for `docker` / `psql` / `git` is a powerful way to confirm environment-variable propagation without a full runtime. Reusable whenever a code-review claim takes the shape "X doesn't actually reach Y."
- **Comments that document the negative space.** When removing a field that a future reader might plausibly re-add (because the name is obvious, because a nearby field suggests the pattern, because it's a typed-config convention), leave a pointer comment explaining the intentional absence. The comment isn't documentation — it's a landmine for a future "let me clean this up" reflex.
