# Quest — inside one quest (S-12.6)

`QuestView(taskId:)`: one quest, as wireframe 2A draws it (`scratch/spikes/SP5-avatar/wireframes/`). The Chat tab's
quest cards and the Quests tab's cards open it. Drawn only with PixelKit components.

It is a Swift package (`MarcelQuest`) so its tests run in the simulator like the other packages'. It depends only on
MarcelKit and PixelKit, so any tab can open it without a dependency cycle; keep it that way.

## What it shows

- **The look** (S-18.4, the chat's language): a plain header, the soft background, Claude's text as plain formatted
  text (the shared Markdown's chat style), your notes as your bubbles, approvals as the chat's "needs you" card; tool
  rows and diffs are the only boxes.
- **Header:** ‹ back, the quest's name, and "Working · Home energy · NUC · sonnet".
- **Progress first:** the worker's `report(progress)` events as a checklist. Every report but the last is ✓ done;
  the last is ▶ what it is doing now, ✓ once the quest is done, × where it stopped if it failed or was stopped.
  While the quest runs, the latest report's `next` steps (app API 0.5) follow as "· next". Each report replaces that
  list; a report without `next` shows none (the worker gave none, which is not "nothing left"). A finished quest shows
  no plan. Before the first report, "· Waiting for its turn" for a queued quest.
- **Live detail** (folds away as a whole): Claude's text, Marcel's notes as plain text and yours as bubbles, tool calls as
  one-line rows that open to show what came back (30 lines, then "…and N more lines"), diffs that open to their lines
  with + and − in the margin and the code in syntax colours (60 lines), approval prompts with Approve / Not now in place,
  and one plain line for state changes, items made, helpers, errors and anything the app can't show.
- **Toolbar:** Stop (after a confirmation), Hand back to Marcel (with an optional note), Move to project (a menu of
  the live projects), Open in Claude (`cloud_url` for a cloud quest, `remote_url` for a NUC session with Remote
  Control, never the other way round; only an `https://claude.ai/code…` address, as the contract describes them,
  opened by the system, so in Safari or the Claude app).
- **Composer** "Talk to this quest…": `POST /tasks/{id}/messages`. Your message shows as "sending…", then "sent",
  until the hub records it as a note in the transcript (a recorded note has no `client_id`, so each new note
  replaces one pending message with the same words). A finished quest answers 409; the text stays in the box and
  the screen says to ask Marcel instead.

Errors are one plain sentence above the steps; tap it to dismiss.

## How it works

- **History plus live, by `seq`.** `load()` reads `GET /tasks/{id}` and then `GET /tasks/{id}/events` page by page
  (200 a page). Live `task.event` frames for this quest are merged as they arrive. `QuestTranscript` keeps one event
  per `seq` (a replay never duplicates) and knows the last contiguous `seq`: a live event past a hole fetches the
  missing ones (`fillGap`) instead of leaving a gap. A history that starts after 1 (the hub kept only newer events)
  is not a hole. A gap noticed while the pages were being read is fetched at the end of `load()`.
- **After an outage.** When the stream re-syncs (`resync.required`), the frames it missed are never replayed. The
  cache tells its observers, and an open quest re-reads its task (state, open approvals) and every event after the
  last one it has (`reload`), without waiting for a new live event.
- **Live frames.** The cache does not store `task.event` or approval frames, so the view listens to the app's cache
  through `MarcelCache.observeFrames` (MarcelKit). The app passes that cache down once, as the `liveEvents`
  environment value (`RootView`). Without it the view still shows the history and refreshes when pulled.
- **Approvals.** A `permission` event carries the approval id. Its prompt is open while the task lists it in
  `approval_ids`; `approval.created` / `approval.resolved` frames and your own answer replace that. If a terminal
  answered first (409), the prompt shows that answer and the screen says so. Once the quest is finished, an
  unanswered prompt reads "No longer waiting".
- **Fast with thousands of events.** The events are read straight from the decoded JSON (no second decoding pass),
  folded into rows in one pass, and rebuilt only when the transcript, the task or an approval changes, never while
  you type (the composer is its own view). The list is a `LazyVStack`; long text is cut when a row is drawn.
- **Untrusted text.** Everything from the session is drawn as plain text: no Markdown, no links, no HTML. The syntax
  colourer only splits a line into coloured runs. Very long lines end in "…".

## Tests (`make -C ios quest-test`, part of `make ios-check`)

- `QuestDetailTests` — every contract example reads as its kind, malformed data is one line, steps and the latest
  report's "· next" steps, how rows fold,
  approval states, capping, syntax colours, untrusted text stays inert, Open in Claude is https only.
- `QuestLiveTests` — the story's UI test ("steer and approve") one layer down: the model with a fake hub, fed with
  frames through a real in-memory cache. Also: history and live merged without duplicates or gaps, paging, a
  terminal answering first, a finished quest refusing a message, stop / hand back / move. XCUITest cannot run on this
  Mac's Xcode (see ../../README.md).
- `QuestPerformanceTests` — 5,000 events decoded from the hub's JSON pages, merged, folded and laid out in a real
  window in under a second; one live event on such a quest in under 50 ms.
- `QuestSnapshotTests` — wireframe 2A in light, dark and Accessibility 5, a finished quest, and every event kind in
  light and dark, compared pixel for pixel with `Tests/MarcelQuestTests/__Snapshots__/`. `ImageRenderer` draws
  nothing inside a `ScrollView`, so they render `QuestStill` and `RowsStill` (the same rows in a plain stack). After a
  deliberate change run `make -C ios quest-record`, look at the PNGs, and commit them.
- `QuestAccessibilityTests` — reads the accessibility tree: plain words, no symbols or capitals, titles are headers,
  every button at least 44 pt tall, at the default and the largest text size.
