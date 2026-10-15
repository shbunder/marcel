# Notebook — Marcel's notebook (S-12.9)

Marcel's memory as plain notes you can read and edit (B-20), and what Marcel carries across a fresh start (B-33).
Wireframe 6B in `scratch/spikes/SP5-avatar/wireframes/`; since S-18.6 in the chat's language (white header, rounded
cards on the soft ground), and a note's preview in the chat's Markdown style (`MarkdownView(style: .chat)`).

It is a Swift package (`MarcelNotebook`) so its tests run in the simulator like the other packages'. It depends on
MarcelKit and PixelKit (whose shared `MarkdownView` draws the preview). The app opens `NotebookView` from the avatar header: a tap on the giraffe asks "Marcel's notebook" or
"Settings" (`AvatarLinks` in MarcelChat; VoiceOver hears "Opens Marcel's notebook or Settings" and gets both as named
actions), wired in `ios/Marcel/App/RootView.swift`.

## The list

| Section | File | What it shows |
|---|---|---|
| About you | `MEMORY.md` | Its first lines ("· Prefers mornings for appointments"), then "Open and edit". |
| Project notes | `memory/projects/<slug>.md` | One row per project, by the project's name from the cache; projects without notes yet are listed after them ("No notes yet") and opening one starts the file. |
| Dated notes | `memory/YYYY-MM-DD.md` | Newest first, titled by the date ("Sun, 4 Oct 2026"). |
| Other notes | anything else | Only when there are any. |
| What Marcel carries | `GET /agents/{id}/brain/rollovers` and `brain.rollover` events | "Context rolled over yesterday 23:00 (the daily fresh start). Kept: … Your chat history is unchanged." |

A row with unsaved changes on this phone says so ("Updated yesterday 18:40 · unsaved changes").

## Editing a note

- **Preview / Edit.** An existing note opens on its preview; a new note on the editor. The preview is the item
  shared `MarkdownView` (PixelKit), as in the item viewer, with its rules: nothing opens by itself; only an
  `http`/`https` address with a host is a link (tappable in the text, and a button under its block); HTML in a note is
  shown as words, never run (there is no web view).
- **Save** sends `PUT /memory/files/{path}` with `If-Match: <etag>` of the version the edit started from (bare: the
  generated client URI-encodes header values, so quotes would arrive as `%22`; the contract allows a bare etag). A new
  note is sent without `If-Match`, so the hub creates it, and answers 409 if it exists after all.
- **Changed elsewhere (409).** The hub answers with its current version; nothing is saved and nothing is overwritten.
  The note shows who changed it and when, and a line-by-line diff (`LineDiff`): the hub's lines (−, blue) and yours
  (+, green), with long unchanged runs folded to "3 lines the same". Save is off until you choose:
  - **Reload theirs** (after a confirmation) shows the hub's version and throws your changes away;
  - **Save mine over it** saves yours against the version just shown (its etag), so a third change made meanwhile
    comes back as another conflict, never a silent overwrite.
- **Drafts.** Every change is kept on this phone (`DraftStore`, `notebook.draft.<path>` in user defaults) with the
  etag it started from, until it is saved or thrown away. So unsaved text survives leaving the screen, closing the app
  and a save that failed. Opening a note that has a draft brings it back ("Your unsaved changes from … are back");
  if the note changed on the hub since the draft began, the conflict shows at once, and keeps showing until you
  choose. Offline, a draft still opens ("Not saved yet: the hub can't be reached. Your draft from …"); the later save
  still checks its version. A draft whose words are what the hub now has is thrown away (when the list loads, and when
  the note opens), whatever version it started from, so it never shows "unsaved changes" for nothing.
- **Bounded.** At most 50 drafts, each at most 1 MB (the hub's own limit). A longer text is not kept, and the note
  says so; a 51st draft throws away the oldest one, and the note names it.
- **Removed elsewhere** (404 `memory_file_not_found` on a save): "This note was removed since you opened it. Your text
  is still here: save again to create it." The next save creates it.
- **Unpairing** forgets every draft and the rollover log: `RolloverLog.attach(to: connection)` registers that with
  `HubConnection.onUnpair`, so it also happens when the hub refuses the phone.

## Paths are untrusted

A path from the hub is used only if it matches the contract's pattern (`MemoryPath`): letters, digits, `.`, `_`, `-`,
`/`, ending in `.md`, no `..`, no leading `/`, no part starting with `.`, no empty part, at most 512 bytes. Anything
else in `GET /memory` is left out ("1 note has a name the app won't open") and never requested. On the wire the
generated client percent-encodes the path as one segment (`memory%2Fprojects%2Fenergy.md`). A file the hub sends back
must have the path that was asked for.

**Whose notebook** (S-22.29, app API 0.8.1): each buddy has its own. The tree, a read and a save send `agent_id` = the
buddy this phone shows (`CurrentBuddy`, no extra request), so after a switch the next request is the new buddy's.
Before the phone knows a buddy it sends none, and the hub uses this phone's. 404 `agent_not_found` (a buddy that is
gone, or saving to an archived one: its notebook stays readable) shows "This buddy is gone or archived, so its notebook
can't change."

## What Marcel carries

The card reads the hub's list, `GET /agents/{id}/brain/rollovers?limit=10` (app API 0.6; the agent is the buddy shown,
among `GET /me`'s), and merges in `RolloverLog`, the rollovers this phone saw on the live stream (`attach(to: connection.cache)`;
kept on the phone, the last ten). The same rollover told twice (the hub's entry and the event: same reason, within a
minute) shows once; newest first, at most ten. A rollover that arrives while the screen is open shows at once. The real
hub does not serve the route until S-06.7: its generic 404 `not_found` (or a 405, or a bare 404) leaves the phone's log
on its own, silently; any other failure says so under the list ("Couldn't read the past fresh starts from the hub, so
this shows the ones this phone saw. …").

**Fresh start** (`POST /agents/{id}/brain/rollover`) asks first ("Give Marcel a fresh start?" — Marcel starts a new
session with its notes, recent messages and open threads, says hello in the chat when it's ready; the chat history
stays as it is). Once the hub accepts it (202), or answers 409 `rollover_running` (one is already on its way), the
button is off and the card says "Fresh start on its way…", until a `manual` rollover from then on shows: "Done: Marcel
started fresh today 21:04." A hub without the route says "Not available yet: this hub can't give Marcel a fresh start
yet."; 503 `runner_unreachable` "The hub can't start a new session for Marcel right now. Nothing changed."; the button
works again after a failure.

## Errors, in plain words

Like Settings: a bare 404 / 405 / 501 (a route the hub has not built) is "Not available yet: the hub can't show
Marcel's notebook yet."; 401 "The hub no longer accepts this phone. Pair it again."; 503 `memory_unavailable` "Marcel's
notebook can't be reached on the hub right now. Nothing was changed." with the hub's reason; another 5xx "The hub had
a problem. Try again in a minute."; no network "Couldn't reach the hub. Check the connection and try again."; 400
`memory_file_too_big` "This note is over 1 MB, too big to open or save on the phone."

## Accessibility

Every row reads as one sentence ("Home energy, Updated today 16:40") with the hint "Opens the note"; section names
and the note's title are headers; Edit / Preview carry the selected trait; each diff line reads "Only in the hub's
version: …", "Only in yours: …" or "3 lines the same"; buttons are at least 44 pt. Text uses PixelKit's fonts, which
follow Dynamic Type (the list is also snapshotted at Accessibility 5).

## Tests (`make -C ios notebook-test`, part of `make ios-check`)

- `NoteEditorTests` — the story's UI test (edit, save, conflict) one layer down: XCUITest cannot run on this Mac's
  Xcode (no iOS 26.5 platform, see ../../README.md), so the editor's model is driven as the screen drives it, against a
  fake hub that keeps files like the real one (git blob etags, `If-Match`, 409 with the current version, 404 for a
  removed file), through the generated client. Edit and save with the etag; create without `If-Match`; a change made
  elsewhere is never overwritten and shows the diff; reload; save mine over it; a third change during the choice;
  drafts across leaving the screen, a failed save (offline, 503, 500, 401, too big) and an older version; a removed
  note; reading failures in words.
- `NotebookModelTests` — the sections and their order, project names, drafts on rows, untrusted paths left out and
  never requested, "not available yet" and the hub's answers in words.
- `MemoryPathTests` — the pattern, the sections, the path as one encoded segment on the wire, an answer about another
  file refused.
- `PreviewSafetyTests` — HTML stays words, only `https` links get a button, and drawing the preview opens nothing.
- `RolloverHistoryTests` — the hub's list merged with the phone's log without duplicates, newest ten; a hub without
  the route (404 `not_found`, 405, bare 404) falling back silently; another failure said; an older log still read;
  Fresh start on its way until the manual rollover shows, one tap sends one request, 409 `rollover_running`, and the
  failures in words.
- `RolloverLogTests` — rollovers from an in-memory cache's frames, newest first, replays ignored, the last ten kept
  across launches, reasons in words.
- `LineDiffTests` — order and folding, and what VoiceOver reads.
- `ReviewFixTests` — unpairing forgets drafts and rollovers; a draft equal to the hub's words is dropped; an offline
  draft's header line; the 1 MB and 50-draft bounds.
- `NotebookSnapshotTests` — the notebook (light, dark, Accessibility 5), a fresh start on its way and one a hub can't
  do yet (light and dark), a hub without the notebook, a note's preview,
  editing, and "changed elsewhere" (light and dark), compared pixel for pixel (no changed pixel allowed) with
  `Tests/MarcelNotebookTests/__Snapshots__/`. They render the content without the scroll view and the editor's text as
  plain text (`notebookStill`), which `ImageRenderer` needs. After a deliberate change run `make -C ios notebook-record`,
  look at the PNGs, and commit them.
