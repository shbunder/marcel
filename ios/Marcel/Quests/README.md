# Quests — the quest log (S-12.5)

The Quests tab: every thread as a `QuestCard`, grouped by what it needs from you. Wireframe 2C in
`scratch/spikes/SP5-avatar/wireframes/`. Drawn only with PixelKit components.

It is a Swift package (`MarcelQuests`) so its tests run in the simulator like the other packages'. The app links it
and the tab bar shows `QuestsView()` (`ios/Marcel/App/RootView.swift`). Xcode leaves a folder with a `Package.swift`
out of the app target's own sources by itself.

## How it works

- **Everything on screen comes from the cache** (`MarcelKit`'s SwiftData rows), which the live event stream keeps
  current: `task.created`, `task.updated`, `task.moved` and `artifact.created` move cards as they arrive.
  `QuestBoard.read` decodes the rows; `QuestBoard` does the grouping. The same call runs in the tests.
- **Groups, top to bottom:** *Waiting on you* (`needs_you`, pinned first) · *Working* (`starting`, `working`,
  `silent`, `unknown`) · *Queued* · *Ready for review* (`done` with an open or draft PR) · *Done · this week*
  (`done`, `failed`, `stopped` in the last 7 days; folded until you tap it). Inside a group the newest activity
  (`last_activity_at`, else `updated_at`) is on top; the queue runs oldest first. Equal times sort by id.
- **A card** shows the title, the state badge ("PR" when ready for review), the project, and one line: what it waits
  for, the PR ("PR #41 · checks passed"), where it runs (NUC or Cloud), its age and its last activity. VoiceOver
  reads the whole card as one sentence in full words. Project colours use the same hash as the Chat tab's cards.
- **The filter** at the top picks one project, or all, and switches to *Archived quests*. Archived quests are not on
  the live stream, so turning it on fetches `GET /tasks?archived=true` into the cache.
- **Tap** a card to open the quest (`QuestView` from `../Quest`, wireframe 2A).
- **Swipe** a running or queued quest to stop it (after a confirmation), a finished one to archive it. The hub's
  answer goes into the cache at once; `task.updated` confirms it later. A stop the NUC did not answer (503) leaves the
  quest running and says so.
- The hub comes from what pairing stored (address in UserDefaults, token in the Keychain); nothing is written there.

## Tests (`make -C ios quests-test`, part of `make ios-check`)

- `QuestBoardTests` — the grouping and ordering rules, the card's words, the filter, the seven-day window.
- `QuestsLiveTests` — the story's UI tests one layer down: an in-memory cache fed with event frames, the screen's
  model with a fake hub. A quest moving to `needs_you` jumps to the top; filtering by project hides the others;
  archiving a done quest moves it under *Archived*; stop, its failures, and a PR arriving. XCUITest cannot run on
  this Mac's Xcode (see ../../README.md).
- `QuestSnapshotTests` — wireframe 2C in light, dark and Accessibility 5, plus the opened done group and an empty
  archive, compared pixel for pixel with `Tests/MarcelQuestsTests/__Snapshots__/`. `ImageRenderer` draws nothing
  inside a `List`, so they render `QuestLogStill` (the same pieces in a stack). After a deliberate change run
  `make -C ios quests-record`, look at the PNGs, and commit them.
- `QuestAccessibilityTests` — reads the accessibility tree: every card is one plain-words sentence, group titles are
  headers, nothing in capitals or symbols, at the default and the largest text size.
- `QuestsMockHubTests` — against the mock hub (not part of the gate): `MOCK_DELAY=0.2 MOCK_HUB_PORT=7488 make
  mock-hub`, then `MOCK_HUB=http://127.0.0.1:7488/api make -C ios quests-integration`. A live quest lands in
  *Working*, then on top under *Waiting on you*; stop, archive and the archived list reach the hub and decode.
