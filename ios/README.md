# ios/ — the Marcel iPhone app

Swift 6, SwiftUI, iOS 18+. Built on the Mac only (it needs Xcode), never in a cloud session.

```
ios/
  Marcel.xcodeproj      the app project (scheme `Marcel`); its sources are the Marcel/ folder
  Marcel/               the app: App/ (entry, root view with the tabs), Pairing/ (QR scan + paste), Chat/ (the
                        Chat tab, its own package: see Marcel/Chat/README.md), Quests/ (the Quests tab, its own
                        package: see Marcel/Quests/README.md), Map/ (the Map tab and project rooms, its own
                        package: see Marcel/Map/README.md), Items/ (the Items tab and the artifact viewer, its own
                        package: see Marcel/Items/README.md), Quest/ (inside one quest, opened from the Chat
                        and Quests tabs, its own package: see Marcel/Quest/README.md), Settings/ (settings, the
                        status screen, and where marcel:// links and pushes lead, its own package: see
                        Marcel/Settings/README.md), Notebook/ (Marcel's notebook: memory notes and rollovers,
                        its own package: see Marcel/Notebook/README.md), Look/ (the look picker, used by
                        onboarding and Settings, its own package: see Marcel/Look/README.md)
  Config/               the app's Info.plist additions (local-network HTTP for the mock hub)
  MarcelKit/            Swift package: API client, pairing, Keychain, WebSocket stream, SwiftData cache
  PixelKit/             Swift package: the pixel-classic design system (tokens, fonts, components; see its README)
  AvatarKit/            Swift package: each agent's sprite player (see AvatarKit/README.md)
  Tools/APIGenerator/   pins swift-openapi-generator for `make -C ios generate`
  Tools/SimTestRunner/  runs the tests inside the simulator with Keychain access
  scripts/              generate-api.sh, strip_conditionals.py, pick-simulator.py, preview-simulator.py
```

## Commands

```bash
make ios-check              # the lane gate (from the repo root) = make -C ios check
make -C ios app             # build the app for the simulator
make -C ios run             # build it and open it in the "Marcel Preview" simulator (see "Trying the app")
make -C ios run-mock        # the same, with the mock hub started and the pairing link on the clipboard
make -C ios stop            # shut down "Marcel Preview", and the mock hub if run-mock started it
make -C ios test            # MarcelKit's and AvatarKit's tests on a simulator (stubs only, no network)
make -C ios pixelkit-test   # PixelKit's snapshot, contrast and accessibility tests
make -C ios pixelkit-record # re-record PixelKit's reference snapshots after a deliberate change
make -C ios chat-test       # the Chat tab's feed, card, composer and snapshot tests
make -C ios chat-record     # re-record the Chat snapshots after a deliberate change
make -C ios quests-test     # the Quests tab's grouping, stop/archive and snapshot tests
make -C ios quests-record   # re-record the Quests snapshots after a deliberate change
make -C ios map-test        # the Map tab's tiles, rooms, moves, room messages and snapshot tests
make -C ios map-record      # re-record the Map snapshots after a deliberate change
make -C ios items-test      # the Items tab and the artifact viewer: every kind, versions, the web view's rules, snapshots
make -C ios items-record    # re-record the Items snapshots after a deliberate change
make -C ios quest-test      # the quest view: steps, transcript merge, steer and approve, 5k events, snapshots
make -C ios quest-record    # re-record the quest view's snapshots after a deliberate change
make -C ios settings-test   # settings, the status screen, every marcel:// link and push from a cold start and running
make -C ios settings-record # re-record the Settings snapshots after a deliberate change
make -C ios notebook-test   # Marcel's notebook: sections, edit/save/conflict, drafts, untrusted paths, rollovers, snapshots
make -C ios notebook-record # re-record the notebook's snapshots after a deliberate change
make -C ios look-test       # the look picker: pick from my name, choose and save, previews, snapshots
make -C ios look-record     # re-record the look picker's snapshots after a deliberate change
make -C ios integration     # one test against the mock hub; start it first with `make mock-hub`
make -C ios generate        # regenerate the API client after contracts/app-api.yaml changes
SIM=<udid> make -C ios test # pick the simulator yourself (default: an iPhone on the newest iOS runtime)
```

The Makefile uses `/Applications/Xcode.app` even when `xcode-select` points at the Command Line Tools.
Override with `DEVELOPER_DIR=…`.

## Trying the app

`make -C ios run` builds the app, starts a simulator called **Marcel Preview** (an iPhone 17 on the newest iOS
runtime, made the first time you run it), opens the Simulator app, installs Marcel and launches it. The tests use a
different simulator, so trying the app never disturbs them.

`make -C ios run-mock` does the same with a fake hub behind it. It starts the mock hub (unless it already runs) and
copies the pairing link onto the simulator's clipboard. On the pairing screen, tap the link field, paste, and tap
**Pair**. Opening a `marcel://pair` link does not pair the phone, on purpose (see Marcel/Settings/README.md).

`make -C ios stop` shuts the simulator down, and the mock hub if `run-mock` started it.

To use Xcode's own Run button (and XCUITest) instead, install the iOS 26.5 simulator in Xcode → Settings →
Components. Until then Xcode shows no simulator to run on (next section).

## How the gate runs, and why not `xcodebuild test`

On the owner's Mac, Xcode 26.6 ships the iOS 26.5 SDK but not the matching iOS platform component, so
`xcodebuild` lists **no** simulator as a destination for any scheme ("iOS 26.5 is not installed"), even
though the iOS 18 and 26.x simulators work. `xcodebuild test` and `-destination` therefore cannot run.
`make -C ios check` needs no destination:

1. **App:** `xcodebuild -target Marcel -sdk iphonesimulator` with `SWIFT_ENABLE_EXPLICIT_MODULES=NO`
   (target builds otherwise fail to order the package modules: "unable to resolve module dependency").
2. **Tests:** SwiftPM builds `MarcelKitTests`, `AvatarKitTests`, `PixelKitTests`, `MarcelChatTests`, `MarcelQuestsTests`, `MarcelMapTests`, `MarcelItemsTests`, `MarcelQuestTests`, `MarcelSettingsTests`, `MarcelNotebookTests` and `MarcelLookTests` for the simulator (`--triple arm64-apple-ios18.0-simulator`).
3. **Run:** `simctl spawn` runs the bundle in a booted simulator through `Tools/SimTestRunner`. Apple's own
   `xctest` agent has no Keychain entitlement (every Keychain call fails with -34018), and the simulator
   refuses one added to a code signature, so the runner carries it the way simulator apps do: in a
   `__TEXT,__entitlements` section. That makes the Keychain tests use the simulator's real Keychain.

**Pending the iOS 26.5 platform install — the app icon's asset catalog.** The icon is loose PNGs in
`Marcel/App/AppIcon/` listed in `Config/Marcel-Info.plist`, because actool cannot compile an asset catalog here.
Loose PNGs give the home screen only the default icon: no dark, clear or tinted icon. **Before the first
TestFlight build**, install the platform and move the icon into `Marcel/App/Assets.xcassets/AppIcon.appiconset`:
one 1024 px "Any appearance" image (`shared/brand/app-icon/out/app-icon-1024.png`), the dark one
(`app-icon-dark-1024.png`), and a tinted one (a grey-scale version for iOS to tint; add it to
`shared/brand/app-icon/generate.py`). Then set `ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon`, drop
`CFBundleIcons` from the plist, delete `Marcel/App/AppIcon/`, and point `AppIconTests` at the catalog.

**Also pending the iOS 26.5 platform install:** XCUITest's `performAccessibilityAudit()` on PixelKit's gallery (story
S-12.2). Until then `PixelKitTests/AccessibilityAuditTests` runs the same checks on SwiftUI's accessibility tree
(see PixelKit/README.md).

The shared `Marcel` scheme lists `MarcelKitTests` for Xcode's Test action. That path is untested here
(no destination), and unhosted tests there would hit the same Keychain error; `make -C ios check` is the gate.

## MarcelKit

- **API client** — `Sources/MarcelAPI` is generated by swift-openapi-generator from
  `contracts/app-api.yaml`, and checked in. Never edit it; run `make -C ios generate`. The script first
  drops the contract's `if`/`then` rules ("`url` is required when `kind` is `pr`") from a temporary copy:
  they only tighten validation, and the generator cannot read them (it turned `Message`, `TaskEvent`,
  `Artifact` and `PushTarget` into opaque blobs at contract 0.1.0). The contract itself is never changed.
- **Events** — `contracts/events.schema.json` is not read by generators, so `HubEvent` is written by hand.
  A test decodes every example in that schema; unknown event types are kept as `.unknown` so ids still advance.
- **Pairing** — the QR code holds `marcel://pair?hub=<server URL>&code=<code>`. `PairingService` posts the
  code to `POST /pair` on that hub, keeps the **token in the Keychain** (`KeychainTokenStore`, readable
  after first unlock, this device only) and the hub's address in UserDefaults. The token never goes to
  UserDefaults; a test reads both stores to prove it. In the simulator, paste the link instead of scanning.
- **Live stream** — `EventStream` keeps one `GET /ws` open. On a drop it waits (1, 2, 4, … 30 s) and
  reconnects with `since = lastEventId`, so the hub replays what was missed; events already applied are
  skipped. `resync.required` re-fetches over REST, then reconnects from the hub's last event. A refused
  token (403) unpairs the phone.
- **Cache** — SwiftData rows for messages, tasks, projects, artifacts and agents, plus the last event id, so a cold
  start resumes with `since`. Tasks and artifacts are filed by `project_id`; `task.moved` refiles a task,
  `artifact.moved` an item, and `artifact.updated` replaces the artifact with its new current version. Agents come
  from `/me` and `agent.updated` (app API 0.7) with their look (`Agent.drawnLook`; a look holding a value
  this build does not know, app API 0.7.1, is nil: draw the default giraffe); each keeps the avatar sheet ETag the last
  `agent.updated` gave (a `/me` refresh keeps it), so the app reloads a sheet only when it changed. Messages keep
  `reply_to_message_id`, and `replies(to:)` finds the replies to a question. Screens that need events the cache
  does not store (a quest's `task.event` and approval frames) listen with `observeFrames`.

## Talking to the mock hub

`make mock-hub` serves the contract on `http://127.0.0.1:7499/api`. In the simulator app, paste
`marcel://pair?hub=http%3A%2F%2F127.0.0.1%3A7499%2Fapi&code=K7Q2M9` (the mock accepts any code).
