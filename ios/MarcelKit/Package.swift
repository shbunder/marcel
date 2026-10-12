// swift-tools-version: 6.0
// MarcelKit: the app's link to the hub — the generated API client, pairing, the event stream and the
// local cache. `MarcelAPI` is generated from contracts/app-api.yaml (`make -C ios generate`); never edit it.
import PackageDescription

let package = Package(
    name: "MarcelKit",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [
        .library(name: "MarcelKit", targets: ["MarcelKit"])
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-runtime", from: "1.8.2"),
        .package(url: "https://github.com/apple/swift-openapi-urlsession", from: "1.1.0"),
        .package(url: "https://github.com/apple/swift-http-types", from: "1.0.2"),
    ],
    targets: [
        .target(
            name: "MarcelAPI",
            dependencies: [
                .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
                .product(name: "HTTPTypes", package: "swift-http-types"),
            ]
        ),
        .target(
            name: "MarcelKit",
            dependencies: [
                "MarcelAPI",
                .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
                .product(name: "OpenAPIURLSession", package: "swift-openapi-urlsession"),
                .product(name: "HTTPTypes", package: "swift-http-types"),
            ]
        ),
        .testTarget(name: "MarcelKitTests", dependencies: ["MarcelKit"]),
    ]
)
