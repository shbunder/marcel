# F12 — iOS app  (waves 1–2, all on the Mac)

Swift 6, SwiftUI, iOS 18+. Three local packages:

- `MarcelKit`: models, API client, WebSocket, SwiftData cache;
- `PixelKit`: the pixel-classic design system (S-12.2);
- `AvatarKit`: the sprite player (F13).

Everything is built against the **mock hub** (S-02.4) until wave 3. The screens follow the wireframes in
`scratch/spikes/SP5-avatar/wireframes/` (published copy linked from `plan/README.md`); the story numbers below
name the wireframe screens they implement (e.g. *wire 1B*).

Order inside the lane: S-12.1 → S-12.2 → (S-12.3, S-12.5, S-12.7 in parallel) → the rest.

### S-12.1  Project, MarcelKit, pairing                       runs-on: Mac · size: M
depends: S-02.1, S-02.4   touches: ios/
behaviours: B-27
do:
1. Create the Xcode project `Marcel` with the three packages.
2. Generate the API client from `app-api.yaml` with swift-openapi-generator.
3. Pairing screen: scan the QR → `POST /pair` → store the token in the Keychain.
4. WebSocket client with reconnect and `since` replay; a SwiftData cache of messages, tasks, projects and artifacts.
done when:
- [ ] `make ios-check` passes.
- [ ] XCTest covers pairing, WS replay after a simulated drop, and that the token is in the Keychain (not UserDefaults).

### S-12.2  PixelKit: the pixel-classic design system         runs-on: Mac · size: M
depends: S-12.1   touches: ios/PixelKit/
behaviours: B-29a
do:
1. Tokens: the palette (cream `#f6efe4`, ink `#2a1d24`, rose `#d87990`, deep `#9c3e5a`, teal `#3f6b6a`, gold
   `#e2a43a`), a light and a dark variant, spacing on a 2-pt pixel grid, hard 2–3 pt borders, offset "pixel" shadows.
2. Bundle the OFL fonts **Silkscreen** (labels, headings) and **Pixelify Sans** (body). Body text scales with
   Dynamic Type; Silkscreen labels clamp at a maximum size.
3. Components, each with SwiftUI previews and snapshot tests:
   `DialogBox` (speaker label, ▼ blink), `ChoicePrompt` (▶ marker, keyboard and VoiceOver focus),
   `QuestCard` (state badge, title, project chip, progress bar), `ArtifactCard` (gold header, "updated" badge),
   `ItemGetBanner`, `ProjectChip`, `StateBadge`, `PixelTabBar`, `HeartsHeader`.
4. Accessibility: every component has an accessibility label that reads as plain words (no "▶", no ALL CAPS
   spelled out); contrast passes WCAG AA in both themes; Reduce Motion stops the ▼ blink.
done when:
- [ ] Snapshot tests for every component in light, dark and the largest Dynamic Type size.
- [ ] An accessibility audit test (`XCUIApplication().performAccessibilityAudit()`) passes on the component gallery.

### S-12.3  Chat: the one conversation                        runs-on: Mac · size: L (split: feed / cards / composer)
depends: S-12.2, S-13.2   touches: ios/Marcel/Chat/
behaviours: B-01, B-02, B-03, B-05, B-06, B-12, B-36  ·  wire 1A, 1B, 1C, 3B, 4A, 6A
do:
1. The feed: user bubbles and Marcel `DialogBox`es, Markdown body. The composer is always enabled.
2. Message kinds rendered with PixelKit:
   - **thread card** (a `QuestCard` that updates in place from `task.*` events; tap → quest view);
   - **approval** as a `ChoicePrompt` (approve / deny / details), resolved state greys it out;
   - **artifact** milestone: `ItemGetBanner` (first version only) + `ArtifactCard` preview; tap → artifact viewer;
   - **digest**: the morning board as an `ArtifactCard`.
3. The avatar header (S-13.3) with the "needs you" count.
4. Pagination upward from the cache; jump to latest.
done when:
- [ ] UI test against the mock-hub scenario: send → two started cards → follow-up routed to a card → approval
  choice → approve → item-get + artifact card.
- [ ] A card already on screen updates when its task changes (no duplicate message).

### S-12.4  Side chat (reply in thread)                      runs-on: Mac · size: S
depends: S-12.3   touches: ios/Marcel/Chat/SideChat/
behaviours: B-08  ·  wire 2B
do: Long-press or the reply button on any message opens a sheet with the side conversation; the parent shows
"n replies". A "send result to main chat" action posts the chosen reply to the main conversation.
done when:
- [ ] UI test: open, reply, send to main.

### S-12.5  Quests tab                                        runs-on: Mac · size: M
depends: S-12.2   touches: ios/Marcel/Quests/
behaviours: B-07, B-25  ·  wire 2C
do: One `QuestCard` per thread, grouped **waiting on you** (pinned), **working**, **queued**, **ready for review**
(PR open), **done / failed** (last 7 days, collapsed). A project filter at the top. Swipe to stop. Live from WS.
done when:
- [ ] Test: the grouping and ordering view model.
- [ ] UI test: a task moving to `needs_you` jumps to the top; filtering by project hides the others.

### S-12.6  Quest view                                        runs-on: Mac · size: L (split: steps+detail / steer+approve)
depends: S-12.5   touches: ios/Marcel/Quest/
behaviours: B-11, B-12, B-26  ·  wire 2A
do:
1. **Steps first:** the worker's `report(progress)` events as a checklist (✓ done, ▶ current, · next).
2. **Live detail, folded:** the transcript from `GET /tasks/{id}/events` + WS `task.event`: text, collapsible
   tool rows, unified diffs with syntax colours, inline approval prompts.
3. Composer "talk to this quest" (steer). Toolbar: stop, hand back to Marcel, move to project, open in Claude.
4. Smooth with 5k events (lazy list).
done when:
- [ ] Snapshot tests for each event type.
- [ ] UI test: steer and approve.
- [ ] Performance test: 5k events load in under 1 s on the simulator.

### S-12.7  Map tab and project rooms                         runs-on: Mac · size: M
depends: S-12.2, S-04.9   touches: ios/Marcel/Map/
behaviours: B-30, B-31, B-32, B-33  ·  wire 5A, 5B
do:
1. Map: one tile per project (name, counts, "needs you" badge, board updated time, Claude Code link chip),
   plus "+ new" (which opens the chat with a prefilled request).
2. Project room: the pinned board (`ArtifactCard` → viewer), its quests by state, its items, its notes.
   The composer posts to the main conversation with `project_id` set (B-32).
3. Move a thread to another project from its card's menu (`POST /tasks/{id}/move`).
done when:
- [ ] UI test: open a project, see its board and quests, post a message that appears in the main chat tagged.
- [ ] UI test: moving a thread updates both project rooms.

### S-12.8  Items tab and the artifact viewer                  runs-on: Mac · size: M
depends: S-12.2, S-04.10   touches: ios/Marcel/Items/
behaviours: B-21, B-34, B-35  ·  wire 4B, 4C
do:
1. Items: a grid of artifacts grouped by project, newest change first; "updated" badge on living ones.
2. Viewer by kind: Markdown natively; HTML in `WKWebView` (JS off unless the hub marks it trusted); files in
   QuickLook; PR cards (state, checks, open in GitHub); **dashboards and boards** natively from JSON
   (`contracts/dashboard.schema.json`: stat, list, checklist, table, bar chart via Swift Charts).
3. Version header ("updated 2 min ago by Energy contracts · v3") and a history list; old versions open read-only.
4. "Ask about this" (posts to main chat with the artifact attached) and "pin to project".
done when:
- [ ] Each kind renders from mock-hub fixtures (snapshot tests).
- [ ] UI test: an `artifact.updated` event refreshes an open viewer and bumps the version.

### S-12.9  Marcel's notebook (memory)                        runs-on: Mac · size: S
depends: S-12.2   touches: ios/Marcel/Notebook/
behaviours: B-20, B-33  ·  wire 6B
do: Sections for "about you", project notes and dated notes; a Markdown editor with preview; save with etag
(409 → "changed elsewhere" with reload and a diff). A "what Marcel carries" card lists the last brain rollovers
(S-06.2) with what was kept.
done when:
- [ ] UI test: edit, save, conflict path.

### S-12.10  Settings, notifications, deep links             runs-on: Mac · size: M
depends: S-12.1, S-08.1   touches: ios/Marcel/Settings/ ios/Marcel/App/
behaviours: B-16, B-18
do:
1. Settings: devices; the notification backend (APNs if entitled, else an ntfy topic with instructions); schedules
   (pause, delete); usage meter; agent name.
2. Deep links `marcel://task/<id>`, `marcel://message/<id>`, `marcel://project/<slug>`, `marcel://artifact/<id>`,
   used by pushes.
done when:
- [ ] Test: every deep link routes correctly from a cold start.

# F13 — Pixel avatar  (waves 1–2)

The pixel-classic giraffe from SP5 (round 3). The sprite is **built**, not drawn by hand: parts, colour ramps
and choreography live in a script; the app plays the resulting sheet. The 3D/RealityKit plan is dropped.

### S-13.1  Sprite pipeline in the repo                       runs-on: cloud · size: M
depends: SP5   touches: shared/avatar/pixel/ contracts/avatar-sheet.schema.json
behaviours: B-28, B-29
do:
1. Move `scratch/spikes/SP5-avatar/pixel-source/build.py` to `shared/avatar/pixel/build.py`; keep it
   dependency-free (zlib PNG and GIF writers).
2. Add the missing loops: **needs_you** (looks at you, "!" above the head, hearts pulse) and **done** (nod +
   sparkle), and make **working** take n = 1, 2, 3 (n spots glow), as separate loops `working1..3`.
3. Write `contracts/avatar-sheet.schema.json` for the sheet JSON (frame size, frame count, loops of
   `[frame, ms]`), and emit `giraffe.png` + `giraffe.json` + preview GIFs into `shared/avatar/pixel/out/`.
4. A test that rebuilds the sheet and fails if the committed output differs (the sheet is reviewable and
   reproducible).
done when:
- [ ] `uv run pytest shared/avatar` passes: schema valid, sheet reproducible, every B-28 state has a loop.
- [ ] Preview GIFs of all loops attached to the PR for the owner's review.

### S-13.2  AvatarKit sprite player                           runs-on: Mac · size: S
depends: S-13.1, S-12.1   touches: ios/AvatarKit/
behaviours: B-28
do:
1. Load the sheet from the bundle; play a loop with `TimelineView`; whole-number zoom, nearest-neighbour.
2. `AvatarState` from the hub's `agent.state` (`idle | thinking | working(n) | needs_you | done`) → loop name;
   a state change waits for the current loop's next frame boundary, so it never jumps mid-blink.
3. Sizes: 24, 32, 44 and 120 pt. Pause off screen; Reduce Motion shows the first frame of each state.
done when:
- [ ] Unit tests: state → loop mapping, loop timing (frame at t), boundary-aligned switching.
- [ ] Snapshot tests of each state's first frame at every size.

### S-13.3  Avatar in the app                                 runs-on: Mac · size: S
depends: S-13.2, S-12.3   touches: ios/Marcel/Chat/AvatarHeader.swift ios/Marcel/App/
do: The avatar header on the chat (about 120 pt, collapsing to 44 pt on scroll), the 32-pt avatar in Marcel's
dialogue boxes, and an app icon rendered from the sheet's idle frame.
done when:
- [ ] UI test: state changes from the mock hub show up in the header.
- [ ] The owner signs off on how it looks.
