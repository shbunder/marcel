// swift-tools-version: 6.0
// AvatarKit: plays Marcel's pixel giraffe sprite sheet. Filled by S-13.2.
import PackageDescription

let package = Package(
    name: "AvatarKit",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "AvatarKit", targets: ["AvatarKit"])],
    targets: [.target(name: "AvatarKit")]
)
