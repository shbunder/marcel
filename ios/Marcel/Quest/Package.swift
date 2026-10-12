// swift-tools-version: 6.0
// MarcelQuest: the quest view (S-12.6) — inside one quest: its steps, its live transcript folded away, inline approval
// prompts, a composer that talks to that quest only, and stop / hand back / move / open in Claude. A package of its
// own so its tests run in the simulator like the other packages'. It depends only on MarcelKit and PixelKit, so the
// Chat, Quests and Map tabs can all open `QuestView(taskId:)`. See README.md.
import PackageDescription

let package = Package(
    name: "MarcelQuest",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "MarcelQuest", targets: ["MarcelQuest"])],
    dependencies: [
        .package(path: "../../MarcelKit"),
        .package(path: "../../PixelKit"),
    ],
    targets: [
        .target(
            name: "MarcelQuest",
            dependencies: [
                .product(name: "MarcelKit", package: "MarcelKit"),
                .product(name: "PixelKit", package: "PixelKit"),
            ]
        ),
        .testTarget(name: "MarcelQuestTests", dependencies: ["MarcelQuest"], exclude: ["__Snapshots__"]),
    ]
)
