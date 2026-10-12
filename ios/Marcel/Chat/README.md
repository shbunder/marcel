# Chat — the one conversation (S-12.3)

The Chat tab: Marcel's avatar header, the conversation, and the composer. Wireframes 1A, 1B, 1C, 3B, 4A and 6A
in `scratch/spikes/SP5-avatar/wireframes/`. Drawn only with PixelKit and AvatarKit components.

It is a Swift package (`MarcelChat`) so its tests run in the simulator like the other packages'. The app links it
and shows `ChatScreen(model:offline:)`; the Xcode project keeps this folder out of the app target's own sources
(an exception on the `Marcel` folder in `project.pbxproj`).

## How it works

- **Everything on screen comes from the cache** (`MarcelKit`'s SwiftData rows), which the live event stream keeps
  current. `FeedInput` decodes a window of the newest messages plus tasks, items and projects; `ChatFeed.items`
  turns that into rows. The same call runs in the tests, so the tests check what the screen shows.
- **One card per thread.** A thread's first milestone is its `QuestCard`; it follows the task (`task.updated`)
  in place. Later milestones of that thread are one line from Marcel ("Done: …"), never a second card. A reply
  Marcel sends about a thread (`task_id` on the message — a follow-up it routed) shows that thread's live card under it.
- **Approvals** are not cached by the stream, so `ChatModel.reconcile` fetches `GET /approvals` when an approval
  message shows one the app does not hold, or when its task stops listing it (answered here, in a terminal, or
  expired). Only an open approval can be answered, once; then the choice prompt greys out and says the outcome.
- **Items**: "You got: …!" on an item's first message only; a new version changes the card's badge
  ("Updated · v2"). The morning board (an artifact of kind `board`) shows as a card with its first entries.
  Previews (a document's first lines, a board's entries) are fetched once per version.
- **A message sent from a project room** (`project_id`, B-32; the Map tab) shows that project's chip under it, and
  VoiceOver says "You said, about Home energy: …". The Map package reuses the chat's `Composer` and
  `ArtifactPreview.dashboardRows`, so both are public.
- **Sending** never waits on Marcel: the message shows at once ("Sending…"), and if the hub cannot be reached it
  stays, marked "Not sent · tap to try again". A retry reuses the `client_id`, so the hub keeps one copy.
- **Scrolling**: the newest 50 messages; reaching the top loads 50 more from the cache. "Latest ▼" jumps back.
  The giraffe is 120 pt at the latest messages and 44 pt once you scroll back or type (and always 44 pt at
  accessibility text sizes). Reduce Motion turns the animations off.
- **Settings** open from a tap on the avatar header (`ChatScreen(openSettings:)`, S-12.10).
- **A message link** (`marcel://message/<id>`) sets `ChatModel.focus`: the feed scrolls to that message, loading older
  pages from the cache (up to 1,000 messages back) until it is in the window, then clears it. A message the phone
  does not have leaves the feed at the latest messages.
- Tapping an item opens the artifact viewer (`ArtifactViewer` from MarcelItems, S-12.8). A quest card opens the
  quest view (`QuestView` from MarcelQuest, S-12.6).

## Tests (`make -C ios chat-test`, part of `make ios-check`)

- `FeedTests`, `CardTests`, `ComposerTests` — the feed, the cards and sending, driven through a real in-memory
  cache fed with event frames. `CardTests.testTheScenarioFromSendToItemGet` is the story's scenario: send → two
  started cards → a routed follow-up → the approval → approve → item get + artifact card.
- `ChatSnapshotTests` — the main states in light and dark (and the largest text size for two), compared pixel for
  pixel with `Tests/MarcelChatTests/__Snapshots__/` like PixelKit's. After a deliberate change run
  `make -C ios chat-record`, look at the PNGs, and commit them.
- `ChatMockHubTests` — the same scenario against the mock hub (not part of the gate):
  `MOCK_DELAY=0.05 MOCK_HUB_PORT=7488 make mock-hub`, then
  `MOCK_HUB=http://127.0.0.1:7488/api make -C ios chat-integration`. The mock runs one thread at a time, so the
  two started cards come from two runs. XCUITest cannot run on this Mac's Xcode (see ../../README.md), so this
  model-level test stands in for the UI test.
