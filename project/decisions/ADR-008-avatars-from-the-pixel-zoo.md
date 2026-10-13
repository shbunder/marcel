---
id: ADR-008
title: Avatars come from the Pixel Zoo generator
status: Accepted
date: 2026-10-13
decided_by: owner
evidence: Owner, 2026-10-13 and 2026-10-13 (chat); project/stories/S-19.1.md
---

# ADR-008 — Avatars come from the Pixel Zoo generator

## Context

v1 bundles one hand-tuned 48×54 giraffe sheet. With a helper per person, every helper needs its own look. The SP5
"Pixel Zoo" spike (`scratch/spikes/SP5-avatar/zoo/zoo.js`) draws 11 animals × 16 colours × 9 personalities × 25 moods
on a 32×32 grid, including the laptop for "working", and picks a stable look from a name. The owner liked it.

## Options

| | Option | Verdict |
|---|---|---|
| **A** | **Port the generator to Python in `shared/avatar/zoo`; the hub builds each agent's sheet; the app plays it** | **Chosen.** One source; AvatarKit already plays any sheet that fits the contract |
| B | Port it to Swift and draw on the phone | Two copies of the art rules to keep in step |
| C | Pre-build every combination | Thousands of sheets in the app bundle |

## Decision

Option A (B-44). The agent's `look` is data; the hub serves the sheet and previews; the bundled giraffe stays as the
offline fallback and the app icon.

## Consequences

- `avatar-sheet.schema.json` allows 32×32 frames. The Python port must match `zoo.js` pixel for pixel on committed fixtures.
