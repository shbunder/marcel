# ISSUE-019: Remove iCloud credentials from .env.local

**Status:** WIP
**Created:** 2026-03-30
**Assignee:** Claude Code
**Priority:** Medium
**Labels:** cleanup, security, icloud

## Capture
**Original request:** "finish the icloud feature and make sure no credentials are in .env.local anymore"

**Resolved intent:** The iCloud client already reads credentials from `data/users/{slug}/credentials.env` (not `.env.local`), but `.env.local` still contains `ICLOUD_APPLE_ID` and `ICLOUD_APP_PASSWORD` as leftover entries. Remove them and ship the pending linting/formatting changes from ISSUE-015.

## Description

ISSUE-015 implemented the iCloud integration with credentials stored per-user in `data/users/shaun/credentials.env`. However, `.env.local` still holds the credentials as a duplicate. These should be removed — `.env.local` is for system-level config (Telegram bot token) not user-specific credentials.

Also ships pending formatting/linting fixes across iCloud, skills, telegram, and test files.

## Tasks
- [✓] Remove `ICLOUD_APPLE_ID` and `ICLOUD_APP_PASSWORD` from `.env.local`
- [✓] Commit pending linting/formatting fixes (icloud, skills, telegram, tests, CLI)
