// swift-tools-version: 6.0
// MarcelItems: the Items tab and the artifact viewer (S-12.8) — every item by project, newest change first, and one
// viewer per kind (Markdown, HTML, files, pull requests, dashboards and boards), with its versions. A package of its
// own so its tests run in the simulator like the other packages'; the app links it and its tab bar shows `ItemsView`.
// The Chat and Map tabs open `ArtifactViewer` from their cards. See README.md.
import PackageDescription

let package = Package(
    name: "MarcelItems",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "MarcelItems", targets: ["MarcelItems"])],
    dependencies: [
        .package(path: "../../MarcelKit"),
        .package(path: "../../PixelKit"),
    ],
    targets: [
        .target(
            name: "MarcelItems",
            dependencies: [
                .product(name: "MarcelKit", package: "MarcelKit"),
                .product(name: "PixelKit", package: "PixelKit"),
            ]
        ),
        .testTarget(name: "MarcelItemsTests", dependencies: ["MarcelItems"], exclude: ["__Snapshots__", "MockHub"]),
    ]
)
