// swift-tools-version: 6.0
// PixelKit: the pixel-classic design system — colour tokens (light and dark), the 2-pt pixel grid, the two bundled
// OFL pixel fonts, and the components the app is drawn with. No dependencies; the snapshot tests compare PNGs
// themselves so the app's package graph stays empty. See README.md.
import PackageDescription

let package = Package(
    name: "PixelKit",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "PixelKit", targets: ["PixelKit"])],
    targets: [
        .target(name: "PixelKit", resources: [.copy("Resources/Fonts")]),
        .testTarget(name: "PixelKitTests", dependencies: ["PixelKit"], exclude: ["__Snapshots__"]),
    ]
)
