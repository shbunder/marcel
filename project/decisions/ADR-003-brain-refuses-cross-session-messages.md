---
id: ADR-003
title: The brain refuses cross-session messages
status: Accepted
date: 2026-10-12
decided_by: S-06.1 implementer
evidence: scratch/spikes/SP1-bg-channel/RESULT.md (option C); Claude Code 2.1.290 settings schema; brain/tests/test_settings.py
---

# ADR-003 — The brain refuses cross-session messages

## Context

S-06.1 asks for `crossSessionInbound` "per SP1" in the brain's settings. Claude Code 2.1.290 accepts
`accept` (deliver `SendMessage` from your other sessions), `hold` (park them for review) or `refuse`.
Unset, a message is delivered when the sender's permission mode is in the same class as the receiver's,
so a worker in `auto` mode could message the brain (also `auto`) directly.

SP1 listed `crossSessionInbound: accept` only in fallback C (steering without channels). The owner chose
option A (ADR-001): everything that reaches the brain comes through the marcel channel, from the hub.

## Options

| | Option | Verdict |
|---|---|---|
| **A** | `refuse` | **Chosen.** The channel is the only way in; the hub sees and stores every message |
| B | `accept` | Needed only for fallback C, which was not chosen. Lets any session on the NUC (a worker reading untrusted web pages) steer the brain around the hub, and the owner never sees it in the chat |
| C | `hold` or unset | Parks messages for a human at a terminal the brain never has |

## Decision

Option A: `"crossSessionInbound": "refuse"` in `brain/.claude/settings.json`. Workers report through the
channel's `report` tool; the hub decides when the brain is woken (S-06.5).

## Consequences

- `brain/tests/test_settings.py` fails if the value changes.
- If the channel ever fails and fallback C is revived, this decision is revisited with it.
