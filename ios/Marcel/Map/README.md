# Map — projects and their rooms (S-12.7)

The Map tab: one tile per project, and behind each tile the project's room. Wireframes 5A and 5B in
`scratch/spikes/SP5-avatar/wireframes/`. Drawn only with PixelKit components, plus the chat's composer and board
preview (MarcelChat), the quest log's grouping (MarcelQuests) and the quest view (MarcelQuest).

It is a Swift package (`MarcelMap`) so its tests run in the simulator like the other packages'. The app links it and
the tab bar shows `MapView` (`ios/Marcel/App/RootView.swift`). Xcode leaves a folder with a `Package.swift` out of
the app target's own sources by itself.

A `marcel://project/<slug>` link (S-12.10) opens a room through `MapView(opening:)`: the app sets the project id, the
map opens that room and sets it back to nil.

## How it works

- **Everything on screen comes from the cache** (`MarcelKit`'s SwiftData rows), which the live event stream keeps
  current. `MapInput.read` decodes the rows; `ProjectMap` builds the tiles and `ProjectRoom` a room. The same calls
  run in the tests. `project.updated` changes a tile's counts and moves it up; `task.moved` moves a quest between
  rooms; `artifact.updated` on a board changes its version, and the room fetches the new rows.
- **The map** lists live projects in the hub's order: most recent activity first, the inbox last. A tile shows the
  name, what is going on ("1 working · 2 items · PR ready", or "quiet"), when the board last changed ("Board
  updated 11:52", "yesterday", "Thu", "3 Oct"), a gold "1 needs you" badge, and a "Claude Code →" chip when the
  project links a Claude Code Project. Counts are the hub's (`Project.counts`). Opening the tab fetches
  `GET /projects` too, in case the stream has not brought them.
- **The agent's name** on the map and in the rooms ("or just ask <name>", the composer, the notes and "Sent to …")
  comes from the `agentName` environment value (MarcelItems), never a hard-coded "Marcel".
- **"+ New"** opens the chat with "Start a new project: " in the composer: Marcel makes projects. It never overwrites
  what you were typing there (the app calls `ChatModel.propose`, which adds it after your words).
- **A room** has the pinned board (an `ArtifactCard` with up to eight entries from `GET /artifacts/{id}/content`,
  fetched once per version), the project's quests grouped like the quest log (waiting on you first), its items as
  chips, and its notes (`memory/projects/<slug>.md`, read-only here). Tapping the board or an item opens
  the artifact viewer (`ArtifactViewer` from MarcelItems, S-12.8); tapping a quest card opens the quest view
  (`QuestView` from MarcelQuest) on top of the room (`MapNavigation`, `MapRoute`).
- **The composer** sends to the main conversation with `project_id` set (B-32): one conversation, tagged. The hub's
  answer goes into the cache at once, so the Chat tab shows the message straight away with the project's chip.
  A message that does not get through stays in the box.
- **Move a quest** from its card's menu (long press, or VoiceOver's actions): "Move to Family" posts
  `POST /tasks/{id}/move`. The answer goes into the cache, so it leaves this room and shows in the other at once;
  `task.moved` and both projects' `project.updated` follow on the stream. An archived target (409
  `project_archived`) leaves the quest where it was and says so.
- The hub comes from what pairing stored (address in UserDefaults, token in the Keychain); nothing is written there.

## Tests (`make -C ios map-test`, part of `make ios-check`)

- `MapNavigationTests` — a tile opens its room, a room's quest card opens the quest view, back returns to the room,
  a project link opens its room from anywhere.
- `ProjectMapTests` — the tiles' order, counts, words and times; a room's board, quests by state, items and move
  targets.
- `MapLiveTests` — the story's UI tests one layer down: an in-memory cache fed with event frames, the screen's model
  with a fake hub. Open a room, see its board and quests, post a message that the main chat shows tagged; moving a
  thread updates both rooms; `project.updated`, `task.moved` and a new board version reach the screens; failures
  in plain words. XCUITest cannot run on this Mac's Xcode (see ../../README.md).
- `LiveProjectServiceTests` — the real generated client with a stubbed transport: the requests (the message's
  `project_id`, the move's body, the notes path as one segment) and the hub's error replies (409
  `project_archived`, 404, 401, 400, no network) in the words the room shows.
- `MapSnapshotTests` — wireframes 5A and 5B in light, dark and Accessibility 5, plus a room after a send and a refused
  move, an empty room and an empty map, compared pixel for pixel with `Tests/MarcelMapTests/__Snapshots__/`. They
  render `MapStill` and `RoomStill` (the same pieces in a stack). After a deliberate change run
  `make -C ios map-record`, look at the PNGs, and commit them.
- `MapAccessibilityTests` — every tile is one plain-words sentence, the room's board, quests, items and notes read
  without symbols or capitals, section titles are headers; at the default and the largest text size.
- `MapMockHubTests` — against the mock hub (not part of the gate): `MOCK_DELAY=0.05 MOCK_HUB_PORT=7488 make
  mock-hub`, then `MOCK_HUB=http://127.0.0.1:7488/api make -C ios map-integration`. The map, a room's board and
  notes, a tagged message whose thread lands in the room, and a move to the inbox.
