// swift-tools-version: 6.0
// MarcelNotebook: Marcel's notebook (S-12.9) — the memory files (`MEMORY.md` "about you", project notes, dated
// notes) to read and edit, with a preview, saves that never overwrite a change made elsewhere, drafts that survive
// leaving the screen, and "what Marcel carries" from the brain's rollovers. A package of its own so its tests run in
// the simulator like the other packages'. Its preview is PixelKit's shared `MarkdownView`, as in the item viewer.
// See README.md.
import PackageDescription

let package = Package(
    name: "MarcelNotebook",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "MarcelNotebook", targets: ["MarcelNotebook"])],
    dependencies: [
        .package(path: "../../MarcelKit"),
        .package(path: "../../PixelKit"),
    ],
    targets: [
        .target(
            name: "MarcelNotebook",
            dependencies: [
                .product(name: "MarcelKit", package: "MarcelKit"),
                .product(name: "PixelKit", package: "PixelKit"),
            ]
        ),
        .testTarget(name: "MarcelNotebookTests", dependencies: ["MarcelNotebook"], exclude: ["__Snapshots__"]),
    ]
)
