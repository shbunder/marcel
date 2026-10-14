# AvatarKit — each agent's pixel avatar

Plays a sprite sheet (B-28): the bundled default, Marcel's own Pixel Zoo look, or an agent's own sheet from the
Pixel Zoo (B-44): `AvatarView(state:size:sheet:)`.

- **Each agent's own sheet** (`AvatarSheets`, S-19.3). `refresh(agentId:etag:)` asks the hub through the `fetch` the
  app passes (`GET /agents/{id}/avatar`, JSON and PNG, with `If-None-Match`; nil means 304) and keeps the sheet in
  `Library/Caches/avatars/<agent id>.{json,png,etag}`, read back at start so it shows at once and offline. An agent
  whose sheet the phone never had, or a fetch that fails, plays the bundled giraffe; a failed fetch never drops a
  sheet the phone has. An `agent.updated` whose ETag matches the kept one fetches nothing.
  `SpriteSheet(json:png:)` reads a sheet from bytes. A new sheet restarts `AvatarView`'s player on the current state.

- **VoiceOver** says the state with the agent's name: `accessibilityDescription(name:)` ("Gigi is thinking");
  `AvatarView(…, name:)` uses it.
- **States → loops.** `AvatarState` mirrors the hub's `AgentState` (`AvatarState(kind:activeTasks:)`):
  `idle`, `thinking`, `working(n)` → `working1`…`working3` (n clamps to 1…3), `needs_you`, `done`.
  An unknown kind shows `idle`.
- **Timing.** `AvatarPlayer` picks the frame for a moment in time. A state change waits for the current frame
  to end (the loop's next frame boundary), then the new loop starts on its first frame — never mid-blink.
- **Sharp pixels.** `AvatarSize` is 24, 32, 44 or 120 pt (a square box). The sprite is drawn at the largest
  whole number of screen pixels per sprite pixel that fits (at least 1), nearest-neighbour. On a 3× screen
  that is 2× at 24 pt, 3× at 32 pt, 4× at 44 pt and 11× at 120 pt for a 32 × 32 Pixel Zoo frame.
  `AvatarView(state:box:)` takes a box of any size: the chat header shrinks from 120 to 44 pt and the sprite steps
  down whole zooms on the way (S-13.3).
- **Quiet when unseen.** The `TimelineView` pauses off screen and in the background. With Reduce Motion the
  avatar shows each state's first frame and does not move.

## The sheet

`Sources/AvatarKit/Resources/giraffe-rose-professional.{png,json}` is Marcel's look (giraffe, rose, professional: the
hub's `OWNER_LOOK`) as the Pixel Zoo (`shared/avatar/zoo`) draws it, at the zoo's native 32 × 32: the same low-res art
as the maker page and every agent's own sheet. It is what `SpriteSheet.giraffe` plays until an agent's own sheet
arrives, and offline. Regenerate it with `make -C ios/AvatarKit sheet` after the zoo changes;
`shared/avatar/zoo/tests/test_app_sheet.py` fails while the bundled copy differs from what the zoo draws. (It replaced
the first, 48 × 54 giraffe from `shared/avatar/pixel`, retired on 2026-10-14.)

## Tests

Run with `make ios-check` (in the simulator, like MarcelKit). `SnapshotTests` renders each state's first frame at
every size at 3× and compares it with `Tests/AvatarKitTests/__Snapshots__/`; it also checks every rendered pixel
is exactly one sprite pixel (no blur). A missing reference is written and the test fails, so it gets looked at.
Re-record all with `SIMCTL_CHILD_AVATARKIT_RECORD=1 make -C ios test`.
`AvatarSheetsTests` covers the per-agent sheets with a fox the Pixel Zoo drew (`Tests/AvatarKitTests/Fixtures/`,
`python -m zoo sheet --animal fox --color tangerine --personality cheerful`): every state's loop, the giraffe until it
arrives and offline, kept for next launch, the ETag sent and an unchanged one not fetched, an id that is not a plain
file name (letters, digits, `_`, `-`) never written.
