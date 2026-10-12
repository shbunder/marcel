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
