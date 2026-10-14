// swift-tools-version: 6.0
// AvatarKit: plays each agent's pixel avatar sprite sheet (B-28, B-44), Marcel's own by default. The bundled
// sheet in Sources/AvatarKit/Resources is Marcel's look drawn by the Pixel Zoo (shared/avatar/zoo); refresh it with
// `make -C ios/AvatarKit sheet`.
import PackageDescription

let package = Package(
    name: "AvatarKit",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "AvatarKit", targets: ["AvatarKit"])],
    targets: [
        .target(
            name: "AvatarKit",
            resources: [
                .copy("Resources/giraffe-rose-professional.png"), .copy("Resources/giraffe-rose-professional.json"),
            ]
        ),
        .testTarget(name: "AvatarKitTests", dependencies: ["AvatarKit"], exclude: ["__Snapshots__", "Fixtures"]),
    ]
)
