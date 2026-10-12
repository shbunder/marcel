# iOS: a tab as its own package, and snapshots of a whole screen

- **Testing app screens.** `xcodebuild test` cannot run on the owner's Mac (no iOS platform component), and the
  app target has no test bundle. S-12.3 made the Chat tab a SwiftPM package inside the app folder
  (`ios/Marcel/Chat/Package.swift`, product `MarcelChat`) that the Xcode project links as a local package, and
  built its tests with SwiftPM like PixelKit's (`make -C ios chat-test`). The app folder is a *synchronised* group,
  so it would compile `Package.swift` and the tests into the app: `project.pbxproj` has a
  `PBXFileSystemSynchronizedBuildFileExceptionSet` that excludes the `Chat` folder. Another tab can follow the same
  pattern (add its folder to that exception list).
- **`ImageRenderer` draws a `TextField` as a yellow "not supported" box** and a `ScrollView`'s content not at
  all. The chat's snapshots render `ChatStill` (the rows in a plain stack) and, under the `chatStillAvatars`
  environment flag, draw the composer's text as `PixelText` and the avatar as one still frame.
- `PixelGlyph.pixel(for:)` and PixelKit's `pixelRendersOffscreen` are internal; tests reach the latter with
  `@testable import PixelKit` (SwiftPM debug builds allow it).
