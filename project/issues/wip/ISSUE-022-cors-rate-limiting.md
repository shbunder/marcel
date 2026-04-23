# ISSUE-022: Add CORS Middleware and Rate Limiting

**Status:** WIP
**Created:** 2026-04-02
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** security, feature

## Capture
**Original request:** From ISSUE-021 security review — user asked to defer CORS and rate limiting to a future issue.

**Resolved intent:** Add explicit CORS configuration with an allowlist and per-user rate limiting to the WebSocket and REST endpoints to prevent cross-origin abuse and resource exhaustion.

## Description
Currently Marcel relies on FastAPI defaults for CORS (which are restrictive but implicit) and has no rate limiting. For a family server exposed on a local network or via tunnel, both should be explicit:

1. **CORS middleware** — add `CORSMiddleware` with a configurable `allow_origins` list (default: localhost only).
2. **Rate limiting** — limit WebSocket messages per user per second (e.g. 5 msg/s) and REST endpoint calls to prevent accidental or malicious resource exhaustion.

## Implementation Approach

### Scope revision — 1 / 3 tasks already shipped; 1 / 3 deferred to a follow-up

Task 1 (CORS env-configurable) has **already shipped** in an earlier change:

- `src/marcel_core/main.py:166-169` mounts `CORSMiddleware(allow_origins=settings.cors_origins)`.
- `src/marcel_core/config.py:36` declares `marcel_cors_origins: str = 'http://localhost:5173'` (env-configurable via `MARCEL_CORS_ORIGINS`).
- `src/marcel_core/config.py:168-169` parses it into a list.

The issue's task 1 is verifiable, not implementable — the change was already on `main` by the time this issue was worked. Task marked complete during the first `🔧 impl:` commit.

Task 3 (Telegram webhook rate limiting) lives in the **marcel-zoo** repo (`<zoo>/channels/telegram/webhook.py`) post-ISSUE-d7eeb1's migrations. Cross-repo work needs its own issue with a zoo-side Implementation Approach + grep gate + zoo-side tests. Deferred to a follow-up issue to keep this one bounded. Captured in Lessons Learned.

This leaves **task 2 (WebSocket rate limiting)** as the real work for this issue. Single-repo, testable, completable.

### Design — token bucket, per-user, in-memory

- `src/marcel_core/rate_limit.py` — new. Standard token-bucket algorithm: each key (user_slug) has `tokens: float` + `last_refill: float`; `allow(key)` refills based on elapsed wall-clock time, caps at `burst`, deducts 1 token if available, returns `True`/`False`.
- `src/marcel_core/config.py` — two new env-configurable settings:
  - `marcel_ws_rate_limit_per_second: float = 5.0` (refill rate).
  - `marcel_ws_rate_limit_burst: int = 10` (bucket capacity; absorbs short spikes while still rate-limiting sustained abuse).
- `src/marcel_core/api/chat.py` — after the message is authenticated and `user_slug` is resolved, call the module-level bucket's `allow(user_slug)`. On reject: `adapter.send_error('Rate limit exceeded — slow down and try again')`, `continue` the loop (do not close the WS; a runaway tab shouldn't lose the user's session).

### Existing code to reuse

- `settings` singleton — `src/marcel_core/config.py:36` — pattern for env-configurable settings already established (e.g. `marcel_cors_origins`). New fields follow the same `marcel_*` naming convention.
- `WebSocketAdapter.send_error` — `src/marcel_core/channels/websocket.py` — already used elsewhere in `chat.py` for auth failures; reuse for rate-limit notification.
- `time.monotonic()` — standard lib; preferred over `time.time()` for token-bucket refill because it's unaffected by wall-clock adjustments.

### Why in-memory, not Redis

Marcel is a single-container family assistant. An in-memory dict is sufficient:
- Rate limit resets on restart — acceptable for a DoS-prevention measure (attacker would need to sustain the attack across restarts *and* survive the watchdog rollback).
- No external dependency (Redis) to install, version-pin, or configure.
- Per-container state means multi-replica deployments would lose sync, but Marcel doesn't run multi-replica.

### Files to modify

- `src/marcel_core/rate_limit.py` — new module, `TokenBucket` class + module-level `ws_bucket` instance.
- `src/marcel_core/config.py` — two new `marcel_ws_rate_limit_*` settings.
- `src/marcel_core/api/chat.py` — per-message `bucket.allow(user_slug)` check after auth.
- `tests/core/test_rate_limit.py` — new. Covers: allow under burst, reject past burst, refill over time, per-key isolation, default config honours settings.

### Verification steps

- `uv run pytest tests/core/test_rate_limit.py -v` — all green.
- Manual smoke: `make serve` + `wscat` 15 rapid messages as one user → first 10 accepted (burst), subsequent messages return `{"type":"error", "message":"Rate limit exceeded..."}`. After ~2 s of idle, new messages accepted again (refill).
- `make check` — green at ≥90 % coverage.

### Non-scope

- **Telegram webhook rate limiting (original task 3)** — deferred to a follow-up issue; the webhook lives in marcel-zoo after ISSUE-d7eeb1.
- **REST endpoint rate limiting** — mentioned in the issue body (`/telegram/webhook`) but the REST surface (chat, artifacts, components, conversations) serves the frontend and internal tooling only; no untrusted public REST endpoints exist today. If that changes, open a new issue.
- **Distributed rate limiting / Redis** — single-container deployment makes this unnecessary.
- **429 HTTP status** — the WebSocket can't return an HTTP status mid-stream; an in-message error frame + `continue` is the correct shape.

## Tasks

- [✓] Add FastAPI CORSMiddleware with configurable origins via env var (already shipped in `main.py:166` + `config.py:36`)
- [ ] Add per-user rate limiting on WebSocket messages
- [ ] Deferred: rate limiting on Telegram webhook endpoint (cross-repo, needs its own follow-up issue)
- [ ] `make check` green
- [ ] `/finish-issue` → merged close commit on main

## Relationships
- Related to: [[ISSUE-021-security-hardening]]

## Comments

## Implementation Log
<!-- issue-task:log-append -->

## Lessons Learned

### What worked well
-

### What to do differently
-

### Patterns to reuse
-
