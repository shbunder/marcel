// swift-tools-version: 6.0
// MarcelWelcome: getting into the app (S-17.5, B-39) — the welcome screen with the pairing scanner, onboarding ("Meet
// your helper": the look picker and the name), the Face ID lock, and signing this phone out. A package of its own so
// its tests run in the simulator (the app target has none; project/lessons/ios-screen-packages.md). See README.md.
import PackageDescription

let package = Package(
    name: "MarcelWelcome",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "MarcelWelcome", targets: ["MarcelWelcome"])],
    dependencies: [
        .package(path: "../../MarcelKit"),
        .package(path: "../../PixelKit"),
        .package(path: "../../AvatarKit"),
        .package(path: "../Look"),
    ],
    targets: [
        .target(
            name: "MarcelWelcome",
            dependencies: [
                .product(name: "MarcelKit", package: "MarcelKit"),
                .product(name: "PixelKit", package: "PixelKit"),
                .product(name: "AvatarKit", package: "AvatarKit"),
                .product(name: "MarcelLook", package: "Look"),
            ]
        ),
        .testTarget(name: "MarcelWelcomeTests", dependencies: ["MarcelWelcome"], exclude: ["__Snapshots__"]),
    ]
)
