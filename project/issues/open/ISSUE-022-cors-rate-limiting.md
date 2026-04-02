# ISSUE-022: Add CORS Middleware and Rate Limiting

**Status:** Open
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

## Tasks
- [ ] Add FastAPI CORSMiddleware with configurable origins via env var
- [ ] Add per-user rate limiting on WebSocket messages
- [ ] Add rate limiting on Telegram webhook endpoint

## Relationships
- Related to: [[ISSUE-021-security-hardening]]

## Comments

## Implementation Log
