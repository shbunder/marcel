# Items — the Items tab and the artifact viewer (S-12.8)

The Items tab (wireframe 4C, B-21) and the viewer every item opens in (wireframe 4B, B-34, B-35), in
`scratch/spikes/SP5-avatar/wireframes/`. Drawn with PixelKit components.

It is a Swift package (`MarcelItems`) so its tests run in the simulator like the other packages'. The app links it and
the tab bar shows `ItemsView` (`ios/Marcel/App/RootView.swift`). The Chat tab (an item card) and the Map tab (a
room's board and item chips) open `ArtifactViewer(artifactId:conversationId:)`; that and `ItemsView` are the
package's only screens. It depends on MarcelKit and PixelKit only, so Chat and Map can depend on it.

## How it works

- **Everything on screen comes from the cache** (`MarcelKit`'s SwiftData rows), which the live event stream keeps
  current. `ItemsInput.read` decodes the rows; `ItemsLibrary` builds the grid. Opening the tab also fetches
  `GET /artifacts` (up to five pages of 200), in case the stream has not brought them.
- **The grid** groups items by project; the project whose newest item changed last comes first, and inside a project
  the newest change comes first. A tile says what the item is ("Document · v3", "Pull request #14 · checks
  passing", "PDF · 1.2 MB", "Link · www.engie.be") and when it changed ("Updated 2 min ago"). A **living** item
  (a board, or anything past version 1) that changed in the last 24 hours wears a gold "Updated" badge.
- **The viewer** reads its item from the cache by id, so an `artifact.updated` changes its version line at once and
  the viewer fetches that version's bytes (`GET /artifacts/{id}/content?version=n`, always by number, once per
  version). An item the cache does not have yet is fetched with `GET /artifacts/{id}`.
- **By kind** (`ItemBody.make`):
  - `doc` as Markdown or plain text: drawn natively (`ItemMarkdown`): headings, paragraphs, lists, quotes, code,
    tables. Links become buttons under their block, and only `http`/`https` with a host — like the chat's Markdown.
  - `doc` as HTML: a `WKWebView` under `WebPolicy`. JavaScript is **off** unless the hub marked the item `trusted`;
    an untrusted page also gets a content security policy that blocks scripts and every network load; nothing is
    stored between pages; the web view never navigates away — a tapped web link opens in Safari, anything else is
    cancelled. A line under the header says which it is.
  - `file`: written to the app's temporary folder (`items/<id>-v<n>/<title>.<ext>`) and shown with QuickLook.
  - `pr`: a card with the state, checks, review and "This changes Marcel itself" for a self-change; "Open in
    GitHub" only for an `https://github.com/…` address.
  - `link` and `branch`: a card; a button only for an http(s) address.
  - `dashboard` and `board`: drawn from the JSON (`contracts/dashboard.schema.json`): stat, list, checklist, table,
    chart (Swift Charts, bar or line), note and section. JSON that does not fit the schema (unknown widget, missing
    field, nested section, a chart with too few values, …) is one plain sentence, never a crash.
- **Version line**: "Updated 2 min ago by Energy contracts · v3" (VoiceOver: "…, version 3"). **History**
  (`GET /artifacts/{id}/versions`, fetched again for each new current version) opens from the "History · 3" chip;
  an older version opens read-only ("v2 of 3 · read only · on 9 Oct") with "Back to the latest".
- **Ask about this** opens a question box. The question goes to the main conversation (`GET /me` once), tagged with
  the item's project (`project_id`), and goes into the cache at once so Chat shows it. The contract's `NewMessage`
  has no field to attach an item, so the text ends with the item: "(About the item “Energy offers compared”,
  version 3, art_01JA0B1.)". A question that does not get through stays in the box.
- **Pin to project** (wireframe 4B) is not built: the app API has no way to pin an item to a project (a project has
  exactly one board, `board_artifact_id`, which the hub keeps). It needs a contract change first.

## Tests (`make -C ios items-test`, part of `make ios-check`)

- `ItemsLibraryTests` — grouping and order, the words on each tile, the "updated" badge, times and sizes in words.
- `ItemBodyTests` — each kind from the mock hub's recordings (`Tests/MarcelItemsTests/MockHub/`, recorded with curl
  from `make mock-hub`) and the contract's dashboard examples; Markdown blocks and web-only links; GitHub-only PR
  buttons; eleven dashboards that break the schema, each a plain sentence.
- `ViewerLiveTests` — the story's UI test one layer down (XCUITest cannot run on this Mac's Xcode, see
  ../../README.md): an in-memory cache fed with event frames, the viewer's model with a fake hub. An
  `artifact.updated` refreshes an open document and a board and bumps the version; the history and read-only old
  versions; "Ask about this"; failures in plain words.
- `WebAndFileTests` — the web view's configuration (JavaScript off unless trusted), the navigation rules, and a real
  `WKWebView` in the simulator: an untrusted page's `<script>` does not run, a trusted one's does, and the content
  security policy alone also stops it. QuickLook can preview the written file.
- `LiveItemsServiceTests` — the generated client with a stubbed transport: paths, `?version=`, the cursor, the
  question's body, and the hub's refusals (404, 401, 400, 500, no network, too big) in words.
- `ItemsSnapshotTests` — the tab and every kind in the viewer, in light, dark and Accessibility 5, compared pixel for
  pixel with `Tests/MarcelItemsTests/__Snapshots__/`. They render `ItemsStill` and `ViewerStill`; where the screen
  shows a web view or QuickLook (which `ImageRenderer` cannot draw), the still shows the page's words or the file's
  card. After a deliberate change run `make -C ios items-record`, look at the PNGs, and commit them.
- `ItemsAccessibilityTests` — tiles as one sentence, project names and headings as headers, the version line without
  "·", tables row by row, charts value by value, buttons at least 44 pt; default and largest text size.
- `ItemsMockHubTests` — against the mock hub (not part of the gate): `MOCK_HUB_PORT=7488 make mock-hub`, then
  `MOCK_HUB=http://127.0.0.1:7488/api make -C ios items-integration`.
