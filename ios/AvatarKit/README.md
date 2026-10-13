# AvatarKit — Marcel's pixel giraffe

Plays the sprite sheet from `shared/avatar/pixel/out/` (B-28): `AvatarView(state:size:)`.

- **VoiceOver** says the state with the agent's name: `accessibilityDescription(name:)` ("Gigi is thinking");
  `AvatarView(…, name:)` uses it.
- **States → loops.** `AvatarState` mirrors the hub's `AgentState` (`AvatarState(kind:activeTasks:)`):
  `idle`, `thinking`, `working(n)` → `working1`…`working3` (n clamps to 1…3), `needs_you`, `done`.
  An unknown kind shows `idle`.
- **Timing.** `AvatarPlayer` picks the frame for a moment in time. A state change waits for the current frame
  to end (the loop's next frame boundary), then the new loop starts on its first frame — never mid-blink.
- **Sharp pixels.** `AvatarSize` is 24, 32, 44 or 120 pt (a square box). The sprite is drawn at the largest
  whole number of screen pixels per sprite pixel that fits (at least 1), nearest-neighbour. On a 3× screen
  that is 1× at 24 and 32 pt, 2× at 44 pt, 6× at 120 pt.
  `AvatarView(state:box:)` takes a box of any size: the chat header shrinks from 120 to 44 pt and the sprite steps
  down whole zooms on the way (S-13.3).
- **Quiet when unseen.** The `TimelineView` pauses off screen and in the background. With Reduce Motion the
  avatar shows each state's first frame and does not move.

## The sheet

`Sources/AvatarKit/Resources/giraffe.{png,json}` are copies. After the giraffe changes in
`shared/avatar/pixel/`, run `make -C ios/AvatarKit sheet` and commit the copies; `SheetResourceTests` fails
while they differ.

## Tests

Run with `make ios-check` (in the simulator, like MarcelKit). `SnapshotTests` renders each state's first frame at
every size at 3× and compares it with `Tests/AvatarKitTests/__Snapshots__/`; it also checks every rendered pixel
is exactly one sprite pixel (no blur). A missing reference is written and the test fails, so it gets looked at.
Re-record all with `SIMCTL_CHILD_AVATARKIT_RECORD=1 make -C ios test`.
