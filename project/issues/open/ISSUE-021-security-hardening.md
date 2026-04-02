# ISSUE-021: Security Hardening & Setup Improvements

**Status:** Open
**Created:** 2026-04-02
**Assignee:** Claude
**Priority:** High
**Labels:** security, feature, docs

## Capture
**Original request:** "Security hardening and setup improvements from codebase review: add WebSocket auth, mandatory webhook secret, user_slug validation, credential encryption, configurable default user, SETUP.md guide, file permissions"

**Follow-up Q&A:**
- User confirmed item 1 (rotate leaked token) is already done
- User declined reducing Docker home mount (Marcel needs write access to home by design)
- User deferred CORS/rate limiting to a future issue (asked for a TODO)

**Resolved intent:** Harden Marcel's security posture so it's safe to run as a family server. This covers authentication on the WebSocket API, mandatory Telegram webhook secret, input validation on user slugs, encryption of credentials at rest, making the default user configurable, writing a non-technical setup guide, and setting restrictive file permissions on sensitive files.

## Description
A codebase review identified several security gaps that make Marcel unsafe for multi-user family deployments. The most critical issues are: no authentication on the WebSocket API (anyone on the network can impersonate any user), optional Telegram webhook secret (allowing message injection), no input validation on user slugs (path traversal risk), plaintext credential storage, hardcoded default user, and overly permissive file permissions. This issue addresses all of these plus adds a SETUP.md for non-technical users.

## Tasks
- [ ] Add bearer token authentication to the WebSocket API endpoint
- [ ] Make Telegram webhook secret mandatory (fail startup if not set)
- [ ] Validate user_slug against `[a-z0-9_-]` pattern, reject invalid values
- [ ] Encrypt credentials at rest using Fernet (key derived from master passphrase or env var)
- [ ] Make default user configurable via env var / config instead of hardcoded 'shaun'
- [ ] Write SETUP.md with step-by-step checklist for non-technical users
- [ ] Set file permissions to 0o600 on credential and conversation files
- [ ] Add TODO for CORS middleware and rate limiting (future issue)

## Relationships
- Related to: [[ISSUE-020-dockerize]]

## Comments

## Implementation Log
