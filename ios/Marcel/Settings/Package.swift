// swift-tools-version: 6.0
// MarcelSettings: Settings (S-12.10) — the agent's name, notifications (APNs or ntfy), schedules, plan usage, devices
// and the system status screen — plus where every `marcel://` link and every push leads. A package of its own so its
// tests run in the simulator like the other packages'. It depends only on MarcelKit and PixelKit; the app shows the
// screens a link leads to (the quest view, a project room, the artifact viewer, a chat message). See README.md.
import PackageDescription

let package = Package(
    name: "MarcelSettings",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "MarcelSettings", targets: ["MarcelSettings"])],
    dependencies: [
        .package(path: "../../MarcelKit"),
        .package(path: "../../PixelKit"),
        // the look picker under "Edit avatar and name" (S-17.6).
        .package(path: "../Look"),
        // Only the tests draw an avatar; the app passes its own (SettingsAppLinks.avatar).
        .package(path: "../../AvatarKit"),
    ],
    targets: [
        .target(
            name: "MarcelSettings",
            dependencies: [
                .product(name: "MarcelKit", package: "MarcelKit"),
                .product(name: "PixelKit", package: "PixelKit"),
                .product(name: "MarcelLook", package: "Look"),
            ]
        ),
        .testTarget(name: "MarcelSettingsTests",
                    dependencies: ["MarcelSettings", .product(name: "AvatarKit", package: "AvatarKit")],
                    exclude: ["__Snapshots__", "Fixtures"]),
    ]
)
