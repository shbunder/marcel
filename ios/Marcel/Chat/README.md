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
- **"Ask about this" on an item** (S-12.11, app API 0.5) comes back to the chat with the item attached
  (`ChatModel.attach`; the viewer calls the `askInChat` environment value, which `ChatScreen` sets for viewers it
  opens and the app sets for the others). The item shows above the composer (X removes it) and goes with the next
  message only, as `artifact_id`, tagged with the item's project. The sent bubble shows the item under it, by its
  title from the cache ("An item" when the phone does not have it); VoiceOver says "You said, with the item …".
  If the hub no longer has the item (404 `artifact_not_found`), the bubble says "Item gone · tap to send without it"
  and a tap sends the message without it; a 404 for the conversation itself (`conversation_not_found`) says that.
- **"+ New" on the map** never overwrites what you typed: `ChatModel.propose` fills an empty composer, and otherwise
  adds the request on a new line after your words (once).
- **The agent's name is never hard-coded**: the header, Marcel's dialogue boxes, the composer ("Talk to <name>…", and
  its VoiceOver label) and "<name>'s notebook" read it from `GET /me` (`ChatService.mainAgent`), `AgentName.fallback`
  until the hub has said. `ChatScreen` puts it in the environment (`agentName`, from MarcelItems) for everything it
  opens; the app does the same for the other tabs.
  The app passes a rename in Settings on (`SettingsModel.nameChanged` → `ChatModel.agentName`).
- **PR item cards** add "May be out of date: …" when the hub could not refresh the card from GitHub (`pr.note`).
- **Sending** never waits on Marcel: the message shows at once ("Sending…"), and if the hub cannot be reached it
  stays, marked "Not sent · tap to try again". A retry reuses the `client_id`, so the hub keeps one copy.
- **Scrolling**: the newest 50 messages; reaching the top loads 50 more from the cache. "Latest ▼" jumps back.
  The giraffe is 120 pt at the latest messages and 44 pt once you scroll back or type (and always 44 pt at
  accessibility text sizes). Reduce Motion turns the animations off.
- **Settings** and **Marcel's notebook** open from a tap on the avatar header (`ChatScreen(openSettings:)`, S-12.10):
  `AvatarLinks` opens the only one or asks which, and gives VoiceOver the hint "Opens Marcel's notebook or Settings" and
  one named action per screen (S-12.9; `AvatarLinksTests`).
- **A message link** (`marcel://message/<id>`) sets `ChatModel.focus`: the feed scrolls to that message, loading older
  pages from the cache (up to 1,000 messages back) until it is in the window, then clears it. A message the phone
  does not have leaves the feed at the latest messages.
- Tapping an item opens the artifact viewer (`ArtifactViewer` from MarcelItems, S-12.8). A quest card opens the
  quest view (`QuestView` from MarcelQuest, S-12.6).

## Side chat — reply in thread (S-12.4, B-08, wireframe 2B)

Code in `Sources/MarcelChat/SideChat/`.

- **Opening**: a long-press on any stored message gives "Reply in thread" (also a VoiceOver action on the row); a
  tap on a message's "n replies" opens the same thing. `ChatModel.openSideChat` makes a `SideChatModel` and
  `ChatScreen` shows `SideChatSheet` over the feed. The sheet calls `POST /messages/{id}/thread` (it returns the
  thread the message already has, or a new one), then fetches the thread's newest 100 messages
  (`GET /conversations/{id}/messages`) into the cache. After that the live stream adds each new message. A thread
  the phone already knows (`Message.thread`, or one opened earlier) shows from the cache at once, offline too.
- **The thread's rows are the main chat's rows** (`ChatFeed.items` over the thread's conversation), so a task's
  milestones and approval cards look and answer the same. Your replies show at once ("Sending…"); a reply that does
  not get through stays, "Not sent · tap to try again", and a retry reuses its `client_id`.
- **"n replies"** under a message in the main chat: the larger of the hub's `thread.reply_count` and how many
  messages of that thread the cache holds, so it follows the live stream without the hub re-sending the parent.
  `SideChats` remembers threads opened here and threads announced by `conversation.created` (opened on another
  device), which it hears through the app's `liveEvents` cache.
- **"Send result to main chat"** takes the thread's newest message from the agent or a task, asks first ("Send this
  to the main chat?", showing exactly what will be quoted), then calls `POST /conversations/{id}/send-to-main` with
  its `message_id`. The quote reaches the main chat over the stream; the sheet says "Sent to the main chat."
- **Task threads** (`Conversation.task_id`, S-04.9; a thread opened on a milestone or approval): the sheet says
  "Replies go to <task>", shows "Open quest" (the same quest view a quest card opens), and a done task's composer
  says "Ask a follow-up…". A stopped or failed task (409 `task_finished`, or a task the cache already shows as
  stopped/failed) shows "This task has stopped; start a new one?"; "Start a new one" closes the sheet and puts what
  you wrote in the main chat's composer, where the agent can start one.
- **Drafts are per thread** (`SideChats.drafts`, by the message the thread is on): closing the sheet keeps what you
  typed, and the main composer's draft is never touched.
- **Errors in plain words** (`SideChatError`): offline, 401 (pair again), 5xx (the hub had a problem), 409
  `task_finished`, and a 404 read by its code — `message_not_found` / `conversation_not_found` mean gone, the generic
  `not_found` (or a bare FastAPI 404) means this hub has not built side chats yet.

## Tests (`make -C ios chat-test`, part of `make ios-check`)

- `FeedTests`, `CardTests`, `ComposerTests` — the feed, the cards and sending, driven through a real in-memory
  cache fed with event frames. `CardTests.testTheScenarioFromSendToItemGet` is the story's scenario: send → two
  started cards → a routed follow-up → the approval → approve → item get + artifact card.
- `CatchUpTests` — app API 0.5 and the review follow-ups: a message with an item attached (sent, shown, retried),
  "+ New" never overwriting a draft, the header and the dialogue boxes following the agent's name, a message whose
  item is gone sent without it on a tap, a send's 404 told apart by its code, and a scan of Chat's, Items' and Map's
  sources for user-facing words that hard-code "Marcel".
- `ChatSnapshotTests` — the main states in light and dark (and the largest text size for two), compared pixel for
  pixel with `Tests/MarcelChatTests/__Snapshots__/` like PixelKit's. After a deliberate change run
  `make -C ios chat-record`, look at the PNGs, and commit them.
- `SideChatTests` — the side chat through `ChatModel` and `SideChatModel` with a fake hub and a fed cache.
  `testOpenReplySendToMain` is S-12.4's "UI test: open, reply, send to main", one layer down (XCUITest cannot run
  here). Also: "n replies" following the stream and other devices, task threads (follow-up on a done task, 409 in
  plain words, "Start a new one"), drafts per thread, offline and gone, and the real client's answers through
  `StubHub`. `SideChatSnapshotTests` draws the sheet (light, dark, largest text), a stopped task's thread and
  "n replies" in the main chat.
- `ChatMockHubTests` — the same scenario against the mock hub (not part of the gate):
  `MOCK_DELAY=0.05 MOCK_HUB_PORT=7488 make mock-hub`, then
  `MOCK_HUB=http://127.0.0.1:7488/api make -C ios chat-integration`. The mock runs one thread at a time, so the
  two started cards come from two runs. XCUITest cannot run on this Mac's Xcode (see ../../README.md), so this
  model-level test stands in for the UI test.
