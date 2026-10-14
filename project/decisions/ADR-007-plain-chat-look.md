---
id: ADR-007
title: The chat reads like the Claude app; pixel art is the accent
status: Accepted
date: 2026-10-13
decided_by: owner
evidence: Owner, 2026-10-13 (two wireframe reviews; approved the modern look on the explainer artifact KtdD3PMLeyZP7QrwqYLrdv v9: "I like the new design of the wireframes much more! go with this!"); project/stories/S-18.3.md–S-18.6.md, S-19.3.md
---

# ADR-007 — The chat reads like the Claude app; pixel art is the accent

## Context

B-29a made the whole app a pixel-classic game UI: dialogue boxes, ▶ YES / NOT NOW choice prompts and a "YOU GOT"
banner. Looking at the wireframes, the owner found the chat cluttered. A first revision (same day) used chat bubbles
on both sides; the owner then chose the "modern" look of the Claude iOS app instead.

## Options

| | Option | Verdict |
|---|---|---|
| A | Keep the game UI, tidy it | Still reads as a game, not a chat |
| B | Bubbles on both sides, avatar beside each of Marcel's bubbles | First revision; replaced: long answers in a bubble are cramped |
| **C** | **The Claude app's look: my messages in rounded bubbles on the right; Marcel's replies as plain formatted text across the full width; only special messages are boxes** | **Chosen** |

## Decision

Option C (B-29a, B-43), approved by the owner on 2026-10-13 after seeing it in the wireframes:

- **My messages:** rounded bubbles on the right, in the helper's colour, with a notch: the bottom-right corner
  squared off, so it is clear "I said that" (owner, 2026-10-13).
- **Marcel's replies:** Markdown (headings, lists, tables, code, links) as plain text across the full width. No
  bubble and no avatar per message.
- **The avatar lives in the chat header.** When more than one helper speaks in a chat (F19), a reply starts with a
  tiny avatar and the name, only when the speaker changes.
- **The four special messages** (started, needs you, done, failed) are framed cards inline in the text column, and
  the only boxes.
- **A quoted reply** (B-41) is one line above it: "↩ re: your 08:12 message", tap to jump.
- **Long-press anywhere on a reply** opens reply-in-thread (the side chat, B-08).
- **Pixel art is the accent, not the frame:** the header avatar, the card labels, and a small "thinking" animation
  while Marcel works.

Why:

- **Long answers read better** at full width, with real headings, lists and tables.
- **It matches the Claude app** the owner uses every day.
- **There is less to build:** the app's item viewer already turns Markdown into native blocks with Apple's parser
  (`ItemMarkdown.swift`), and no bubble layout is needed for Marcel.

## Consequences

- `ItemMarkdown` moves to a shared place (PixelKit) and renders emphasis in the body font. The chat's flattening
  `ChatMarkdown` is retired (S-18.3).
- `PixelKit`'s `DialogBox`, `ChoicePrompt` and `ItemGetBanner` are replaced (S-18.3, S-18.4, S-18.6). The pixel fonts
  stay for labels.
- The per-message chat avatar is gone; AvatarKit plays in the header (S-19.3; the thinking row went in the 2026-10-14
  amendment).

## Amendment (2026-10-14) — the header shows what Marcel is doing

Decided by the owner on 2026-10-14, after three header variants and three blur strengths (their reference: Dot's
chat, a big centred avatar with a name pill). It replaces wireframe Q1's 44 pt header strip and the thinking row.

- **The header:** the helper big and centred (96 pt, an exact 9× of the 32-pixel sprite on a 3× screen), its name
  under it on a small white pill, and what it is doing in a pixel speech bubble beside its head ("Thinking…",
  "Reading…", "Waiting for you"). No bubble while it is free. A round gold count at the top left says how many things
  need you.
- **The pose follows what Marcel does,** refined from `agent.state` by the last tool call and recent problems
  (`MarcelAction`), each pose held at least 2 s. VoiceOver reads "Marcel, working on: reading and searching".
- **Moods for what happens** (`MoodBook`, the owner's table): a picked mood for needs you, done and a problem; one
  pass of a mood for 3+ quests, your message, a thank-you, a long quest ending, a stopped quest, a denied approval
  and the app opening; calm moods now and then while free, and sleepy after ten quiet minutes. The personality
  weights the pick. With Reduce Motion only the state poses show. Every mood is a loop in the sheet (about 19 KB per
  helper); a sheet without them plays the state loops.
- **The "Marcel is working…" row is gone:** the bubble says it.
- **The header has no background:** the bow tiling (outlines only) runs whole behind it and up under the status
  bar. Messages blur and fade as they scroll under the helper (the owner picked "soft": blur 3 pt, about a third left
  at the very top). With Reduce Transparency they only fade.
- **Zoo art:** the thinking pose lost its thought dots, and every mark beside the head ("!", "?", hearts, "zzz")
  moved to the left, away from the bubble.
