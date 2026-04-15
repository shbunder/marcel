---
paths:
  - "src/marcel_core/storage/**/*.py"
  - "src/marcel_core/auth/**/*.py"
  - "src/marcel_core/config.py"
  - "src/marcel_core/memory/**/*.py"
  - "src/marcel_core/channels/**/*.py"
---

# Rule — data boundaries

Marcel's state has two completely separate homes.

| What | Where | Shared across users? |
|---|---|---|
| System config (API keys, model IDs, feature flags, webhook secrets, encryption key) | `.env` + `.env.local` | Yes — one server, one config |
| User data (profile, credentials, memories, conversation history, skills config, jobs) | `~/.marcel/users/{slug}/` | No — one directory per user |

These never mix.

## Never

- **User preference in `.env`.** `ALICE_MORNING_DIGEST_ENABLED=true` is wrong. Preferences live in `profile.md` frontmatter under `~/.marcel/users/alice/`.
- **Secret in a user file.** Credentials do not live in `profile.md`, conversation history JSONL, memory files, or anything outside `~/.marcel/users/{slug}/credentials/`. Credentials in that directory are encrypted with `MARCEL_CREDENTIAL_ENC_KEY`.
- **Cross-user state at the top level.** A top-level file under `~/.marcel/` that holds shared household data (calendar, grocery list) is a design smell. Design an explicit per-user reference pattern — e.g., each user has a `calendar_links.md` pointing at the shared source — not a root-level pile.
- **Hardcoded `/home/shbunder/.marcel`.** Always resolve the data root via `settings.data_dir` (pydantic-settings) or a storage helper. Hardcoding breaks multi-user, breaks testing, and breaks deployment.

## Always

- **Resolve the data root** via `settings.data_dir` or `storage.user_path(slug)`.
- **Validate user slugs** before path concatenation. Never pass raw user input to `Path(...)` without rejecting `..`, absolute paths, and characters outside `[a-z0-9_-]`.
- **Encrypt credentials** before writing them to disk. Never serialize them to conversation history, log them, or echo them in an error message.
- **All config env vars** are declared once, typed, in [src/marcel_core/config.py](../../src/marcel_core/config.py). No `os.environ.get` scattered around.

## Why

Mixing the two surfaces breaks:

- **Backups.** `~/.marcel/users/` is backed up per-user; `.env` is backed up as system state. A user preference in `.env` means per-user restore is broken.
- **Privacy.** A user's memories must never leak to another user. A config leak is a different-magnitude incident than a credential leak — the threat models don't mix.
- **Multi-user.** Marcel serves an entire household. Conflating "a user" with "the env" makes two-user households impossible from day one.

## Enforcement

- [.claude/agents/security-auditor.md](../agents/security-auditor.md) treats any boundary violation as **High**, or **Critical** when a user's raw input reaches a path operation without validation.
- [.claude/agents/code-reviewer.md](../agents/code-reviewer.md) rejects hardcoded `/home/shbunder` paths on sight.
