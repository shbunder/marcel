# ios/ — the Marcel iPhone app

Swift 6, SwiftUI, iOS 18+, with local packages `MarcelKit` (API, WebSocket, cache) and `AvatarKit`
(the procedural RealityKit giraffe). The Xcode project is created in story S-12.1 **on the Mac**:
it needs Xcode, so it is never built in a cloud session. `make ios-check` runs `xcodebuild test`.
