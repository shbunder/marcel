// swift-tools-version: 6.0
// PixelKit: the pixel-classic design system (tokens, fonts, components). Filled by S-12.2.
import PackageDescription

let package = Package(
    name: "PixelKit",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "PixelKit", targets: ["PixelKit"])],
    targets: [.target(name: "PixelKit")]
)
