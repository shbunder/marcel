// swift-tools-version: 6.0
// MarcelLook: the look picker (S-19.4, B-44) — an animal, a colour and a personality from the Pixel Zoo, a live preview
// from the hub, "Pick from my name", and Save. Onboarding (S-17.5) and Settings (S-17.6) show it. A package of its own
// so its tests run in the simulator like the other packages'. See README.md.
import PackageDescription

let package = Package(
    name: "MarcelLook",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "MarcelLook", targets: ["MarcelLook"])],
    dependencies: [
        .package(path: "../../MarcelKit"),
        .package(path: "../../PixelKit"),
    ],
    targets: [
        .target(
            name: "MarcelLook",
            dependencies: [
                .product(name: "MarcelKit", package: "MarcelKit"),
                .product(name: "PixelKit", package: "PixelKit"),
            ]
        ),
        .testTarget(name: "MarcelLookTests", dependencies: ["MarcelLook"], exclude: ["__Snapshots__", "Fixtures"]),
    ]
)
