import ImageIO
import PixelKit
import SwiftUI
import UniformTypeIdentifiers
import XCTest

@testable import MarcelWelcome

/// Design renders (for review, not checked): the start screen on the logo's bow tiling, at a phone's full size, light and
/// dark, into `ios/.build/bow-tiling/`.
@MainActor
final class BowTilingStartTests: XCTestCase {
    func testRenderTheStartScreen() throws {
        let out = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent().appending(path: ".build/bow-tiling")
        try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        for scheme in [ColorScheme.light, .dark] {
            let view = WelcomeView { _ in }
                .environment(\.welcomeStill, true)
                .environment(\.colorScheme, scheme)
                .frame(width: 390, height: 844)
            let renderer = ImageRenderer(content: view)
            renderer.scale = 2
            let image = try XCTUnwrap(renderer.cgImage)
            let url = out.appending(path: "start-\(scheme == .dark ? "dark" : "light").png")
            let dest = try XCTUnwrap(CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil))
            CGImageDestinationAddImage(dest, image, nil)
            XCTAssertTrue(CGImageDestinationFinalize(dest))
        }
    }
}
