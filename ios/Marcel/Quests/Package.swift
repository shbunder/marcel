// swift-tools-version: 6.0
// MarcelQuests: the Quests tab (S-12.5) — the quest log, one card per thread grouped by what it needs from you. A
// package of its own so its tests run in the simulator like the other packages'; the app links it and its tab bar
// shows `QuestsView()`. See README.md.
import PackageDescription

let package = Package(
    name: "MarcelQuests",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "MarcelQuests", targets: ["MarcelQuests"])],
    dependencies: [
        .package(path: "../../MarcelKit"),
        .package(path: "../../PixelKit"),
    ],
    targets: [
        .target(
            name: "MarcelQuests",
            dependencies: [
                .product(name: "MarcelKit", package: "MarcelKit"),
                .product(name: "PixelKit", package: "PixelKit"),
            ]
        ),
        .testTarget(name: "MarcelQuestsTests", dependencies: ["MarcelQuests"], exclude: ["__Snapshots__"]),
    ]
)
