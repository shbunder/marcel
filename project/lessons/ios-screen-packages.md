# iOS: a tab as its own package, and snapshots of a whole screen

- **Testing app screens.** `xcodebuild test` cannot run on the owner's Mac (no iOS platform component), and the
  app target has no test bundle. S-12.3 made the Chat tab a SwiftPM package inside the app folder
  (`ios/Marcel/Chat/Package.swift`, product `MarcelChat`) that the Xcode project links as a local package, and
  built its tests with SwiftPM like PixelKit's (`make -C ios chat-test`). The app folder is a *synchronised* group,
  so it would compile `Package.swift` and the tests into the app: `project.pbxproj` has a
  `PBXFileSystemSynchronizedBuildFileExceptionSet` that excludes the `Chat` folder. Another tab can follow the same
  pattern (add its folder to that exception list).
- **Xcode already skips a folder that holds a `Package.swift`** (S-12.5 found this): nothing in it, not even a loose
  `.swift` file beside the manifest, is compiled into the app target, exception or not. So a screen package cannot
  also hold an app-side wrapper; make the package's view public (`MarcelQuests.QuestsView`) and import the package
  where the tab bar routes to it. Quests links `Marcel/Quests` as a local package with no exception entry. Check what
  the app target really compiles in `ios/.build/xcode/**/Marcel.build/**/*.SwiftFileList`.
- **`ImageRenderer` draws a `TextField` as a yellow "not supported" box** and a `ScrollView`'s content not at
  all. The chat's snapshots render `ChatStill` (the rows in a plain stack) and, under the `chatStillAvatars`
  environment flag, draw the composer's text as `PixelText` and the avatar as one still frame.
- `PixelGlyph.pixel(for:)` and PixelKit's `pixelRendersOffscreen` are internal; tests reach the latter with
  `@testable import PixelKit` (SwiftPM debug builds allow it).
- **A screen package can depend on another** (S-12.7): `MarcelMap` lists `../Chat` and `../Quests` in its
  `Package.swift` to reuse the composer, the board preview and the quest grouping. Make only what it needs `public`,
  and add the new package to `project.pbxproj` the way Quests is (a local package reference and a product dependency;
  no exception entry).
- **Several `Grid`s in one stack under `ImageRenderer`** (S-12.8): tiles with `.frame(maxHeight: .infinity)` made
  the stack hand the grids the wrong heights (rows overlapping, the last grid stretched). Give each grid
  `.fixedSize(horizontal: false, vertical: true)`: rows still stretch to their tallest tile, and no taller.
- **A real `WKWebView` works in the SimTestRunner** (S-12.8): `loadHTMLString` plus `evaluateJavaScript` in an XCTest
  shows whether a page's own `<script>` ran, so a web view's rules can be tested on behaviour, not just on its
  configuration. Swift Charts also draws under `ImageRenderer`; a web view and QuickLook do not.
- **The item viewer lives in MarcelItems, which depends only on MarcelKit and PixelKit**, so Chat and Map can depend on
  it and open `ArtifactViewer(artifactId:)` (no cycle). Keep it that way: Items must not import Chat or Map.
- **Live-only events reach a screen through the cache** (S-12.6): the cache drops `task.event` and approval frames,
  and every screen makes its own `MarcelCache(container:)`, so a screen cannot hook the stream itself. The app's one
  cache (`connection.cache`) is put in the environment as `liveEvents` (MarcelQuest) and the screen calls
  `observeFrames` on it. A test does the same with an in-memory cache and `cache.apply(frame)`.
- **Measure a lazy list in a real window** (S-12.6): a `UIHostingController` in a `UIWindow` in the SimTestRunner lays
  out a `ScrollView { LazyVStack }` of 5,000 rows in well under a second, so a performance test can include layout,
  not just the model.
- **App wiring that must be tested goes in a package** (S-12.10): the app target has no test bundle, so the link
  router, the push handler and the push categories live in MarcelSettings, and `AppDelegate` / `RootView` only create
  and connect them. "Cold start" is tested there: deliver the link before `attach`, then attach as the tabs do.
- **A hub route that is not built yet answers 404 with the generic code `not_found`** (`hub/…/auth/errors.py`; 405
  `method_not_allowed` when the path exists for another method). For an operation whose contract lists a 404 that
  body **decodes** as the contract's `Error`, so `.notFound` alone cannot tell "gone" from "not built": check the code
  (`message_not_found`, `device_not_found`, … mean gone; `not_found` means not built — S-12.11 review,
  `SettingsError.missing`). A body that does not decode (FastAPI's bare `{"detail": "Not Found"}`) throws a
  `ClientError` whose `response` holds the status; `LiveSettingsService.call` reads that as "not available yet". Test
  fakes should answer unknown routes with the hub's real body, not the bare one.
- **Silkscreen (the label face) has capitals only**: anything case-sensitive (an ntfy topic, an id) goes in the body
  face (`PixelText(role: .body)`), or it reads wrong.
- **Notification actions can run with no screen** (S-12.10 review): iOS starts the app in the background for a
  lock-screen action, so anything the action needs (here the hub address and token) must be built at launch from the
  stores, never from a view's `.task`.
- **The generated client URI-encodes header values and path parameters** (S-12.9): a path parameter goes through
  `renderedPath`, which percent-encodes everything but `A-Z a-z 0-9 - . _ ~`, so `memory/x.md` arrives as one segment
  `memory%2Fx.md` — pass the raw value, never a pre-encoded one (it would become `%252F`). A header such as `If-Match`
  is encoded the same way, so a quoted etag would arrive as `%22…%22`: send it bare (the memory routes accept that).
- **No asset catalogs on the owner's Mac** (S-13.3): actool fails on any `.xcassets`, for the simulator and for a
  device alike ("No simulator runtime version … available to use with iphonesimulator SDK version 23F81a"), because
  the iOS 26.5 platform component is missing — and that fails `make app`. The app icon is therefore loose PNGs in
  `Marcel/App/AppIcon/` listed under `CFBundleIcons` in `Config/Marcel-Info.plist`; iOS shows them on the home screen
  (checked on a 26.4 simulator). Move them into an asset catalog once the platform is installed.
- **An `Animatable` SwiftUI view under Swift 6** needs `nonisolated var animatableData` (the `View` is main-actor
  isolated; the conformance is not). `AvatarHeader` animates its collapse this way, so each in-between value goes
  through `body` and the sprite can snap to whole zooms instead of being scaled.
