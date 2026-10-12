// swift-tools-version: 6.0
// Pins the OpenAPI generator used by `make -C ios generate`. Not part of the app build.
import PackageDescription

let package = Package(
    name: "APIGenerator",
    platforms: [.macOS(.v15)],
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-generator", exact: "1.13.1")
    ]
)
