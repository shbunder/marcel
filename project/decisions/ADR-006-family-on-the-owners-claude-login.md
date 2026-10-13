---
id: ADR-006
title: Family members get their own helper on the owner's Claude login
status: Accepted
date: 2026-10-15
decided_by: owner
evidence: Owner, 2026-10-15 (chat); project/stories/S-17.1.md, S-17.2.md, SP8.md
---

# ADR-006 — Family members get their own helper on the owner's Claude login

## Context

The spec kept v1 to the owner alone. The owner now wants the family on Marcel, with the owner as admin. The NUC has
one Claude login (the owner's Max subscription), and the runner deliberately removes `CLAUDE_CONFIG_DIR` so no session
can switch accounts (`runner/src/marcel_runner/claude_env.py`).

## Options

| | Option | Verdict |
|---|---|---|
| **A** | **Everyone's helper runs on the owner's login now; per-person logins later** | **Chosen.** No runner change; ships with F17 |
| B | Each adult signs in with their own Claude account from day one | Needs several logins on one NUC (SP8) and runner changes first |
| C | One shared helper for the whole family | Mixes everyone's chats and memory; nobody asked for it |

## Decision

Option A. The owner is the admin (`role: owner`) and the account holder. Members get their own agent, chat, memory and
phones, isolated from each other, but their sessions run on the owner's login, share the usage guard and the worker
cap. SP8 checks whether separate config folders (`CLAUDE_CONFIG_DIR`) let several accounts be signed in at once; if
so, a later story adds an `account` to each spawn.

## Consequences

- The owner accepts that family use runs on a subscription meant for individual use, and that everyone shares one
  usage limit. This is revisited when SP8 passes.
- Isolation between people is a control: S-17.1 and S-17.2 carry tests that fail when the checks are removed.
- Family phones need the app installed: in practice TestFlight, so an Apple developer account (planned by the owner).
