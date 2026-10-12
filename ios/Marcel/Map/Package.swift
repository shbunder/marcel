// swift-tools-version: 6.0
// MarcelMap: the Map tab (S-12.7) — one tile per project, and each project's room: its pinned board, its quests by
// state, its items and notes, and a composer that talks to Marcel about the project. A package of its own so its
// tests run in the simulator like the other packages'; the app links it and its tab bar shows `MapView`. See README.md.
import PackageDescription

let package = Package(
    name: "MarcelMap",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "MarcelMap", targets: ["MarcelMap"])],
    dependencies: [
        .package(path: "../../MarcelKit"),
        .package(path: "../../PixelKit"),
        // The room reuses the chat's composer and board preview, and the quest log's grouping and cards.
        .package(path: "../Chat"),
        .package(path: "../Quests"),
    ],
    targets: [
        .target(
            name: "MarcelMap",
            dependencies: [
                .product(name: "MarcelKit", package: "MarcelKit"),
                .product(name: "PixelKit", package: "PixelKit"),
                .product(name: "MarcelChat", package: "Chat"),
                .product(name: "MarcelQuests", package: "Quests"),
            ]
        ),
        .testTarget(name: "MarcelMapTests", dependencies: ["MarcelMap"], exclude: ["__Snapshots__"]),
    ]
)
