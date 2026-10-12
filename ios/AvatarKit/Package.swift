// swift-tools-version: 6.0
// AvatarKit: plays Marcel's pixel giraffe sprite sheet (B-28). The sheet in Sources/AvatarKit/Resources is a
// copy of shared/avatar/pixel/out/giraffe.{png,json}; refresh it with `make -C ios/AvatarKit sheet`.
import PackageDescription

let package = Package(
    name: "AvatarKit",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "AvatarKit", targets: ["AvatarKit"])],
    targets: [
        .target(
            name: "AvatarKit",
            resources: [.copy("Resources/giraffe.png"), .copy("Resources/giraffe.json")]
        ),
        .testTarget(name: "AvatarKitTests", dependencies: ["AvatarKit"], exclude: ["__Snapshots__"]),
    ]
)
