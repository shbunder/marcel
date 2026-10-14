# Chat — the one conversation (S-12.3)

The Chat tab: Marcel's avatar header, the conversation, and the composer. Wireframes 1A, 1B, 1C, 3B, 4A and 6A
in `scratch/spikes/SP5-avatar/wireframes/`. Drawn only with PixelKit and AvatarKit components.

It is a Swift package (`MarcelChat`) so its tests run in the simulator like the other packages'. The app links it
and shows `ChatScreen(model:offline:)`; the Xcode project keeps this folder out of the app target's own sources
(an exception on the `Marcel` folder in `project.pbxproj`).

## The look (S-18.3, ADR-007)

The chat reads like the Claude app; pixel art is the accent. The pieces are PixelKit's (`PixelKit/…/Chat/`, see its
README § Chat); the colours are the wireframe's (explainer artifact KtdD3PMLeyZP7QrwqYLrdv, Q1 v10).

- **Your messages** are rounded bubbles on the right with the notch (bottom-right corner 4 pt, the others 16 pt), dark
  text on your helper's colour (`ChatModel.bubbleColor` from the agent's look, MarcelLook's `LookColors`,
  again on every `agent.updated` of your helper; rose for Marcel),
  lightened when needed so the text reaches 4.5:1.
- **Marcel's replies** are plain formatted text across the full width (`MarcelReply`: PixelKit's `MarkdownView` in its
  chat style). No bubble, no avatar per message. Links are tappable in the text, only http(s). `ChatMarkdown` is gone.
- **The four special messages** are the only boxes (`CardRows.swift`): started (teal, "Watch ›"), needs you (gold,
  tinted; Approve / Not now / Details), done (rose, with the item's preview; it replaces the "YOU GOT" banner), failed
  (red, with the failed milestone's text). A quest's card follows its task and changes colour with it.
- **Time lines** ("Today 08:12") above the first message and between groups more than 15 minutes apart (`TimeLine`).
- **The thinking row** (`ThinkingRow.swift`): the small avatar and "Marcel is working…" at the end of the chat while
  the agent thinks or works; still under Reduce Motion.
- **Long-press** (or the VoiceOver action) on any stored row, cards included, is "Reply in thread". The side chat's
  quoted message is a `ChatQuote`.

## How it works

- **Everything on screen comes from the cache** (`MarcelKit`'s SwiftData rows), which the live event stream keeps
  current. `FeedInput` decodes a window of the newest messages plus tasks, items and projects; `ChatFeed.items`
  turns that into rows. The same call runs in the tests, so the tests check what the screen shows.
- **One card per thread.** A thread's first milestone is its `QuestCard`; it follows the task (`task.updated`)
  in place. Later milestones of that thread are one line from Marcel ("Done: …"), never a second card. A reply
  Marcel sends about a thread (`task_id` on the message — a follow-up it routed) shows that thread's live card under it.
- **Approvals** are not cached by the stream, so `ChatModel.reconcile` fetches `GET /approvals` when an approval
  message shows one the app does not hold, or when its task stops listing it (answered here, in a terminal, or
  expired). Only an open approval can be answered, once; then the buttons grey out and the card says the outcome.
- **Items**: a "done · you got an item" card on an item's first message only; a new version changes the card's label
  ("Updated · v2"). The morning board (an artifact of kind `board`) shows as a card with its first entries.
  Previews (a document's first lines, a board's entries) are fetched once per version.
- **A message sent from a project room** (`project_id`, B-32; the Map tab) shows that project's chip under it, and
  VoiceOver says "You said, about Home energy: …". The Map package reuses `ArtifactPreview.dashboardRows`, so it is
  public; both the chat's `Composer` and the project room's draw PixelKit's rounded `ChatComposerField`.
  The mention chips and the attached item going with the next message (`ComposerExtras`) sit on the composer's
  white strip above the field, at its inset.
- **"Ask about this" on an item** (S-12.11, app API 0.5) comes back to the chat with the item attached
  (`ChatModel.attach`; the viewer calls the `askInChat` environment value, which `ChatScreen` sets for viewers it
  opens and the app sets for the others). The item shows above the composer (X removes it) and goes with the next
  message only, as `artifact_id`, tagged with the item's project. The sent bubble shows the item under it, by its
  title from the cache ("An item" when the phone does not have it); VoiceOver says "You said, with the item …".
  If the hub no longer has the item (404 `artifact_not_found`), the bubble says "Item gone · tap to send without it"
  and a tap sends the message without it; a 404 for the conversation itself (`conversation_not_found`) says that.
- **"+ New" on the map** never overwrites what you typed: `ChatModel.propose` fills an empty composer, and otherwise
  adds the request on a new line after your words (once).
- **The agent's name is never hard-coded**: the header, the thinking row, the composer ("Talk to <name>…", and
  its VoiceOver label) and "<name>'s notebook" read it from `GET /me` (`ChatService.mainAgent`), `AgentName.fallback`
  until the hub has said. `ChatScreen` puts it in the environment (`agentName`, from MarcelItems) for everything it
  opens; the app does the same for the other tabs.
  The app passes a rename in Settings on (`SettingsModel.nameChanged` → `ChatModel.agentName`).
- **PR item cards** add "May be out of date: …" when the hub could not refresh the card from GitHub (`pr.note`).
- **Sending** never waits on Marcel: the message shows at once ("Sending…"), and if the hub cannot be reached it
  stays, marked "Not sent · tap to try again". A retry reuses the `client_id`, so the hub keeps one copy.
- **Scrolling**: the newest 50 messages; reaching the top loads 50 more from the cache. "Latest ▼" jumps back.
  Reduce Motion turns the animations off.
- **Settings** and **Marcel's notebook** open from a tap on the avatar header (`ChatScreen(openSettings:)`, S-12.10):
  `AvatarLinks` opens the only one or asks which, and gives VoiceOver the hint "Opens Marcel's notebook or Settings" and
  one named action per screen (S-12.9; `AvatarLinksTests`).
- **A message link** (`marcel://message/<id>`) sets `ChatModel.focus`: the feed scrolls to that message, loading older
  pages from the cache (up to 1,000 messages back) until it is in the window, then clears it. A message the phone
  does not have leaves the feed at the latest messages.
- Tapping an item opens the artifact viewer (`ArtifactViewer` from MarcelItems, S-12.8). A quest card opens the
  quest view (`QuestView` from MarcelQuest, S-12.6).

## The avatar header (S-13.3, B-28)

Code in `Sources/MarcelChat/AvatarHeader.swift` (the story names `Marcel/Chat/AvatarHeader.swift`; Chat is a package,
so it sits in the package's sources). Drawn with AvatarKit's sheet and loops; nothing is redrawn here. Since the S-18.6
follow-up it is the chat's white strip (thin line under it, the name in bold, what is running muted, a gold "needs
you" badge) instead of the pixel `HeartsHeader`, so it matches the other tabs.

- **What the giraffe shows** is the hub's word: `GET /me` gives the agent's state, then every `agent.state` event
  moves it (`ChatModel.agentState`; `LiveFeed` passes the app's `liveEvents` frames to `ChatModel.receive`). An event
  for another agent is ignored; after a `resync.required` the app asks `GET /me` again. Until the hub has said, and
  while the live stream is down, the header works the state out from the cached tasks (needs you, working(n), idle).
- **Size**: 120 pt at the latest messages, 44 pt once you scroll back or type (always 44 pt at accessibility text
  sizes). `AvatarHeader` is `Animatable` in `collapse` (0 → 1), so the strip shrinks smoothly while the sprite steps
  through whole zooms only (6×, 5×, 4×, 3×, 2× on a 3× phone): every sprite pixel stays a sharp square, never a blurred
  in-between. With Reduce Motion there is no animation and the giraffe holds its state's first frame (AvatarKit).
- **VoiceOver** reads the whole header as one line with the agent's name: "Marcel is thinking. Nothing running.
  1 thing needs you" (`AvatarState.accessibilityDescription(name:)`).
- **The app icon** is not drawn here any more: it is a white pixel M with a teal bow tie, drawn by
  `shared/brand/app-icon/generate.py` (see its README). The PNGs are in `../App/AppIcon/`, named the way iOS looks
  them up (`AppIcon60x60@3x.png`), and listed in `Config/Marcel-Info.plist` (`CFBundleIconFiles`). They are loose
  files, not an asset catalog: actool cannot compile a catalog on a Mac without the iOS platform component.
  `AppIcon-1024.png` is the App Store size. `AppIconTests` (`Tests/MarcelChatTests/AppIconTests.swift`) fails while a
  committed icon differs from the script's `checksums.txt`, has the wrong size or has transparency.
  `__Snapshots__/AppIcon-on-home-screen.png` is a screenshot of the built app on a simulator's home screen (not a
  test reference).

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
  `SideChats.counts` is counted once per thread when it comes on screen, and again when the stream adds a message to
  it, never on a redraw. One `SideChatModel` per message: a second tap or reopening shows the same side chat. A reply
  typed while the thread is still opening waits for that open, and confirming "Send to main chat" takes the proposal
  before the dialog closes.
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

## Each helper's own avatar (S-19.3, B-44)

- **The header and the thinking row** play the agent's own sheet: `ChatModel.avatars` (AvatarKit's `AvatarSheets`)
  fetches it after `GET /me` and again on an `agent.updated` with a new `avatar_etag`, keeps it in
  `Library/Caches/avatars`, and falls back to the bundled giraffe offline or before it arrives. The agent's id is
  remembered (`rememberAgent`), so an offline start plays its kept sheet at once. The JSON and the PNG must carry the
  same ETag; a look that changes between the two is asked for again. Views read the sheets from `\.chatAvatars`;
  `ChatAvatar` takes the `agentId`.
- **Several helpers in one chat** (ADR-007): when more than one helper speaks in the window, a reply starts with a
  speaker line (`SpeakerLine`: a 16-pt avatar and the name) where the speaker changes. Who said it: the message's own
  `agent_id` (app API 0.7.2), else the agent of its quest, else the agent whose conversation this is.
  With one helper no speaker line ever shows. Names come from the cached agents; every helper with a speaker line on
  screen gets its sheet fetched (`ChatModel.loadAvatars`), not only one whose look just changed.
- The app icon is not touched (`shared/brand/app-icon/`).

## Quoted replies and mentions (S-18.5, B-41, B-42)

- **"re:" line.** A message from your helper carries `reply_to_message_id`: the message of yours it answers. When
  that is not your latest message before it, the row gets a small line above it — "re: your 08:12 message",
  "re: your message yesterday 21:40", or "re: your earlier message" when the message is outside the window
  (`ChatFeed.items` sets `FeedItem.quote`; `ReplyQuoteLine` draws it). Cards, replies and milestones all get it.
  Tapping it scrolls to your message and outlines it for 2.5 s (`ChatModel.jump(to:)`, `chatHighlight`; no fade
  with Reduce Motion).
- **Mentions.** Typing `@` at the start of a word asks the hub (`GET /mentions`) for your quests and items, `#` for
  your projects (`MentionTrigger`). Picking one takes the `@word` out of the text and shows it as a chip above the
  composer (tap the chip to take it off); the message is sent with `mentions`. `@` asks the hub once and drops
  projects, so the newest-first order holds. A sign followed by a space with nothing matching ("I paid #5 for") is
  plain text and the list closes; a hub out of reach says "Can't reach the hub to look that up." A sent message shows its chips under
  the bubble: a quest chip opens the quest, an item chip the item, a project chip `marcel://project/<slug>`.
  VoiceOver reads ", mentioning …" after the message.

## Tests (`make -C ios chat-test`, part of `make ios-check`)

- `FeedTests`, `CardTests`, `ComposerTests` — the feed, the cards and sending, driven through a real in-memory
  cache fed with event frames. `CardTests.testTheScenarioFromSendToItemGet` is the story's scenario: send → two
  started cards → a routed follow-up → the approval → approve → item get + artifact card.
- `CatchUpTests` — app API 0.5 and the review follow-ups: a message with an item attached (sent, shown, retried),
  "+ New" never overwriting a draft, the header following the agent's name, a message whose
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
- `AvatarHeaderStateTests` — S-13.3's "UI test: state changes from the mock hub show up in the header", one layer
  down: `agent.state` frames through a real in-memory cache into `ChatModel.receive`, as `LiveFeed` wires it, and the
  header's state and VoiceOver line read back. Also: another agent's events ignored, offline falls back to the cached
  tasks, a re-sync asks `GET /me` again, and the collapse stepping through whole zooms.
  `AvatarHeaderSnapshotTests` draws the header at 3× in light and dark, expanded and collapsed in every state, half-way
  through the collapse. `AppIconTests` — see above.
- `ChatLookSnapshotTests` (S-18.3) — your message (with the notch), a reply with a heading, a list, a table and code,
  each special message and the thinking row, in light and dark at the default and the largest text size, and the
  whole chat. `ChatLookTests` — the notch's geometry, every chat colour pair at 4.5:1 (your text on every helper
  colour too), card colours per task state. `LongPressTests` is the story's UI test one layer down: the chat in a
  real window, the "Reply in thread" action found on a reply and on a card in the accessibility tree, and run.
  `ReplyTests`, `TimeLineTests` — replies through the shared Markdown, and the time lines.
- `QuoteAndMentionTests` (S-18.5) — when an answer gets the "re:" line and when not, its words, where `@` and `#`
  start a mention, `@fi` → chip → sent with `mentions`, `#` lists projects only, a stray sign, the hub out of
  reach, a pick after the draft changed, the jump's outline, stored chips, and the
  "Quote-and-mentions" snapshot in light and dark. `ReplyQuoteTapTests` is "tap jumps" one layer down: the line in a
  real window is a button, and pressing it jumps to the message it answers.
- `OwnAvatarTests` (S-19.3) — an `agent.updated` with a new look through a real cache into `ChatModel.receive` changes
  the header's and the thinking row's pixels; the same ETag fetches nothing; an offline start draws the giraffe;
  speaker lines only where the helper changes, none with one helper; speakers' sheets fetched once; the remembered
  agent's kept sheet offline; the live call through a stub hub (JSON then PNG, 304, a look changing mid-fetch, 401);
  snapshots "Own-avatar" and "Speakers".
- `ChatMockHubTests` — the same scenario against the mock hub (not part of the gate):
  `MOCK_DELAY=0.05 MOCK_HUB_PORT=7488 make mock-hub`, then
  `MOCK_HUB=http://127.0.0.1:7488/api make -C ios chat-integration`. The mock runs one thread at a time, so the
  two started cards come from two runs. XCUITest cannot run on this Mac's Xcode (see ../../README.md), so this
  model-level test stands in for the UI test.
  `testAgentStateFromTheMockHubShowsInTheHeader` follows a run's `agent.state` events into the header: thinking,
  working(1), needs you, working(1) after the approval, done, idle.
  `testQuotedRepliesAndMentionsOnTheMockHub` is S-18.5's UI test one layer down: the first card has no "re:" line;
  after a second message and the approval, the quest's later messages quote the first one; `@fi` → the quest as a
  chip → the hub stores the message with it. `testPickingALookOnTheMockHubShowsInTheHeader` is S-19.4's: pick fox,
  teal, cool in the look picker (MarcelLook) → Save → the header plays the fox and your bubbles turn teal.
