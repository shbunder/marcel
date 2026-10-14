// swift-tools-version: 6.0
// MarcelChat: the Chat tab (S-12.3) — the one conversation with Marcel, its cards and the composer. A package of its
// own so its snapshot and scenario tests run in the simulator like the other packages'; the app links it and shows
// `ChatScreen`. The Xcode project keeps this folder out of the app target's own sources. See README.md.
import PackageDescription

let package = Package(
    name: "MarcelChat",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "MarcelChat", targets: ["MarcelChat"])],
    dependencies: [
        .package(path: "../../MarcelKit"),
        .package(path: "../../PixelKit"),
        .package(path: "../../AvatarKit"),
        // A tapped item card opens the artifact viewer.
        .package(path: "../Items"),
        // A tapped quest card opens the quest view.
        .package(path: "../Quest"),
        // Your bubbles take the look's colour (LookColors); the mock-hub test saves a look through the picker.
        .package(path: "../Look"),
    ],
    targets: [
        .target(
            name: "MarcelChat",
            dependencies: [
                .product(name: "MarcelKit", package: "MarcelKit"),
                .product(name: "PixelKit", package: "PixelKit"),
                .product(name: "AvatarKit", package: "AvatarKit"),
                .product(name: "MarcelItems", package: "Items"),
                .product(name: "MarcelQuest", package: "Quest"),
                .product(name: "MarcelLook", package: "Look"),
            ]
        ),
        .testTarget(name: "MarcelChatTests", dependencies: ["MarcelChat"], exclude: ["__Snapshots__"]),
    ]
)
