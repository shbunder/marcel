---
id: ADR-007
title: A plain chat look replaces the pixel-classic dialogue boxes
status: Accepted
date: 2026-10-15
decided_by: owner
evidence: Owner, 2026-10-15 (chat, wireframe review); project/stories/S-18.3.md, S-18.4.md
---

# ADR-007 — A plain chat look replaces the pixel-classic dialogue boxes

## Context

B-29a made the whole app a pixel-classic game UI: dialogue boxes, ▶ YES / NOT NOW choice prompts and a "YOU GOT"
banner. Looking at the wireframes, the owner found the chat cluttered: it was hard to see what is what.

## Options

| | Option | Verdict |
|---|---|---|
| A | Keep the game UI, tidy it | Still reads as a game, not a chat |
| **B** | **Familiar chat bubbles; colour frames only for special messages; the same cards in every tab; pixel art only in the avatar and small labels** | **Chosen** (wireframes: explainer artifact KtdD3PMLeyZP7QrwqYLrdv) |

## Decision

Option B (B-29a, B-43). Marcel left in white/grey, the owner right in the helper's colour, the avatar beside the
bubble with no circle, special messages framed on all four sides (never a single side stripe).

## Consequences

- `PixelKit`'s `DialogBox`, `ChoicePrompt` and `ItemGetBanner` are replaced (S-18.3, S-18.4). The pixel fonts stay
  for labels.
